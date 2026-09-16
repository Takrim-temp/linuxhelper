#!/usr/bin/env bash
# setup.sh — install Python and system dependencies for ai-linux-agent
set -e

echo "==> Detecting distro..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="$ID"
else
    echo "Cannot detect distro"; exit 1
fi

echo "==> Installing system packages..."
case "$DISTRO" in
    debian|ubuntu|kali|linuxmint|pop)
        sudo apt update
        sudo apt install -y python3 python3-pip python3-venv \
                            tmux xfce4-terminal curl
        ;;
    rhel|fedora|centos|rocky|alma|redhat)
        sudo dnf install -y python3 python3-pip tmux curl gnome-terminal
        ;;
    arch|manjaro|endeavouros)
        sudo pacman -Sy --noconfirm python python-pip tmux xterm
        ;;
    opensuse*|sles)
        sudo zypper install -y python3 python3-pip tmux xterm
        ;;
    alpine)
        sudo apk add python3 py3-pip tmux bash curl
        ;;
    *)
        echo "Unsupported distro: $DISTRO"
        echo "Install manually: python3, pip, venv, tmux, and a GUI terminal"
        exit 1
        ;;
esac

echo "==> Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "==> Installing Python packages..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete."
echo ""
echo "Next steps:"
echo "  1. Set your Gemini API key:"
echo "       export GEMINI_API_KEY=\"your-key\""
echo "  2. Activate the venv each session:"
echo "       source venv/bin/activate"
echo "  3. Run the agent:"
echo "       python3 ai_agent.py --api-key \"\$GEMINI_API_KEY\" \"your question\""