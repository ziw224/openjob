#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Pick the best install dir — prefer one already in PATH
_pick_install_dir() {
    # Check common dirs that are usually already in PATH
    for candidate in /usr/local/bin /opt/homebrew/bin "$HOME/.local/bin"; do
        if echo "$PATH" | tr ':' '\n' | grep -qxF "$candidate"; then
            if [ -w "$candidate" ] 2>/dev/null || mkdir -p "$candidate" 2>/dev/null && [ -w "$candidate" ]; then
                echo "$candidate"
                return
            fi
        fi
    done
    # Fallback: ~/.local/bin (may need PATH update)
    echo "$HOME/.local/bin"
}
INSTALL_DIR="$(_pick_install_dir)"

echo "🔧 Installing openjob..."

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 is required."
    echo "   Install via Homebrew: brew install python"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "   Python $PYTHON_VERSION detected"

# Create virtual environment
echo ""
echo "📦 Creating virtual environment (.venv)..."
python3 -m venv "$VENV_DIR"

# Install dependencies into venv
echo "📦 Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# Install Playwright browser
echo "🌐 Installing Playwright Chromium..."
"$VENV_DIR/bin/playwright" install chromium

# Create openjob command (points to venv Python)
mkdir -p "$INSTALL_DIR"
cat > "$INSTALL_DIR/openjob" << SCRIPT
#!/bin/bash
cd "$SCRIPT_DIR"
"$VENV_DIR/bin/python3" src/cli.py "\$@"
SCRIPT
chmod +x "$INSTALL_DIR/openjob"

# Add to PATH only if install dir isn't already in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$INSTALL_DIR"; then
    PATH_LINE="export PATH=\"$INSTALL_DIR:\$PATH\""
    # Detect shell config file
    if [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
        SHELL_RC="$HOME/.bash_profile"
    else
        SHELL_RC="$HOME/.zshrc"
    fi
    if ! grep -qF "$INSTALL_DIR" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# openjob" >> "$SHELL_RC"
        echo "$PATH_LINE" >> "$SHELL_RC"
    fi
    export PATH="$INSTALL_DIR:$PATH"
    echo ""
    echo "⚡ Run 'source $SHELL_RC' to make openjob available in future terminals."
fi

echo ""
echo "✅ Done! Installed to $INSTALL_DIR"
echo ""
echo "  1. openjob setup    → choose AI model & enter API key"
echo "  2. openjob run"
echo ""
