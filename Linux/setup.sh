#!/bin/bash
set -e

GITHUB_USER="Milanv2l"
GITHUB_REPO="justcompiler"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

INSTALL_DIR="$HOME/.justcompiler"
PYTHON_FILES=("justcompiler.py" "core.py" "engine.py" "baremetal.py" "plugins.json")

echo "--- JustCompiler Installer ---"

read -p "Do you want to install JustCompiler on this system? (y/n): " confirm_install
if [[ ! "$confirm_install" =~ ^[Yy](es)?$ ]]; then
    echo "[INFO] Installation cancelled by user."
    exit 0
fi

if command -v docker &> /dev/null; then
    echo "[OK] Docker detected."
else
    echo "[WARN] Docker not found on this system."
    echo "Please install Docker to use the sandbox runtime environment."
fi

echo -e "\n[INFO] Downloading components to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

for file in "${PYTHON_FILES[@]}"; do
    echo "  Fetching: $file..."
    curl -sSf "$BASE_URL/$file" -o "$INSTALL_DIR/$file"
done

echo "  Fetching: update.sh..."
curl -sSf "$BASE_URL/Linux/update.sh" -o "$INSTALL_DIR/update.sh"
chmod +x "$INSTALL_DIR/update.sh"

# Shell profiel detecteren (Bash of Zsh)
PROFILE_FILE=""
if [ -f "$HOME/.zshrc" ]; then
    PROFILE_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    PROFILE_FILE="$HOME/.bashrc"
elif [ -f "$HOME/.profile" ]; then
    PROFILE_FILE="$HOME/.profile"
fi

ALIAS_LINE="alias justcompiler=\"python3 $INSTALL_DIR/justcompiler.py\""

if [ -n "$PROFILE_FILE" ]; then
    if ! grep -q "alias justcompiler" "$PROFILE_FILE"; then
        echo -e "\n# JustCompiler Alias\n$ALIAS_LINE" >> "$PROFILE_FILE"
        echo "[OK] Registered 'justcompiler' alias to $PROFILE_FILE"
    fi
else
    echo "[WARN] Could not automatically detect shell profile. Please add this alias manually:"
    echo "  $ALIAS_LINE"
fi

echo -e "\n[OK] Installation completed successfully."
echo "Please restart your terminal session or run 'source $PROFILE_FILE', then type 'justcompiler' to start."
