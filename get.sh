#!/bin/bash
# openjob one-line installer
# curl -fsSL https://raw.githubusercontent.com/ziw224/openjob/master/get.sh | bash
set -e

REPO="https://github.com/ziw224/openjob.git"
INSTALL_DIR="$HOME/.openjob"
CYAN="\033[96m"; BOLD="\033[1m"; GREEN="\033[92m"; RESET="\033[0m"

echo -e "\n${BOLD}${CYAN}  🤖  Installing openjob...${RESET}\n"

# Check dependencies
for cmd in python3 git; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  ✗ '$cmd' not found. Please install it first."
        exit 1
    fi
done

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Updating existing install..."
    git -C "$INSTALL_DIR" pull --quiet
else
    echo "  Cloning openjob..."
    git clone --quiet "$REPO" "$INSTALL_DIR"
fi

# Run bundled install script
cd "$INSTALL_DIR"
bash install.sh

echo -e "\n${BOLD}${GREEN}  ✅  openjob installed!${RESET}"
echo -e "  Run: ${CYAN}openjob setup${RESET}\n"
