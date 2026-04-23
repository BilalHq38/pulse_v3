# Pulse Engine - Lean Startup Script
# Usage: .\start.ps1 [-Rebuild] [-FollowLogs] [-FullRebuild] [-FullRebuildNoCache] [-SkipBaseImagePull]
#
# Starts the full Docker Compose stack in detached mode and waits for the
# API gateway + frontend to come online. Assumes .\setup.ps1 has already
# generated .env and validated local pre-requisites.
#
# -FullRebuild: docker compose down (with volumes), prune dangling images, optional base-image pull, rebuild, then up (destructive to DB volume).

param(
    [switch]$Rebuild,
    [switch]$FollowLogs,
    [switch]$FullRebuild,
    [switch]$FullRebuildNoCache,
    [switch]$SkipBaseImagePull
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

$envPath = Join-Path $root ".env"
$composeFile = Join-Path $root "docker-compose.yml"
$projectName = "pulse-v3"

function Write-Step    { param([string]$m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Info    { param([string]$m) Write-Host "    $m" -ForegroundColor Gray }
function Write-Warn    { param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }

function Get-DockerCmd {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        throw "Docker not found in PATH. Install Docker Desktop and retry."
    }
    return $docker.Source
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $docker = Get-DockerCmd
    # Docker writes progress to stderr; do not merge into error stream (would trip $ErrorActionPreference = Stop).
    # Pipe stdout only to host so return value stays a single integer exit code.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $docker "compose" "-p" $projectName "-f" $composeFile "--env-file" $envPath @Arguments | Out-Host
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    return $exitCode
}

function Wait-HttpReady {
    param(
        [string]$Name,
        [string]$Url,
        [int]$Attempts = 30,
        [int]$SleepSeconds = 3
    )

    Write-Info "Waiting for $Name ($Url)..."
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Success "$Name is ready."
                return $true
            }
        } catch {}
        Start-Sleep -Seconds $SleepSeconds
    }

    Write-Warn "$Name did not respond within $([int]($Attempts * $SleepSeconds))s. Check 'docker compose logs'."
    return $false
}

Write-Step "Performing preflight checks"
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Environment file (.env) not found. Run .\setup.ps1 first."
}
if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "docker-compose.yml not found at $composeFile."
}

$docker = Get-DockerCmd
& $docker version 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not responding. Start Docker Desktop and retry."
}
Write-Success "Prerequisites met."

if ($FullRebuild) {
    Write-Step "Full rebuild (containers and Compose volumes removed; local DB volume will be recreated)"
    if ((Invoke-Compose -Arguments @("down", "--remove-orphans", "--volumes")) -ne 0) {
        throw "docker compose down failed during full rebuild."
    }
    Write-Info "Pruning dangling images..."
    & $docker image prune -f 1>$null 2>$null

    $svcImages = & $docker "compose" "-p" $projectName "-f" $composeFile "--env-file" $envPath "images" "-q" 2>$null
    if ($svcImages) {
        $svcImages | ForEach-Object {
            $id = ($_ | Out-String).Trim()
            if ($id) { & $docker rmi -f $id 1>$null 2>$null }
        }
    }

    if (-not $SkipBaseImagePull) {
        Write-Step "Pulling base images"
        foreach ($img in @("pgvector/pgvector:pg16", "redis:7-alpine", "node:20-alpine", "python:3.12-slim")) {
            Write-Info "Pulling $img ..."
            & $docker pull $img | Out-Host
        }
    }

    $buildArgs = @("build", "--parallel")
    if ($FullRebuildNoCache) { $buildArgs += "--no-cache" }
    if ((Invoke-Compose -Arguments $buildArgs) -ne 0) {
        throw "docker compose build failed during full rebuild."
    }
    Write-Success "Images rebuilt."
} else {
    Write-Step "Ensuring clean state"
    (Invoke-Compose -Arguments @("stop")) | Out-Null
    Write-Success "Old service instances stopped."
}

Write-Step "Starting infrastructure (Postgres, Redis)"
if ((Invoke-Compose -Arguments @("up", "-d", "postgres", "redis")) -ne 0) {
    throw "Failed to start infrastructure containers."
}

Write-Info "Waiting for Postgres health..."
$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    $status = & $docker inspect -f '{{.State.Health.Status}}' pulse-postgres-engine 2>$null
    if ($status -eq "healthy") { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if ($ready) {
    Write-Success "Postgres is healthy."
} else {
    Write-Warn "Postgres health check still pending; continuing."
}

Write-Step "Starting Pulse Engine stack"
# --wait avoids Compose returning non-zero while frontend (last service) is still "health: starting".
$upArgs = @("up", "-d", "--wait", "--wait-timeout", "600")
if ($Rebuild -and -not $FullRebuild) { $upArgs += "--build" }
$composeUpExit = Invoke-Compose -Arguments $upArgs
if ($composeUpExit -ne 0) {
    Write-Warn "docker compose up returned exit code $composeUpExit; verifying HTTP endpoints (Compose sometimes misreports on Windows)."
}
Write-Success "Docker stack is up (detached)."

$gwOk = Wait-HttpReady -Name "API Gateway" -Url "http://127.0.0.1:8000/api/healthz"
$feOk = Wait-HttpReady -Name "Frontend"    -Url "http://127.0.0.1:3000"
if (-not $gwOk -or -not $feOk) {
    throw "Services did not become ready (docker compose exit: $composeUpExit). Check: docker compose -p $projectName --env-file .env ps"
}

Write-Host ""
Write-Host "+-------------------------------------------------------------------+" -ForegroundColor Green
Write-Host "|                     Pulse Engine is running                       |" -ForegroundColor Green
Write-Host "+-------------------------------------------------------------------+" -ForegroundColor Green
Write-Host "| API Gateway          -> http://127.0.0.1:8000                    |" -ForegroundColor Green
Write-Host "| Frontend             -> http://127.0.0.1:3000                    |" -ForegroundColor Green
Write-Host "| Auth Service         -> internal :8001                           |" -ForegroundColor Green
Write-Host "| User Service         -> internal :8002                           |" -ForegroundColor Green
Write-Host "| Customer Service     -> internal :8003                           |" -ForegroundColor Green
Write-Host "| Lead Service         -> internal :8004                           |" -ForegroundColor Green
Write-Host "| AI Service           -> internal :8005                           |" -ForegroundColor Green
Write-Host "| Agent Orchestrator   -> internal :8009                           |" -ForegroundColor Green
Write-Host "| Analytics Service    -> internal :8006                           |" -ForegroundColor Green
Write-Host "| Data Pipeline        -> internal :8012                           |" -ForegroundColor Green
Write-Host "| Product Service      -> internal :8007                           |" -ForegroundColor Green
Write-Host "| Notification Service -> internal :8008                           |" -ForegroundColor Green
Write-Host "| Identity Service     -> internal :8010                           |" -ForegroundColor Green
Write-Host "| Super Admin Service  -> http://127.0.0.1:8011                    |" -ForegroundColor Green
Write-Host "+-------------------------------------------------------------------+" -ForegroundColor Green
Write-Host ""

Write-Success "Project is running in detached mode."
Write-Info "Logs:   docker compose -p $projectName --env-file .env logs -f gateway"
Write-Info "Stop:   docker compose -p $projectName --env-file .env down"
Write-Info "Widget: http://127.0.0.1:3000/widget-demo"

if ($FollowLogs) {
    Write-Step "Tailing gateway logs (Ctrl+C to detach)"
    (Invoke-Compose -Arguments @("logs", "-f", "--tail=100", "gateway")) | Out-Null
}
