$ErrorActionPreference = "Stop"

$INSTALL_DIR = "$HOME\.justcompiler"

Write-Host "--- JustCompiler Uninstaller ---" -ForegroundColor Cyan
$confirm = Read-Host "Are you sure you want to uninstall JustCompiler? (y/n)"
if ($confirm -notmatch "^[yY](es)?$") {
    Write-Host "[INFO] Uninstallation cancelled." -ForegroundColor Yellow
    exit
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "[INFO] Removing Docker images..." -ForegroundColor Cyan
    $images = docker images justcompiler-engine -q 2>$null
    if ($images) {
        foreach ($img in $images) {
            docker rmi -f $img 2>$null
        }
    }
    docker image prune -f 2>$null
}

if (Test-Path -Path $INSTALL_DIR) {
    Write-Host "[INFO] Removing $INSTALL_DIR..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force $INSTALL_DIR -ErrorAction SilentlyContinue
}

$PROFILE_PATH = $PROFILE
if (Test-Path -Path $PROFILE_PATH) {
    $content = Get-Content $PROFILE_PATH -Raw -ErrorAction SilentlyContinue
    if ($content -match "justcompiler") {
        $content = $content -replace "(?m)^.*justcompiler.*\r?\n?", ""
        Set-Content $PROFILE_PATH -Value $content
        Write-Host "[OK] Removed alias from PowerShell profile." -ForegroundColor Green
    }
}

Write-Host "[OK] JustCompiler has been completely uninstalled." -ForegroundColor Green
