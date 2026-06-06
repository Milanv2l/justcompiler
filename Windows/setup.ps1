$ErrorActionPreference = "Stop"

$GITHUB_USER = "Milanv2l"
$GITHUB_REPO = "justcompiler"
$BRANCH = "main"
$BASE_URL = "https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

$INSTALL_DIR = "$HOME\.justcompiler"
$PYTHON_FILES = @("justcompiler.py", "core.py", "engine.py", "baremetal.py", "plugins.json")

Write-Host "--- JustCompiler Installer ---" -ForegroundColor Cyan

$confirm_install = Read-Host "Do you want to install JustCompiler on this system? (y/n)"
if ($confirm_install -notmatch "^[yY](es)?$") {
    Write-Host "[INFO] Installation cancelled by user." -ForegroundColor Yellow
    exit
}

$use_docker = Read-Host "Do you want to enable the Docker sandbox runtime environment? (y/n)"
if ($use_docker -match "^[yY](es)?$") {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Host "[OK] Docker detected." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Docker not found on this system." -ForegroundColor Yellow
        Write-Host "Please download Docker Desktop from: https://docs.docker.com/desktop/windows/" -ForegroundColor Yellow
    }
} else {
    Write-Host "[INFO] Skipping Docker integration. JustCompiler will run in host-only mode." -ForegroundColor Yellow
}

Write-Host "`n[INFO] Downloading components to $INSTALL_DIR..." -ForegroundColor Cyan
if (-not (Test-Path -Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR | Out-Null
}

foreach ($file in $PYTHON_FILES) {
    Write-Host "  Fetching: $file..."
    Invoke-WebRequest -Uri "$BASE_URL/$file" -OutFile "$INSTALL_DIR\$file" -UseBasicParsing
}

Write-Host "  Fetching: update.ps1..."
Invoke-WebRequest -Uri "$BASE_URL/Windows/update.ps1" -OutFile "$INSTALL_DIR\update.ps1" -UseBasicParsing

$PROFILE_DIR = Split-Path -Parent $PROFILE
if (-not (Test-Path -Path $PROFILE_DIR)) { New-Item -ItemType Directory -Path $PROFILE_DIR | Out-Null }
if (-not (Test-Path -Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE | Out-Null }

$ALIAS_LINE = "Set-Alias -Name justcompiler -Value python -Option AllScope; function justcompiler { python `"$INSTALL_DIR\justcompiler.py`" }"
$PROFILE_CONTENT = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue

if ($PROFILE_CONTENT -notmatch "function justcompiler") {
    Add-Content -Path $PROFILE -Value "`n$ALIAS_LINE"
    Write-Host "[OK] Registered 'justcompiler' alias to your PowerShell profile." -ForegroundColor Green
}

Write-Host "`n[OK] Installation completed successfully." -ForegroundColor Green
Write-Host "Please restart your terminal session and type 'justcompiler' to start." -ForegroundColor Yellow
