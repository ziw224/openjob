#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/bin"

echo "🔧 Installing openjob..."

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 is required. Install from https://python.org"
    exit 1
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"

# Install Playwright browser
echo "🌐 Installing Playwright Chromium..."
python3 -m playwright install chromium

# Create openjob command
mkdir -p "$INSTALL_DIR"
cat > "$INSTALL_DIR/openjob" << SCRIPT
#!/bin/bash
cd "$SCRIPT_DIR"
python3 src/cli.py "\$@"
SCRIPT
chmod +x "$INSTALL_DIR/openjob"

# Check PATH
if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
    echo ""
    echo "⚠️  Add to your shell config (~/.zshrc or ~/.bashrc):"
    echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "   Then run: source ~/.zshrc"
fi

echo ""
echo "✅ Done! Next steps:"
echo ""
echo "  1. cp .env.example .env"
echo "     → Fill in CANDIDATE_NAME, EMAIL, OPENAI_API_KEY"
echo ""
echo "  2. Edit resume/base_resume_ai.html with your AI/ML resume"
echo "     Edit resume/base_resume.html with your full-stack resume"
echo ""
echo "  3. Edit config/search_config.json to set your target keywords"
echo ""
echo "  4. openjob run"
echo ""
