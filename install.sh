#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
INSTALL_DIR="$HOME/.local/bin"

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

# Auto-add to PATH if not already present
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
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

    # Only append if not already in the file
    if ! grep -qF "$PATH_LINE" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# openjob" >> "$SHELL_RC"
        echo "$PATH_LINE" >> "$SHELL_RC"
        echo "✅ Added ~/.local/bin to PATH in $SHELL_RC"
    fi

    # Apply to current session
    export PATH="$INSTALL_DIR:$PATH"
fi

echo ""
echo "✅ Done! Next steps:"
echo ""
echo "  1. openjob setup"
echo "     → Choose your AI model & enter API key"
echo ""
echo "  2. Edit resume/base_resume_ai.html with your resume"
echo ""
echo "  3. openjob run"
echo ""
