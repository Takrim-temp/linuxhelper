#!/usr/bin/env python3
"""
AI Linux Agent - Final Build
- Answers questions, executes tasks, gives final answers
- Works on Debian/Ubuntu/Kali, RHEL/Fedora, Arch, SUSE, Alpine
- Auto-detects distro and picks the right package manager
- Opens real GUI terminal windows or tmux sessions
- Auto-redirects server commands to prevent hangs
- Validates heredocs before execution
- Handles API rate limits (429) with backoff
"""

import os
import sys
import subprocess
import json
import re
import time
import shlex
import shutil
import argparse
import platform
from datetime import datetime
from typing import Dict, List, Optional, Any

try:
    from openai import OpenAI
except ImportError:
    print("Error: openai not installed. Run: pip install openai")
    sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


# ============================================================
# Constants
# ============================================================
DANGEROUS_PATTERNS = [
    r'rm\s+-rf\s+/(?!\S*\.\.)', r'rm\s+-rf\s+/\*', r'\bdd\s+if=',
    r'\bmkfs', r'>\s*/dev/sd', r'\bformat\b', r'\bfdisk',
    r'chmod\s+777\s+/\s*$', r'chown\s+-R\s+root:root\s+/\s*$',
    r'kill\s+-9\s+1\s*$', r':\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:',
]

REFUSAL_MARKERS = [
    "i can't", "i cannot", "i won't", "i will not",
    "i'm unable", "i am unable", "cannot assist",
    "can't help with", "i'm not able", "against my",
    "not able to help", "i must decline", "i refuse",
    "sorry, but i can", "sorry, i can",
]

SERVER_PATTERNS = [
    r'http\.server', r'\bnc\s+-l', r'\bncat\s+-l',
    r'\bsocat\b.*listen', r'\bflask\s+run', r'\buvicorn\b',
    r'\bgunicorn\b', r'\bdjango\s+runserver',
    r'\btail\s+-f\b', r'\bwatch\b',
    r'^top\s*$', r'^htop\s*$',
    r'\bping\s+(?!-c)', r'\bserve\b', r'\bserver\b',
]


def detect_distro() -> Dict[str, str]:
    info = {
        'id': 'unknown', 'id_like': '',
        'name': platform.system(),
        'pkg_install': 'apt install -y',
        'pkg_update': 'apt update',
        'pkg_search': 'apt search',
    }
    try:
        with open('/etc/os-release', 'r') as f:
            for line in f:
                if '=' not in line:
                    continue
                k, v = line.strip().split('=', 1)
                v = v.strip('"\'')
                if k == 'ID':
                    info['id'] = v.lower()
                elif k == 'ID_LIKE':
                    info['id_like'] = v.lower()
                elif k == 'PRETTY_NAME':
                    info['name'] = v
    except Exception:
        pass

    fam = info['id'] + ' ' + info['id_like']

    if any(x in fam for x in ['debian', 'ubuntu', 'kali', 'mint', 'pop']):
        info.update({'family': 'debian',
                     'pkg_install': 'apt install -y',
                     'pkg_update': 'apt update',
                     'pkg_search': 'apt search'})
    elif any(x in fam for x in ['rhel', 'fedora', 'centos', 'rocky', 'alma', 'redhat']):
        pkg = 'dnf' if shutil.which('dnf') else 'yum'
        info.update({'family': 'rhel',
                     'pkg_install': f'{pkg} install -y',
                     'pkg_update': f'{pkg} check-update',
                     'pkg_search': f'{pkg} search'})
    elif any(x in fam for x in ['arch', 'manjaro', 'endeavour']):
        info.update({'family': 'arch',
                     'pkg_install': 'pacman -S --noconfirm',
                     'pkg_update': 'pacman -Sy',
                     'pkg_search': 'pacman -Ss'})
    elif any(x in fam for x in ['suse', 'opensuse', 'sles']):
        info.update({'family': 'suse',
                     'pkg_install': 'zypper install -y',
                     'pkg_update': 'zypper refresh',
                     'pkg_search': 'zypper search'})
    elif 'alpine' in fam:
        info.update({'family': 'alpine',
                     'pkg_install': 'apk add',
                     'pkg_update': 'apk update',
                     'pkg_search': 'apk search'})
    else:
        info['family'] = 'unknown'

    return info


def parse_timeout(s: str) -> int:
    s = s.strip().lower()
    try:
        if s.endswith('s'):
            return int(s[:-1])
        if s.endswith('m'):
            return int(s[:-1]) * 60
        if s.endswith('h'):
            return int(s[:-1]) * 3600
        return int(s)
    except ValueError:
        return 60


# ============================================================
# Agent
# ============================================================
class AIAgent:
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite"):
        self.api_key = api_key
        self.model = model
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        self.working_dir = os.getcwd()
        self.timeout = 60
        self.auto_approve = False
        self.debug = False
        self.max_iter = 30
        self.terminal_pref = "auto"
        self.history: List[Dict[str, Any]] = []
        self.background: List[Dict[str, Any]] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.distro = detect_distro()

        self.terminals = {
            'xfce4-terminal': shutil.which('xfce4-terminal'),
            'gnome-terminal': shutil.which('gnome-terminal'),
            'konsole': shutil.which('konsole'),
            'xterm': shutil.which('xterm'),
            'tmux': shutil.which('tmux'),
        }
        self.tmux_available = bool(self.terminals['tmux'])
        self.gui_available = any(
            self.terminals[k] for k in
            ['xfce4-terminal', 'gnome-terminal', 'konsole', 'xterm']
        )

    # -------------------- logging --------------------
    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [{level}] {msg}")

    def dbg(self, msg: str):
        if self.debug:
            self.log(msg, "DEBUG")

    # -------------------- safety --------------------
    def is_dangerous(self, cmd: str) -> bool:
        for p in DANGEROUS_PATTERNS:
            if re.search(p, cmd, re.IGNORECASE):
                return True
        return False

    def _looks_like_server(self, cmd: str) -> bool:
        for p in SERVER_PATTERNS:
            if re.search(p, cmd, re.IGNORECASE):
                return True
        return False

    # --- FIX 1: heredoc validation ---
    def _heredoc_incomplete(self, cmd: str) -> bool:
        """Return True if cmd starts a heredoc but doesn't contain body + terminator."""
        # Not a heredoc at all
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", cmd)
        if not m:
            return False
        terminator = m.group(1)
        # Everything after the heredoc operator line should contain the terminator
        after = cmd[m.end():]
        # Terminator must appear on its own line
        for line in after.split('\n'):
            if line.strip() == terminator:
                return False
        return True

    # -------------------- API --------------------
    def test_api(self) -> bool:
        self.log(f"Testing API ({self.model})...")
        for m in [self.model, "gemini-1.5-flash", "gemini-pro", "gemini-1.5-pro"]:
            try:
                self.client.chat.completions.create(
                    model=m,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5, temperature=0
                )
                if m != self.model:
                    self.log(f"Using fallback model: {m}", "WARN")
                    self.model = m
                self.log("✅ API OK", "SUCCESS")
                return True
            except Exception as e:
                self.dbg(f"model {m} failed: {str(e)[:120]}")
        self.log("❌ All models failed. Check API key.", "ERROR")
        return False

    # --- FIX 3: 429 backoff ---
    def chat(self, system: str, user: str, max_tokens: int = 1000,
             temperature: float = 0.3) -> Optional[str]:
        max_retries = 4
        delay = 5
        for attempt in range(max_retries):
            try:
                r = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return r.choices[0].message.content
            except Exception as e:
                msg = str(e)
                if '429' in msg or 'quota' in msg.lower() or 'rate' in msg.lower():
                    self.log(f"Rate limited (429). Sleeping {delay}s "
                             f"before retry {attempt+1}/{max_retries}...", "WARN")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                self.log(f"Chat error: {msg[:200]}", "ERROR")
                return None
        self.log("❌ Gave up after retries (still rate limited)", "ERROR")
        return None

    # -------------------- classification --------------------
    def classify(self, user_input: str) -> str:
        prompt = """Classify the user's intent into EXACTLY ONE word:

QUESTION  - only wants information/explanation, no system access
ACTION    - wants you to DO something on the system
HYBRID    - question that needs system check (e.g., "what's my IP?")

Examples:
"what is nginx"              -> QUESTION
"install nginx"              -> ACTION
"what's my IP address"       -> HYBRID
"how do I list files"        -> QUESTION
"list all files in /tmp"     -> ACTION
"is nginx running"           -> HYBRID

Reply with ONE word only."""
        r = self.chat(prompt, user_input, max_tokens=10, temperature=0)
        if not r:
            return "ACTION"
        r = r.strip().upper()
        for c in ("HYBRID", "QUESTION", "ACTION"):
            if c in r:
                return c
        return "ACTION"

    def answer_question(self, q: str) -> str:
        sys_prompt = ("You are a knowledgeable Linux assistant. "
                      "Answer clearly and concisely with practical examples.")
        r = self.chat(sys_prompt, q, max_tokens=1200)
        return r or "Could not generate answer."

    # -------------------- entry helpers --------------------
    def _entry(self, cmd, ok, out, err, dur, kind) -> Dict[str, Any]:
        return {
            'command': cmd, 'success': ok, 'output': out, 'error': err,
            'returncode': 0 if ok else 1, 'duration': dur,
            'type': kind, 'timestamp': datetime.now().isoformat()
        }

    def _err(self, cmd, err, kind) -> Dict[str, Any]:
        return {
            'command': cmd, 'success': False, 'output': '', 'error': err,
            'returncode': -1, 'duration': 0, 'type': kind,
            'timestamp': datetime.now().isoformat()
        }

    def _preview(self, entry):
        if entry['output']:
            p = entry['output'][:300]
            if len(entry['output']) > 300:
                p += '...'
            self.log(f"  → {p}", "OUT")
        if entry['error'] and not entry['success']:
            p = entry['error'][:200]
            if len(entry['error']) > 200:
                p += '...'
            self.log(f"  ⚠ {p}", "ERR")

    # -------------------- command execution --------------------
    def run_blocking(self, cmd: str) -> Dict[str, Any]:
        self.log(f"COMMAND: {cmd}", "EXEC")

        if self.is_dangerous(cmd):
            self.log("BLOCKED: dangerous command", "ERROR")
            entry = self._err(cmd, "Blocked: dangerous", "command")
            self.history.append(entry)
            return entry

        # FIX 1: refuse incomplete heredocs so the AI gets clear feedback
        if self._heredoc_incomplete(cmd):
            self.log("INCOMPLETE HEREDOC — needs body and terminator in one command", "ERROR")
            entry = self._err(cmd,
                              "Incomplete heredoc: the terminator line is missing. "
                              "Re-send the ENTIRE heredoc (header, body, terminator) "
                              "as a single COMMAND block.",
                              "command")
            self.history.append(entry)
            return entry

        try:
            start = time.time()
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.working_dir
            )
            dur = time.time() - start
            entry = self._entry(cmd, r.returncode == 0, r.stdout, r.stderr, dur, "command")
            self.history.append(entry)
            self._preview(entry)
            return entry
        except subprocess.TimeoutExpired:
            self.log(f"TIMEOUT after {self.timeout}s — killing leftover processes", "ERROR")
            try:
                subprocess.run(f"pkill -f {shlex.quote(cmd)}",
                               shell=True, capture_output=True, timeout=5)
            except Exception:
                pass
            entry = self._err(cmd, f"Timeout after {self.timeout}s", "command")
            self.history.append(entry)
            return entry
        except Exception as e:
            self.log(f"Execution error: {e}", "ERROR")
            entry = self._err(cmd, str(e), "command")
            self.history.append(entry)
            return entry

    def run_persistent(self, cmd: str) -> Dict[str, Any]:
        self.log(f"PERSISTENT: {cmd}", "EXEC")
        if self.is_dangerous(cmd):
            entry = self._err(cmd, "Blocked: dangerous", "persistent")
            self.history.append(entry)
            return entry

        log_dir = os.path.join(self.working_dir, ".agent_logs")
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"bg_{ts}.log")
        pid_file = os.path.join(log_dir, f"bg_{ts}.pid")

        wrapped = (
            f"setsid nohup bash -c {shlex.quote(cmd)} "
            f"> {shlex.quote(log_file)} 2>&1 < /dev/null & "
            f"echo $! > {shlex.quote(pid_file)}; "
            f"sleep 1; cat {shlex.quote(pid_file)}"
        )
        try:
            r = subprocess.run(wrapped, shell=True, capture_output=True,
                               text=True, timeout=10, cwd=self.working_dir)
            pid = r.stdout.strip().split('\n')[-1]
            time.sleep(1)
            check = subprocess.run(f"ps -p {pid} -o pid=,cmd=", shell=True,
                                   capture_output=True, text=True)
            alive = check.returncode == 0 and pid in check.stdout

            log_tail = ""
            try:
                with open(log_file, 'r') as f:
                    log_tail = f.read()[:500]
            except Exception:
                pass

            out = (f"Background process started\n"
                   f"PID: {pid}\n"
                   f"Log: {log_file}\n"
                   f"Status: {'RUNNING' if alive else 'DIED'}\n"
                   f"Stop with: kill {pid}")
            if log_tail:
                out += f"\n\nInitial log:\n{log_tail}"

            entry = self._entry(cmd, alive, out,
                                "" if alive else f"Process died. Log: {log_tail[:200]}",
                                0, "persistent")
            entry.update({'pid': pid, 'log_file': log_file, 'pid_file': pid_file})
            self.history.append(entry)
            if alive:
                self.background.append({
                    'command': cmd, 'pid': pid, 'log_file': log_file,
                    'pid_file': pid_file, 'started': datetime.now().isoformat()
                })
                self.log(f"✅ Running (PID {pid}) — log: {log_file}", "SUCCESS")
            else:
                self.log(f"❌ Process died — {log_tail[:200]}", "ERROR")
            return entry
        except Exception as e:
            self.log(f"Launch failed: {e}", "ERROR")
            entry = self._err(cmd, str(e), "persistent")
            self.history.append(entry)
            return entry

    # ============================================================
    # TERMINAL launching
    # ============================================================
    def _strip_terminal_wrapper(self, cmd: str) -> str:
        """Strip any terminal-launcher wrapper the AI accidentally added."""
        cmd = cmd.strip()

        # tmux new-session ... 'CMD'
        m = re.match(r"^tmux\s+new-session\s+(?:-[a-zA-Z]+\s+\S+\s+)*['\"]?(.+?)['\"]?$", cmd)
        if m:
            return m.group(1).strip().strip("'\"")

        # FIX 2: broaden to catch -e, --command=, --command , --execute, -x
        m = re.match(
            r"^(?:xfce4-terminal|gnome-terminal|konsole|xterm|mate-terminal|tilix)"
            r"\s+.*?(?:-e|--command[= ]|--execute[= ]|-x)\s*['\"]?(.+?)['\"]?$",
            cmd
        )
        if m:
            return m.group(1).strip().strip("'\"")

        return cmd

    def run_terminal(self, cmd: str) -> Dict[str, Any]:
        cmd = self._strip_terminal_wrapper(cmd)

        self.log(f"TERMINAL: {cmd}", "EXEC")
        if self.is_dangerous(cmd):
            entry = self._err(cmd, "Blocked: dangerous", "terminal")
            self.history.append(entry)
            return entry

        session_name = f"agent_{datetime.now().strftime('%H%M%S')}"

        if self.terminal_pref == "tmux":
            order = ["tmux"]
        elif self.terminal_pref == "gui":
            order = ["xfce4-terminal", "gnome-terminal", "konsole", "xterm", "tmux"]
        else:
            order = ["xfce4-terminal", "gnome-terminal", "konsole", "xterm", "tmux"]

        for name in order:
            if name == "tmux" and self.terminals['tmux']:
                return self._tmux(cmd, session_name)
            if name == "xfce4-terminal" and self.terminals['xfce4-terminal']:
                return self._xfce(cmd, session_name)
            if name == "gnome-terminal" and self.terminals['gnome-terminal']:
                return self._gnome(cmd, session_name)
            if name == "konsole" and self.terminals['konsole']:
                return self._konsole(cmd, session_name)
            if name == "xterm" and self.terminals['xterm']:
                return self._xterm(cmd, session_name)

        self.log("No terminal emulator found. Falling back to PERSISTENT.", "WARN")
        return self.run_persistent(cmd)

    def _tmux(self, cmd: str, session: str) -> Dict[str, Any]:
        subprocess.run(f"tmux kill-session -t {shlex.quote(session)} 2>/dev/null",
                       shell=True)
        inner = f"bash -c {shlex.quote(cmd)}"
        launch = f"tmux new-session -d -s {shlex.quote(session)} {shlex.quote(inner)}"
        self.dbg(f"tmux launch: {launch}")

        try:
            r = subprocess.run(launch, shell=True, capture_output=True,
                               text=True, timeout=5)
            if r.returncode != 0:
                self.dbg(f"tmux stderr: {r.stderr.strip()[:300]}")
            time.sleep(0.8)

            check = subprocess.run(f"tmux has-session -t {shlex.quote(session)}",
                                   shell=True, capture_output=True)
            alive = check.returncode == 0

            out = (f"tmux session '{session}' {'started' if alive else 'FAILED'}\n"
                   f"Attach:  tmux attach -t {session}\n"
                   f"Detach:  Ctrl+B then D\n"
                   f"Kill:    tmux kill-session -t {session}\n"
                   f"List:    tmux ls")

            entry = self._entry(cmd, alive, out,
                                r.stderr if not alive else "", 0, "terminal")
            entry.update({'session': session, 'terminal': 'tmux'})
            self.history.append(entry)

            if alive:
                self.log(f"✅ tmux session '{session}' running", "SUCCESS")
                self.log(f"   Attach: tmux attach -t {session}")
            else:
                self.log(f"❌ tmux failed: {r.stderr.strip()[:200]}", "ERROR")
            return entry
        except Exception as e:
            self.log(f"tmux exception: {e}", "ERROR")
            entry = self._err(cmd, str(e), "terminal")
            self.history.append(entry)
            return entry

    def _gui_terminal(self, cmd, session, launcher_fn, term_name):
        keep_open = f"{cmd}; echo; echo '[process exited - press Enter to close]'; read"
        try:
            argv = launcher_fn(keep_open, session)
            self.dbg(f"{term_name} argv: {argv}")
            subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.5)
            entry = self._entry(cmd, True,
                                f"{term_name} window '{session}' opened with: {cmd}",
                                "", 0, "terminal")
            entry.update({'session': session, 'terminal': term_name})
            self.history.append(entry)
            self.log(f"✅ {term_name} window opened (session: {session})", "SUCCESS")
            return entry
        except Exception as e:
            self.log(f"❌ Failed to open {term_name}: {e}", "ERROR")
            entry = self._err(cmd, str(e), "terminal")
            self.history.append(entry)
            return entry

    def _xfce(self, cmd, session):
        def launcher(keep_open, s):
            return ['xfce4-terminal', '--title', s, '--hold',
                    '-e', f'bash -c {shlex.quote(keep_open)}']
        return self._gui_terminal(cmd, session, launcher, 'xfce4-terminal')

    def _gnome(self, cmd, session):
        def launcher(keep_open, s):
            return ['gnome-terminal', '--title', s,
                    '--', 'bash', '-c', keep_open]
        return self._gui_terminal(cmd, session, launcher, 'gnome-terminal')

    def _konsole(self, cmd, session):
        def launcher(keep_open, s):
            return ['konsole', '--hold', '-p', f'tabtitle={s}',
                    '-e', 'bash', '-c', keep_open]
        return self._gui_terminal(cmd, session, launcher, 'konsole')

    def _xterm(self, cmd, session):
        def launcher(keep_open, s):
            return ['xterm', '-title', s, '-hold',
                    '-e', 'bash', '-c', keep_open]
        return self._gui_terminal(cmd, session, launcher, 'xterm')

    def run_detached(self, cmd: str) -> Dict[str, Any]:
        self.log(f"DETACH: {cmd}", "EXEC")
        if self.is_dangerous(cmd):
            entry = self._err(cmd, "Blocked: dangerous", "detached")
            self.history.append(entry)
            return entry

        log_dir = os.path.join(self.working_dir, ".agent_logs")
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"daemon_{ts}.log")
        pid_file = os.path.join(log_dir, f"daemon_{ts}.pid")

        wrapped = (
            f"setsid nohup bash -c {shlex.quote(cmd)} "
            f"> {shlex.quote(log_file)} 2>&1 < /dev/null & "
            f"echo $! > {shlex.quote(pid_file)}; sleep 1; cat {shlex.quote(pid_file)}"
        )
        try:
            r = subprocess.run(wrapped, shell=True, capture_output=True,
                               text=True, timeout=10, cwd=self.working_dir)
            pid = r.stdout.strip().split('\n')[-1]
            time.sleep(1)
            check = subprocess.run(f"ps -p {pid} -o pid=,cmd=", shell=True,
                                   capture_output=True, text=True)
            alive = check.returncode == 0 and pid in check.stdout

            out = (f"Detached daemon started\nPID: {pid}\n"
                   f"Log: {log_file}\nStatus: {'RUNNING' if alive else 'DIED'}\n"
                   f"Stop with: kill {pid}")
            entry = self._entry(cmd, alive, out, "", 0, "detached")
            entry.update({'pid': pid, 'log_file': log_file})
            self.history.append(entry)
            if alive:
                self.background.append({'command': cmd, 'pid': pid,
                                        'log_file': log_file,
                                        'started': datetime.now().isoformat()})
                self.log(f"✅ Daemon running (PID {pid})", "SUCCESS")
            return entry
        except Exception as e:
            entry = self._err(cmd, str(e), "detached")
            self.history.append(entry)
            return entry

    # -------------------- AI: next action --------------------
    def next_action(self, user_input: str) -> Dict:
        recent = [{
            'type': h.get('type', 'command'),
            'command': h['command'],
            'success': h['success'],
            'output': (h['output'] or '')[:800],
            'error': (h['error'] or '')[:300],
        } for h in self.history[-6:]]

        last = self.history[-1] if self.history else None
        last_block = "None yet"
        if last:
            last_block = (
                f"Type: {last.get('type', 'command')}\n"
                f"Command: {last['command']}\n"
                f"Success: {last['success']}\n"
                f"Output:\n{(last['output'] or '')[:1500]}\n"
                f"Error:\n{(last['error'] or '')[:400]}"
            )

        terminals = [k for k, v in self.terminals.items() if v]
        d = self.distro

        system = f"""You are a Linux command execution agent running on {d['name']} (family: {d.get('family', 'unknown')}).

DISTRO PACKAGE MANAGER (USE THIS, NOT OTHERS):
  Update : {d['pkg_update']}
  Install: {d['pkg_install']}
  Search : {d['pkg_search']}
NEVER use apt on non-Debian systems. NEVER use dnf on Debian systems.
Note: yum and dnf are interchangeable on RHEL systems.

COMMAND TYPES:
  COMMAND: <cmd>       - regular blocking command, wait for result
  PERSISTENT: <cmd>    - background process (nohup)
  TERMINAL: <cmd>      - open in a NEW visible terminal window
  DETACH: <cmd>        - full daemon (setsid)

HEREDOCS:
- If you use a heredoc (cat << 'EOF' ... EOF), the ENTIRE thing — header,
  body, and terminator — MUST be in a single COMMAND block.
- You cannot split a heredoc across multiple COMMAND blocks.
- Prefer 'printf' or 'tee' for short file writes; use heredoc only for multi-line.

RULES:
- NEVER type "tmux", "xterm", "xfce4-terminal", "gnome-terminal", "konsole",
  "--command=" or "-e" in your command. The agent opens the terminal for you.
- After "TERMINAL:", write ONLY the bare command. Examples:
    TERMINAL: python3 -m http.server 9000
    TERMINAL: watch -n 1 free -m
  WRONG:
    TERMINAL: xfce4-terminal --command="..."    ← will be stripped, but don't do it
    TERMINAL: tmux new-session -d '...'         ← will be stripped, but don't do it
- For servers/listeners/long-running commands, ALWAYS use TERMINAL or
  PERSISTENT. NEVER use COMMAND — it will hang and time out.
- NEVER repeat a command already in history.
  * If "Address already in use", use a DIFFERENT port.
  * If a command failed, change the approach — do NOT retry verbatim.
- If last shows "Timeout after 60s", switch to TERMINAL/PERSISTENT for retry.
- If last shows "Permission denied", retry with "sudo" prefix.
- When you have enough info to answer, reply: TASK_COMPLETE

Format:
  <COMMAND|PERSISTENT|TERMINAL|DETACH>: <bare command>
  REASON: <why>
  EXPECTED_OUTCOME: <expected>

OR just: TASK_COMPLETE

Available terminals: {', '.join(terminals) or 'none (will use persistent)'}"""

        user = f"""USER REQUEST: {user_input}

ACTIONS SO FAR ({len(self.history)}):
{json.dumps(recent, indent=2) if recent else 'None'}

LAST ACTION:
{last_block}

Next step?"""

        raw = self.chat(system, user, max_tokens=600)
        if raw is None:
            return {'error': 'API returned nothing', 'is_complete': False}

        self.dbg(f"Raw AI:\n{raw}")

        low = raw.lower()
        if any(m in low for m in REFUSAL_MARKERS):
            return {'error': None, 'is_complete': True, 'refusal': raw.strip()}

        return self._parse(raw)

    def _parse(self, raw: str) -> Dict:
        d = {'action_type': None, 'command': None, 'reason': '',
             'expected': '', 'is_complete': False, 'refusal': None}

        prefixes = {
            'COMMAND:': 'command',
            'PERSISTENT:': 'persistent',
            'TERMINAL:': 'terminal',
            'DETACH:': 'detached',
        }

        for line in raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            if 'TASK_COMPLETE' in line.upper():
                d['is_complete'] = True
                continue
            for pfx, kind in prefixes.items():
                if line.upper().startswith(pfx):
                    d['action_type'] = kind
                    d['command'] = line[len(pfx):].strip()
                    break
            if line.upper().startswith('REASON:'):
                d['reason'] = line[7:].strip()
            elif line.upper().startswith('EXPECTED_OUTCOME:'):
                d['expected'] = line[17:].strip()

        return d

    # -------------------- final answer --------------------
    def final_answer(self, user_input: str) -> str:
        findings = []
        for i, h in enumerate(self.history, 1):
            findings.append({
                'step': i,
                'type': h.get('type', 'command'),
                'command': h['command'],
                'success': h['success'],
                'output': (h['output'] or '')[:4000],
               