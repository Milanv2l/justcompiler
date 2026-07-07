#!/bin/bash
set -e

INSTALL_DIR="$HOME/.justcompiler"

echo "--- JustCompiler Uninstaller ---"
read -p "Are you sure you want to uninstall JustCompiler? (y/n): " confirm < /dev/tty
if [[ ! "$confirm" =~ ^[Yy](es)?$ ]]; then
    echo "[INFO] Uninstallation cancelled."
    exit 0
fi

if command -v docker &> /dev/null; then
    echo "[INFO] Removing Docker images..."
    IMAGES=$(docker images justcompiler-engine -q 2>/dev/null)
    if [ -n "$IMAGES" ]; then
        for img in $IMAGES; do
            sudo docker rmi -f "$img" 2>/dev/null || true
        done
    fi
    sudo docker image prune -f 2>/dev/null || true
fi

if [ -d "$INSTALL_DIR" ]; then
    echo "[INFO] Removing $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
fi

for PROFILE_FILE in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile"; do
    if [ -f "$PROFILE_FILE" ]; then
        if grep -q "alias justcompiler" "$PROFILE_FILE" 2>/dev/null; then
            sed -i '/alias justcompiler/d' "$PROFILE_FILE"
            echo "[OK] Removed alias from $PROFILE_FILE"
        fi
    fi
done

echo "[OK] JustCompiler has been completely uninstalled."
