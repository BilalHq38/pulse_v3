param(
  [string]$Project = "pulse-v3",
  [int]$Tail = 80,
  [string]$ComposeFile = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-ComposeArgs {
  $argsList = @()
  if ($ComposeFile.Trim()) {
    $argsList += @("-f", $ComposeFile.Trim())
  }
  $argsList += @("-p", $Project)
  return $argsList
}

function Get-Services {
  $composeArgs = Get-ComposeArgs
  $services = & docker compose @composeArgs config --services 2>$null
  if (-not $services) {
    throw "No services found. Make sure you're in the repo root and docker compose works."
  }
  return ($services | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Start-CmdLogWindow([string]$ServiceName) {
  $composeArgs = Get-ComposeArgs
  $composeArgText = ($composeArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
  $cmd = "docker compose $composeArgText logs -f --tail=$Tail $ServiceName"

  # Open a dedicated CMD window that keeps running.
  Start-Process -FilePath "cmd.exe" -ArgumentList @(
    "/k",
    "title $($Project):$ServiceName && $cmd"
  ) | Out-Null
}

try {
  $root = Split-Path -Parent $MyInvocation.MyCommand.Path
  if ($root) { Set-Location $root }

  $services = Get-Services
  Write-Host "Starting CMD log windows for $($services.Count) services (project: $Project, tail: $Tail)..." -ForegroundColor Cyan

  foreach ($svc in $services) {
    Start-CmdLogWindow -ServiceName $svc
    Start-Sleep -Milliseconds 120
  }

  Write-Host "Done. Close the CMD windows to stop tailing logs." -ForegroundColor Green
} catch {
  Write-Host $_.Exception.Message -ForegroundColor Red
  exit 1
}

