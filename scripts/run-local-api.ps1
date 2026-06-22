$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Local virtualenv not found. Run .\scripts\setup-local-dev.ps1 first."
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "local-data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "uploads") | Out-Null

$env:HOST = "127.0.0.1"
$env:PORT = "8000"
$env:LOOP_UPLOAD_ROOT = Join-Path $Root "uploads"
$env:LOOP_JOBS_DB = Join-Path $Root "local-data\jobs.db"
$env:LOOP_PIPELINE_DB = Join-Path $Root "local-data\pipeline.db"
$env:LOOP_PIPELINE_TIMEOUT_SECONDS = "1800"
$env:LOOP_SKIP_MODULE_SITE_PACKAGES = "1"
$env:FLASK_DEBUG = "1"

Write-Host "Starting LOOP API at http://localhost:$($env:PORT)"
Write-Host "Jobs DB: $($env:LOOP_JOBS_DB)"
Write-Host "Pipeline DB: $($env:LOOP_PIPELINE_DB)"

& $Python (Join-Path $Root "apicalls\api.py")
