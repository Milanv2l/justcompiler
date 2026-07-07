$ErrorActionPreference = "Stop"

$GITHUB_USER = "Milanv2l"
$GITHUB_REPO = "justcompiler"
$BRANCH = "main"
$BASE_URL = "https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$BRANCH"

$INSTALL_DIR = "$HOME\.justcompiler"
$PYTHON_FILES = @("justcompiler.py", "core.py", "engine.py", "docker_manager.py", "plugins.json", "version.txt")

Write-Host "--- JustCompiler Updater ---" -ForegroundColor Cyan
Write-Host "[INFO] Fetching updates from repository..." -ForegroundColor Cyan

$hashTable = @{}
Write-Host "  Fetching: checksums.txt..."
Invoke-WebRequest -Uri "$BASE_URL/checksums.txt" -OutFile "$INSTALL_DIR\checksums.txt" -UseBasicParsing
Get-Content "$INSTALL_DIR\checksums.txt" | ForEach-Object {
    if ($_ -match "^\s*([a-f0-9]+)\s+\*?([\w\./-]+)$") {
        $hashTable[$matches[2]] = $matches[1]
    }
}

foreach ($file in $PYTHON_FILES) {
    Write-Host "  Updating: $file..."
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
    Write-Host "[WARN] Some files failed checksum verification." -ForegroundColor Yellow
} else {
    Write-Host "[OK] All checksums verified." -ForegroundColor Green
}

$PROFILE_PATH = $PROFILE
if (Test-Path -Path $PROFILE_PATH) {
    $content = Get-Content $PROFILE_PATH -Raw -ErrorAction SilentlyContinue
    if ($content -notmatch "function justcompiler") {
        $ALIAS_LINE = "Set-Alias -Name justcompiler -Value python -Option AllScope; function justcompiler { python `"$INSTALL_DIR\justcompiler.py`" }"
        Add-Content -Path $PROFILE_PATH -Value "`n$ALIAS_LINE"
        Write-Host "[OK] Restored alias in PowerShell profile." -ForegroundColor Green
    }
}

Write-Host "[OK] All components are up to date." -ForegroundColor Green
