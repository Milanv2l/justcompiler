#!/bin/bash
set -e

GITHUB_USER="Milanv2l"
GITHUB_REPO="justcompiler"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

INSTALL_DIR="$HOME/.justcompiler"
PYTHON_FILES=("justcompiler.py" "core.py" "engine.py" "docker_manager.py" "plugins.json" "version.txt")

echo "--- JustCompiler Updater ---"
echo "[INFO] Fetching updates from repository..."

echo "  Fetching: checksums.txt..."
curl -sSf "$BASE_URL/checksums.txt" -o "$INSTALL_DIR/checksums.txt"

for file in "${PYTHON_FILES[@]}"; do
    echo "  Updating: $file..."
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
else
    echo "[OK] All checksums verified."
fi

for PROFILE_FILE in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile"; do
    if [ -f "$PROFILE_FILE" ]; then
        if ! grep -q "alias justcompiler" "$PROFILE_FILE" 2>/dev/null; then
            echo -e "\n# JustCompiler Alias\nalias justcompiler=\"python3 $INSTALL_DIR/justcompiler.py\"" >> "$PROFILE_FILE"
            echo "[OK] Restored alias in $PROFILE_FILE"
        fi
    fi
done

echo "[OK] All components are up to date."
