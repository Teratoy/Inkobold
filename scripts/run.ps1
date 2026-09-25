# Run Inkobold on Windows (MSYS2 MINGW64 Python on PATH, or repo .venv).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating venv with --system-site-packages (needs PyGObject/GTK4)..."
    python -m venv --system-site-packages .venv
    & (Join-Path $Root ".venv\Scripts\pip.exe") install -r requirements.txt
}

$env:INKOBOLD_ICON = Join-Path $Root "scripts\inkobold.png"
& $VenvPython -m inkobold @args
