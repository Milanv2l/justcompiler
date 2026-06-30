$ErrorActionPreference = "Stop"

$GITHUB_USER = "Milanv2l"
$GITHUB_REPO = "justcompiler"
$BRANCH = "main"
$BASE_URL = "https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

$INSTALL_DIR = "$HOME\.justcompiler"
# baremetal.py verwijderd, docker_manager.py toegevoegd
$PYTHON_FILES = @("justcompiler.py", "core.py", "engine.py", "docker_manager.py", "plugins.json")

Write-Host "--- JustCompiler Updater ---" -ForegroundColor Cyan
Write-Host "[INFO] Fetching updates from repository..." -ForegroundColor Cyan

foreach ($file in $PYTHON_FILES) {
    Write-Host "  Updating: $file..."
    Invoke-WebRequest -Uri "$BASE_URL/$file" -OutFile "$INSTALL_DIR\$file" -UseBasicParsing
}

Write-Host "[OK] All components are up to date." -ForegroundColor Green
