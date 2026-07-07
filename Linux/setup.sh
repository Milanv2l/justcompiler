#!/bin/bash
set -e

GITHUB_USER="Milanv2l"
GITHUB_REPO="justcompiler"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

INSTALL_DIR="$HOME/.justcompiler"
PYTHON_FILES=("justcompiler.py" "core.py" "engine.py" "docker_manager.py" "plugins.json")

echo "--- JustCompiler Installer ---"

read -p "Do you want to install JustCompiler on this system? (y/n): " confirm_install < /dev/tty
if [[ ! "$confirm_install" =~ ^[Yy](es)?$ ]]; then
    echo "[INFO] Installation cancelled by user."
    exit 0
fi

read -p "Do you want to enable the Docker sandbox runtime environment? (y/n): " use_docker < /dev/tty
if [[ "$use_docker" =~ ^[Yy](es)?$ ]]; then
    if command -v docker &> /dev/null; then
        echo "[OK] Docker detected."
    else
        echo "[WARN] Docker not found on this system."
        echo "Please install Docker to use the sandbox runtime environment."
    fi
else
    echo "[INFO] Skipping Docker integration. JustCompiler will run in host-only mode."
fi

echo -e "\n[INFO] Downloading components to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

echo "  Fetching: checksums.txt..."
curl -sSf "$BASE_URL/checksums.txt" -o "$INSTALL_DIR/checksums.txt"

for file in "${PYTHON_FILES[@]}"; do
    echo "  Fetching: $file..."
    curl -sSf "$BASE_URL/$file" -o "$INSTALL_DIR/$file"
done

echo "  Fetching: update.sh..."
curl -sSf "$BASE_URL/Linux/update.sh" -o "$INSTALL_DIR/update.sh"
chmod +x "$INSTALL_DIR/update.sh"

echo "  Fetching: uninstall.sh..."
curl -sSf "$BASE_URL/Linux/uninstall.sh" -o "$INSTALL_DIR/uninstall.sh"
chmod +x "$INSTALL_DIR/uninstall.sh"

echo "  Verifying checksums..."
VERIFY_OK=1
while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    expected_hash=$(echo "$line" | awk '{print $1}')
    filename=$(echo "$line" | awk '{print $2}')
    filepath="$INSTALL_DIR/$filename"
    if [ -f "$filepath" ]; then
        actual_hash=$(sha256sum "$filepath" | awk '{print $1}')
        if [ "$expected_hash" != "$actual_hash" ]; then
            echo "[WARN] Checksum mismatch: $filename"
            VERIFY_OK=0
        fi
    fi
done < "$INSTALL_DIR/checksums.txt"

if [ "$VERIFY_OK" -eq 0 ]; then
    echo "[WARN] Some files failed checksum verification. They may be corrupted."
    read -p "Continue anyway? (y/n): " continue_anyway < /dev/tty
    if [[ ! "$continue_anyway" =~ ^[Yy](es)?$ ]]; then
        echo "[INFO] Installation aborted."
        rm -rf "$INSTALL_DIR"
        exit 1
    fi
else
    echo "[OK] All checksums verified."
fi

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
