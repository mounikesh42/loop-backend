param(
  [Parameter(Mandatory=$true)]
  [string]$Bucket,

  [string]$Prefix = "site-reality/hyderabad-m7-2026-05-18",
  [string]$Region = "",
  [string]$Profile = "",
  [switch]$PublicRead
)

$ErrorActionPreference = "Stop"

function Invoke-Aws {
  param([string[]]$Args)

  $base = @()
  if ($Profile) { $base += @("--profile", $Profile) }
  if ($Region) { $base += @("--region", $Region) }
  & aws @base @Args
  if ($LASTEXITCODE -ne 0) {
    throw "aws command failed: aws $($base + $Args -join ' ')"
  }
}

function Sync-Asset {
  param(
    [string]$Source,
    [string]$Dest,
    [string[]]$ExtraArgs = @()
  )

  if (!(Test-Path -LiteralPath $Source)) {
    throw "Missing asset path: $Source"
  }

  $args = @("s3", "sync", $Source, $Dest, "--exclude", "*.log", "--exclude", "*.BIN", "--exclude", "*.kmz")
  if ($PublicRead) { $args += @("--acl", "public-read") }
  $args += $ExtraArgs
  Invoke-Aws -Args $args
}

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$compare = Join-Path $root "site_reality\compare"
$s3Base = "s3://$Bucket/$Prefix".TrimEnd("/")

Write-Host "Uploading Site Reality assets to $s3Base"

Sync-Asset -Source (Join-Path $compare "pointcloud_3dtiles") -Dest "$s3Base/pointcloud_3dtiles"
Sync-Asset -Source (Join-Path $compare "models") -Dest "$s3Base/models"
Sync-Asset -Source (Join-Path $compare "drone_log") -Dest "$s3Base/drone_log"

Write-Host ""
Write-Host "Set this on your deployed app:"
Write-Host "SITE_REALITY_ASSET_BASE=https://$Bucket.s3.amazonaws.com/$Prefix"
Write-Host ""
Write-Host "If the bucket is private, put CloudFront in front of it and use the CloudFront URL instead."
