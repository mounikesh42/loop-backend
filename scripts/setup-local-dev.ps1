$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$BundledPython = "C:\Users\MOUNIKESH\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $Venv)) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        python -m venv $Venv
    } elseif (Test-Path $BundledPython) {
        & $BundledPython -m venv $Venv
    } else {
        throw "No Python executable found. Install Python 3.12 or update BundledPython in this script."
    }
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

& $Python -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python dependencies from requirements.txt."
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "local-data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "uploads") | Out-Null

Write-Host "Local dev environment is ready."
Write-Host "Run: .\scripts\run-local-api.ps1"
