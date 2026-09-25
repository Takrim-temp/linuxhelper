This project uses the Gemini model, so you need to create an account in Google AI Studio ([https://aistudio.google.com/api-keys](https://aistudio.google.com/api-keys)) and get an API key from there. If you use other models, just change the source code accordingly. Edit ~/.ai_agent_repls.json only when you want the agent to drive an interactive tool that isn't in the built-in list.

**Installation Guide for Debian / Ubuntu / Kali**

1. Make sure Python is installed. If not, install Python.
2. Run: `sudo apt install -y tmux xfce4-terminal`
3. Go to the `linuxhelper` directory.
4. Run: `python3 -m venv venv`
5. Run: `source venv/bin/activate`
6. Run: `pip install openai tabulate`

**Installation Guide for RHEL / Fedora / Rocky / Alma / CentOS**

1. Make sure Python is installed. If not, install Python.
2. Run: `sudo dnf install -y tmux gnome-terminal`
3. Go to the `linuxhelper` directory.
4. Run: `python3 -m venv venv`
5. Run: `source venv/bin/activate`
6. Run: `pip install openai tabulate`

Here's the simplified user manual:

# AI Linux Agent — User Manual

## What it does

You type a request in plain English. The agent figures out what to do, runs the commands, reads the results, and gives you back a real answer.

It handles three kinds of requests automatically:

- **Questions** — *"what is a subnet mask?"* → answers directly, runs nothing.
- **Tasks** — *"install nginx and start it on port 8080"* → runs commands step by step.
- **Hybrid** — *"what's my IP?"* → checks the system, then answers with the real value.

---

## How to run it

Every session, do this first:

```bash
cd linuxhelper
source venv/bin/activate
```

Then run your request. Pass the API key directly in the command:

```bash
python3 ai_agent.py --api-key "your-key-here" "your request"
```

---

## Basic usage

### Ask a question

```bash
python3 ai_agent.py --api-key "your-key-here" "what is a subnet mask?"
```

The agent answers from knowledge. No commands are run.

### Run a task

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve \
  "install nginx and start it on port 8080"
```

The agent runs the commands, then writes a summary at the end.

### Ask a question that checks the system

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve \
  "what's my IP address and what network am I on?"
```

The agent runs `ip a`, `ip route`, and similar, then answers with the real values.

### Start a server in a new terminal window

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve --terminal gui \
  "start a python http server on port 9090 in a new terminal"
```

A window pops up on your desktop running the server. The agent prints the URL and the stop command at the end.

### Over SSH (no desktop)

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve --terminal tmux \
  "start a python http server on port 9090 in the background"
```

Creates a tmux session you can attach to from anywhere:

```bash
tmux attach -t agent_HHMMSS
```

---

## All options

| Flag | Default | What it does |
|---|---|---|
| `--api-key` | *required* | Your Gemini API key |
| `--model` | `gemini-3.5-flash-lite` | Which Gemini model to use |
| `--terminal` | `auto` | `auto`, `gui`, or `tmux` |
| `--auto-approve` | off | Skip per-command confirmation |
| `--timeout` | `60s` | Max time for a blocking command |
| `--max-iter` | `30` | Max loop iterations before giving up |
| `--dir` | current | Working directory for commands |
| `--save` | off | Save session to a JSON file |
| `--debug` | off | Print raw AI responses |

### Timeout formats

```bash
--timeout 30s      # 30 seconds
--timeout 2m       # 2 minutes
--timeout 1h       # 1 hour
--timeout 90       # 90 seconds
```

---

## Approving commands

By default, the agent asks before running each command:

```
  Type: terminal
  Cmd:  python3 -m http.server 9090
  Why:  Start a file server on port 9090
  Execute? [y/N/a=auto]
```

Your choices:

| Key | Meaning |
|---|---|
| `y` | Run this one command |
| `n` or Enter | Skip it |
| `a` | Approve this and all following commands (switches to auto mode) |

With `--auto-approve`, this prompt is skipped entirely. Use it only on tasks you trust.

---

## Command types

The agent picks one of four modes for each command:

| Mode | What happens | Used for |
|---|---|---|
| `COMMAND` | Runs and waits for the result | `ls`, `cat`, `apt install`, `curl` |
| `TERMINAL` | Opens a new visible terminal window | Servers, `htop`, `watch`, editors |
| `PERSISTENT` | Background process with a log file | Long jobs you don't need to see |
| `DETACH` | Full daemon (survives shell exit) | Services you want fully isolated |

You can nudge it in your request:

- *"…in a new terminal"* → forces `TERMINAL`
- *"…in the background"* → forces `PERSISTENT`
- *"…as a daemon"* → forces `DETACH`

---

## Common use cases

### Diagnose a slow machine

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve \
  "figure out why this machine is slow and give me a report with real numbers"
```

### Find large files

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve \
  "find all files in my home directory larger than 100MB, sort by size, show top 10"
```

### Check disk usage

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve \
  "show me what's using the most disk space in my home folder"
```

### Three-terminal monitoring dashboard

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve --terminal gui \
  "open three terminals: one running 'watch -n 1 free -m', one running 'watch -n 1 df -h', and one running 'python3 -m http.server 9090'"
```

### Set up a dev environment

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve --terminal gui \
  "create a folder called myapp, set up a Python venv, install flask, write a hello-world app.py, and run it on port 5000 in a new terminal"
```

### Save a session for later

```bash
python3 ai_agent.py --api-key "your-key-here" --auto-approve \
  --save session.json \
  "check disk usage and find the biggest directories in my home folder"
```

---

## Managing processes the agent starts

### Find what's running

```bash
tmux ls                              # all tmux sessions
ps aux | grep -E "http.server|watch" # background processes
```

### Attach to a tmux session

```bash
tmux attach -t agent_HHMMSS
```

Inside tmux:

- `Ctrl+B` then `D` — detach without killing it
- Type `exit` — close the process

### Stop a tmux session

```bash
tmux kill-session -t agent_HHMMSS
```

### Kill all sessions at once

```bash
tmux kill-server
```

### Kill a background process

The final answer always includes the PID. Use it:

```bash
kill <PID>
```

### Close a GUI terminal window

Just click the X. If the process inside is a server, closing the window kills it.

---

## Troubleshooting

### "No DISPLAY detected"

You're over SSH or on a text console. GUI windows can't appear.

```bash
echo "DISPLAY=$DISPLAY"   # empty means no GUI
```

**Fix:** use `--terminal tmux` instead of `--terminal gui`.

### "No GUI terminal emulator installed"

Install one:

```bash
sudo apt install -y xfce4-terminal    # Debian / Ubuntu / Kali
sudo dnf install -y gnome-terminal    # RHEL / Fedora
```

Or use `--terminal tmux`.

### "Address already in use"

Something is holding the port.

```bash
sudo lsof -i :9090      # see what's using it
kill <PID>              # kill it
tmux kill-server        # or kill all sessions
```

### "API connection failed"

- Check the key is correct
- Check quota at https://aistudio.google.com/app/apikey
- Free tier is rate-limited. The agent retries automatically.

### "Timeout after 60s"

The command didn't finish in the limit.

- Raise it: `--timeout 5m`
- Look at the command table to see what the agent tried

### "module not found: openai"

You forgot to activate the venv.

```bash
source venv/bin/activate
pip install openai tabulate
```

### The agent keeps running the same command

Run with `--debug` to see the raw AI output. Then:

- Lower the loop cap: `--max-iter 10`
- Rephrase your request more specifically
- Stop it with `Ctrl+C` and try again

---

## Tips for better results

**Be specific.** *"Install nginx"* works. *"Set up a web server"* works but the agent has to guess. *"Install nginx, configure it to serve /var/www/html on port 80, and start it"* works best.

**Say where you want long-running things.** *"…in a new terminal"* or *"…in the background"* prevents ambiguity.

**Use `--auto-approve` only on tasks you trust.**

**Raise `--timeout` for slow tasks.** Package installs, `nmap` scans, and database operations often need more than 60 seconds.

**Save important sessions with `--save`.**

**Use `--terminal tmux` when you're not sure.** It works over SSH, in headless environments, and on desktops.

---

## Quick reference

```bash
# Question
python3 ai_agent.py --api-key "your-key-here" "what is X?"

# Task
python3 ai_agent.py --api-key "your-key-here" --auto-approve "do Y"

# Server in a window
python3 ai_agent.py --api-key "your-key-here" --auto-approve --terminal gui "run Z"

# Over SSH
python3 ai_agent.py --api-key "your-key-here" --auto-approve --terminal tmux "run Z"

# Slow task
python3 ai_agent.py --api-key "your-key-here" --auto-approve --timeout 5m "do W"

# Save session
python3 ai_agent.py --api-key "your-key-here" --auto-approve --save run.json "do V"

# Debug
python3 ai_agent.py --api-key "your-key-here" --debug "why is this broken?"
```
