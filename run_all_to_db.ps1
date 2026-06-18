param(
    [string]$Python = "python",
    [switch]$SkipBaseStation,
    [switch]$SkipGcp,
    [switch]$SkipDrone,
    [switch]$SkipCheckPoint
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DbPath = Join-Path $Root "apicalls\pipeline.db"
$env:LOOP_PIPELINE_DB = $DbPath

function Run-Step {
    param(
        [string]$Name,
        [string]$WorkDir,
        [string[]]$Args
    )

    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    Push-Location $WorkDir
    try {
        & $Python @Args
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "Shared DB: $DbPath" -ForegroundColor Green

if (-not $SkipGcp) {
    Run-Step `
        -Name "GCP -> shared DB" `
        -WorkDir (Join-Path $Root "GCP_CodeBase\GCP_CodeBase") `
        -Args @("scripts\load_to_db.py", "paths.json")
}

if (-not $SkipDrone) {
    Run-Step `
        -Name "Drone -> shared DB" `
        -WorkDir (Join-Path $Root "Drone_CodeBase\Drone_CodeBase") `
        -Args @("scripts\load_to_db.py", "paths.json")
}

if (-not $SkipCheckPoint) {
    Run-Step `
        -Name "Check Point -> shared DB" `
        -WorkDir (Join-Path $Root "CheckPoint_CodeBase\CheckPoint_CodeBase") `
        -Args @("scripts\sqlite_pipeline.py", "save", "paths.json")
}

if (-not $SkipBaseStation) {
    Run-Step `
        -Name "Base Station -> shared DB" `
        -WorkDir (Join-Path $Root "BaseStation_CodeBase\BaseStation_CodeBase") `
        -Args @("scripts\run_pipeline.py", "paths.json")
}

Write-Host ""
Write-Host "Done. Shared DB refreshed at: $DbPath" -ForegroundColor Green
