#!/bin/bash
set -e

GITHUB_USER="Milanv2l"
GITHUB_REPO="justcompiler"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

INSTALL_DIR="$HOME/.justcompiler"
# baremetal.py verwijderd, docker_manager.py toegevoegd
PYTHON_FILES=("justcompiler.py" "core.py" "engine.py" "docker_manager.py" "plugins.json")

echo "--- JustCompiler Updater ---"
echo "[INFO] Fetching updates from repository..."

for file in "${PYTHON_FILES[@]}"; do
    echo "  Updating: $file..."
    curl -sSf "$BASE_URL/$file" -o "$INSTALL_DIR/$file"
done

echo "[OK] All components are up to date."
