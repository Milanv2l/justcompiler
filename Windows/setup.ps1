$ErrorActionPreference = "Stop"

$GITHUB_USER = "Milanv2l"
$GITHUB_REPO = "justcompiler"
$BRANCH = "main"
$BASE_URL = "https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

$INSTALL_DIR = "$HOME\.justcompiler"
$PYTHON_FILES = @("justcompiler.py", "core.py", "engine.py", "docker_manager.py", "plugins.json", "version.txt")

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

$hashTable = @{}
Write-Host "  Fetching: checksums.txt..."
Invoke-WebRequest -Uri "$BASE_URL/checksums.txt" -OutFile "$INSTALL_DIR\checksums.txt" -UseBasicParsing
Get-Content "$INSTALL_DIR\checksums.txt" | ForEach-Object {
    if ($_ -match "^\s*([a-f0-9]+)\s+\*?([\w\./-]+)$") {
        $hashTable[$matches[2]] = $matches[1]
    }
}

foreach ($file in $PYTHON_FILES) {
    Write-Host "  Fetching: $file..."
    Invoke-WebRequest -Uri "$BASE_URL/$file" -OutFile "$INSTALL_DIR\$file" -UseBasicParsing
}

Write-Host "  Fetching: update.ps1..."
Invoke-WebRequest -Uri "$BASE_URL/Windows/update.ps1" -OutFile "$INSTALL_DIR\update.ps1" -UseBasicParsing

Write-Host "  Fetching: uninstall.ps1..."
Invoke-WebRequest -Uri "$BASE_URL/Windows/uninstall.ps1" -OutFile "$INSTALL_DIR\uninstall.ps1" -UseBasicParsing

Write-Host "  Verifying checksums..."
$verifyOk = $true
Get-ChildItem "$INSTALL_DIR\*.py", "$INSTALL_DIR\*.json", "$INSTALL_DIR\*.txt", "$INSTALL_DIR\*.ps1" -ErrorAction SilentlyContinue | ForEach-Object {
    $name = $_.Name
    if ($hashTable.ContainsKey($name)) {
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $hashTable[$name]) {
            Write-Host "  [WARN] Checksum mismatch: $name" -ForegroundColor Yellow
            $verifyOk = $false
        }
    }
}

if (-not $verifyOk) {
    Write-Host "[WARN] Some files failed checksum verification. They may be corrupted." -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? (y/n)"
    if ($continue -notmatch "^[yY](es)?$") {
        Write-Host "[INFO] Installation aborted." -ForegroundColor Red
        Remove-Item -Recurse -Force $INSTALL_DIR -ErrorAction SilentlyContinue
        exit 1
    }
} else {
    Write-Host "[OK] All checksums verified." -ForegroundColor Green
}

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
