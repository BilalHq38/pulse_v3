# Fast Docker image build: BuildKit + parallel service builds. Dockerfiles use cache mounts
# (pip, npm) so repeat builds are much faster.
# Usage (from repo root): .\scripts\docker-build-fast.ps1
# With services:         .\scripts\docker-build-fast.ps1 -- frontend

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$env:DOCKER_BUILDKIT = "1"
$env:COMPOSE_DOCKER_CLI_BUILD = "1"
if (-not $env:COMPOSE_BAKE) { $env:COMPOSE_BAKE = "true" }

docker compose build --parallel @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Build finished OK. Use: docker compose up -d" -ForegroundColor Green
