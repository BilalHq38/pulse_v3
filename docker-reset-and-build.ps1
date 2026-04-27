<#
.SYNOPSIS
  Wipes Docker Compose data volumes (Postgres + Redis) and rebuilds all application images.

.DESCRIPTION
  1. docker compose down -v  → removes containers and named volumes (all DB rows + Redis AOF/data).
  2. docker compose pull     → refreshes public images used by compose (postgres, redis) when -PullBases is set.
  3. docker compose build    → builds every service with a build: section (auth, user, gateway, frontend, …).

  Requires Docker Desktop (or Engine) running. Run from the repository root.

.PARAMETER SkipPullBases
  Skip `docker compose pull` for postgres/redis images.

.PARAMETER NoCache
  Pass --no-cache to `docker compose build` (slower, fully clean rebuild).
#>

param(
  [switch]$SkipPullBases,
  [switch]$NoCache
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

function Resolve-DockerExe {
  if ($env:DOCKER_CLI_PATH -and (Test-Path -LiteralPath $env:DOCKER_CLI_PATH)) {
    return $env:DOCKER_CLI_PATH
  }
  $candidates = @(
    (Get-Command docker -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "${env:ProgramFiles}\Docker\Docker\resources\bin\docker.exe",
    "${env:ProgramFiles}\Docker\Docker\Docker\resources\bin\docker.exe"
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  if ($candidates.Count -eq 0) {
    throw "Docker CLI not found. Install Docker Desktop or set DOCKER_CLI_PATH to docker.exe."
  }
  return [string]$candidates[0]
}

$docker = Resolve-DockerExe
Write-Host "Using Docker: $docker" -ForegroundColor Gray

Write-Host @"

WARNING: This will DELETE all PostgreSQL and Redis data in this project’s Compose volumes
         (postgres-data, redis-data). Application images will be rebuilt.

"@ -ForegroundColor Yellow

& $docker compose version | Out-Host

Write-Host "`n==> docker compose down -v" -ForegroundColor Cyan
& $docker compose down -v

if (-not $SkipPullBases) {
  Write-Host "`n==> docker compose pull (postgres, redis, …)" -ForegroundColor Cyan
  & $docker compose pull
}

$buildArgs = @("compose", "build")
if ($NoCache) {
  $buildArgs += "--no-cache"
}
Write-Host "`n==> docker compose build" -ForegroundColor Cyan
& $docker @buildArgs

Write-Host @"

Done.
  Next: docker compose up -d
  Postgres will re-initialize from backend/sql_schema.sql and bootstrap_microservices.sql on first start.

"@ -ForegroundColor Green
