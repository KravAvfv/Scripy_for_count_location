# Build SiteSizer.exe (single file) on Windows.
# Usage (PowerShell, from the repo folder):   .\build.ps1
# Optional:                                     .\build.ps1 -SkipTests
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    py -3 -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r requirements-dev.txt

if (-not $SkipTests) {
    Write-Host "Running tests..." -ForegroundColor Cyan
    $env:QT_QPA_PLATFORM = "offscreen"
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed — build aborted." }
    Remove-Item Env:\QT_QPA_PLATFORM
}

& $py packaging\make_icon.py
& $py packaging\make_version.py
& $py -m PyInstaller --noconfirm --clean packaging\sitesizer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = Get-Item "dist\SiteSizer.exe"
Write-Host ("Done: {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB)) -ForegroundColor Green
