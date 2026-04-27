<#
.SYNOPSIS
  Starts Pulse Engine locally (without Docker): database, backends, and React frontend.

.DESCRIPTION
  - Initializes a workspace-local PostgreSQL cluster in .pg-local/data when needed.
  - Starts PostgreSQL on port 5433 in its own terminal window.
  - Optionally starts Redis on 127.0.0.1:6379 via Docker (matches docker-compose URL layout).
  - Applies bootstrap SQL files for microservice schemas.
  - Starts every backend uvicorn service (API gateway + microservices) in separate terminals.
  - Starts the frontend (npm start) in a separate terminal.
  - Optionally probes /health endpoints after a short delay.
#>

param(
  [string]$PostgresBin = "C:\Program Files\PostgreSQL\15\bin",
  [switch]$SkipDbBootstrap,
  [switch]$SkipFrontend,
  [switch]$SkipRedis,
  [switch]$SkipHealthCheck,
  [int]$HealthWaitSeconds = 18,
  [int]$HealthRetryRounds = 3,
  [int]$HealthRetrySleepSeconds = 4,
  [int]$FrontendHealthRetryRounds = 12
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

function Write-Step([string]$Message) {
  Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Info([string]$Message) {
  Write-Host "    $Message" -ForegroundColor Gray
}

function Write-Warn([string]$Message) {
  Write-Host "    $Message" -ForegroundColor Yellow
}

function Write-Success([string]$Message) {
  Write-Host "    $Message" -ForegroundColor Green
}

function ConvertTo-SingleQuotedText([string]$Value) {
  if ($null -eq $Value) { return "" }
  return $Value -replace "'", "''"
}

function Read-DotEnv([string]$Path) {
  $map = @{}
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = [string]$line
    if (-not $trimmed) { continue }
    $trimmed = $trimmed.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) { continue }

    $key = $trimmed.Substring(0, $idx).Trim()
    $value = $trimmed.Substring($idx + 1).Trim()

    if ((($value.StartsWith('"')) -and $value.EndsWith('"')) -or (($value.StartsWith("'")) -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    if ($key) { $map[$key] = $value }
  }
  return $map
}

function Test-PortOpen([int]$Port) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(400)) {
      return $false
    }
    $null = $client.EndConnect($iar)
    return $true
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds = 45) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    if (Test-PortOpen -Port $Port) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Wait-PostgresAcceptingConnections {
  param(
    [string]$PgBin,
    [string]$DbHost,
    [int]$DbPort,
    [string]$DbUser,
    [string]$DbPassword,
    [int]$TimeoutSeconds = 90
  )
  $psqlExe = Join-Path $PgBin "psql.exe"
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    $env:PGPASSWORD = $DbPassword
    # Use Start-Process so transient FATAL "database system is starting up" on stderr
    # does not terminate the script under $ErrorActionPreference = Stop.
    $probeSql = "SELECT 1"
    $p = Start-Process -FilePath $psqlExe -ArgumentList @(
      "-h", $DbHost, "-p", ([string]$DbPort), "-U", $DbUser, "-d", "postgres",
      "-v", "ON_ERROR_STOP=1", "-c", $probeSql
    ) -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -eq 0) {
      Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
      return $true
    }
    Start-Sleep -Milliseconds 400
  }
  Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
  return $false
}

function Test-HttpOk([string]$Url, [int]$TimeoutSec = 5) {
  try {
    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.Method = "GET"
    $req.Timeout = $TimeoutSec * 1000
    $resp = $req.GetResponse()
    $code = [int]$resp.StatusCode
    $resp.Dispose()
    return ($code -ge 200 -and $code -lt 300)
  } catch {
    return $false
  }
}

function Resolve-LocalRedisUrls() {
  if ($SkipRedis) {
    return @{
      RedisBase = ""
      BackgroundEnabled = "false"
    }
  }
  if (Test-PortOpen -Port 6379) {
    Write-Info "Redis already listening on 127.0.0.1:6379"
    return @{
      RedisBase = "redis://127.0.0.1:6379"
      BackgroundEnabled = "true"
    }
  }
  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    Write-Warn "Docker not found; Redis not started. Set REDIS_* in .env or install Docker for full parity."
    return @{
      RedisBase = ""
      BackgroundEnabled = "false"
    }
  }
  $containerName = "pulse-engine-local-redis"
  Write-Step "Starting Redis ($containerName on port 6379)"
  docker start $containerName 2>$null | Out-Null
  $running = docker ps --filter "name=$containerName" --format "{{.Names}}" 2>$null
  if (-not $running) {
    docker run -d --restart unless-stopped --name $containerName -p "6379:6379" redis:7-alpine 2>$null | Out-Null
  }
  if (-not (Wait-Port -Port 6379 -TimeoutSeconds 25)) {
    Write-Warn "Redis did not open port 6379; continuing with queue/cache fallbacks."
    return @{
      RedisBase = ""
      BackgroundEnabled = "false"
    }
  }
  return @{
    RedisBase = "redis://127.0.0.1:6379"
    BackgroundEnabled = "true"
  }
}

function Start-Terminal([string]$Title, [string]$WorkingDirectory, [string]$CommandText) {
  $titleEsc = ConvertTo-SingleQuotedText $Title
  $wdEsc = ConvertTo-SingleQuotedText $WorkingDirectory
  $script = "`$Host.UI.RawUI.WindowTitle = '$titleEsc'; Set-Location '$wdEsc'; $CommandText"

  Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $script
  ) | Out-Null
}

$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$envPath = Join-Path $root ".env"
$pythonExe = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $envPath)) {
  throw "Missing .env at $envPath. Run .\setup.ps1 first."
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
  throw "Missing Python runtime at $pythonExe. Create the root virtual environment first."
}
if (-not (Test-Path -LiteralPath $backendDir)) {
  throw "Backend folder not found at $backendDir"
}
if ((-not $SkipFrontend) -and (-not (Test-Path -LiteralPath $frontendDir))) {
  throw "Frontend folder not found at $frontendDir"
}

$dotenv = Read-DotEnv -Path $envPath
$dbUser = if ($dotenv.ContainsKey("POSTGRES_USER") -and $dotenv["POSTGRES_USER"]) { $dotenv["POSTGRES_USER"] } else { "pulse_engine_app" }
$dbPassword = if ($dotenv.ContainsKey("POSTGRES_PASSWORD")) { $dotenv["POSTGRES_PASSWORD"] } else { "" }
$dbName = if ($dotenv.ContainsKey("POSTGRES_DB") -and $dotenv["POSTGRES_DB"]) { $dotenv["POSTGRES_DB"] } else { "pulse_engine" }
$dbHost = "127.0.0.1"
$dbPort = 5433

if (-not $dbPassword) {
  throw "POSTGRES_PASSWORD is missing in .env."
}

foreach ($exe in @("initdb.exe", "postgres.exe", "psql.exe", "createdb.exe")) {
  $path = Join-Path $PostgresBin $exe
  if (-not (Test-Path -LiteralPath $path)) {
    throw "PostgreSQL executable not found: $path"
  }
}

$pgRoot = Join-Path $root ".pg-local"
$pgData = Join-Path $pgRoot "data"
if (-not (Test-Path -LiteralPath $pgRoot)) {
  New-Item -ItemType Directory -Path $pgRoot -Force | Out-Null
}

$pgVersionFile = Join-Path $pgData "PG_VERSION"
if (-not (Test-Path -LiteralPath $pgVersionFile)) {
  Write-Step "Initializing local PostgreSQL cluster (.pg-local/data)"
  New-Item -ItemType Directory -Path $pgData -Force | Out-Null

  $pwFile = Join-Path $pgRoot "postgres.pw"
  Set-Content -LiteralPath $pwFile -Value $dbPassword -NoNewline
  try {
    & (Join-Path $PostgresBin "initdb.exe") -D $pgData -U $dbUser -A scram-sha-256 --pwfile=$pwFile --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) {
      throw "initdb failed with exit code $LASTEXITCODE"
    }
  } finally {
    Remove-Item -LiteralPath $pwFile -Force -ErrorAction SilentlyContinue
  }
}

if (-not (Test-PortOpen -Port $dbPort)) {
  Write-Step ("Starting local PostgreSQL on {0}:{1}" -f $dbHost, $dbPort)
  $postgresExe = ConvertTo-SingleQuotedText (Join-Path $PostgresBin "postgres.exe")
  $pgDataEsc = ConvertTo-SingleQuotedText $pgData
  Start-Terminal -Title "pulse-postgres:$dbPort" -WorkingDirectory $root -CommandText "& '$postgresExe' -D '$pgDataEsc' -p $dbPort"

  if (-not (Wait-Port -Port $dbPort -TimeoutSeconds 45)) {
    throw "PostgreSQL did not open port $dbPort in time."
  }
} else {
  Write-Info ("PostgreSQL already listening on {0}:{1}" -f $dbHost, $dbPort)
}

Write-Step "Waiting for PostgreSQL to accept connections"
if (-not (Wait-PostgresAcceptingConnections -PgBin $PostgresBin -DbHost $dbHost -DbPort $dbPort -DbUser $dbUser -DbPassword $dbPassword)) {
  throw "PostgreSQL did not accept connections in time (after TCP bind). Try again or check the pulse-postgres terminal."
}

if (-not $SkipDbBootstrap) {
  Write-Step "Bootstrapping database schemas"
  $psqlExe = Join-Path $PostgresBin "psql.exe"
  $createdbExe = Join-Path $PostgresBin "createdb.exe"
  $env:PGPASSWORD = $dbPassword

  $dbNameEscSql = $dbName.Replace("'", "''")
  $existsRow = (& $psqlExe -h $dbHost -p $dbPort -U $dbUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$dbNameEscSql'" 2>&1 | Out-String).Trim()
  if ($existsRow -ne '1') {
    & $createdbExe -h $dbHost -p $dbPort -U $dbUser $dbName 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "createdb failed with exit code $LASTEXITCODE"
    }
  }

  $bootstrapFiles = @(
    (Join-Path $backendDir "shared\db\bootstrap_microservices.sql"),
    (Join-Path $backendDir "sql_schema.sql")
  )

  foreach ($file in $bootstrapFiles) {
    if (-not (Test-Path -LiteralPath $file)) {
      throw "Bootstrap file not found: $file"
    }
    Write-Info "Applying $(Split-Path -Leaf $file)"
    & $psqlExe -h $dbHost -p $dbPort -U $dbUser -d $dbName -v ON_ERROR_STOP=1 -f $file
    if ($LASTEXITCODE -ne 0) {
      throw "psql failed for $file with exit code $LASTEXITCODE"
    }
  }

  Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
} else {
  Write-Info "Skipping DB bootstrap (-SkipDbBootstrap provided)"
}

$databaseUrl = "postgresql://{0}:{1}@{2}:{3}/{4}" -f $dbUser, $dbPassword, $dbHost, $dbPort, $dbName
$pythonExeEsc = ConvertTo-SingleQuotedText $pythonExe
$dbUserEsc = ConvertTo-SingleQuotedText $dbUser
$dbPasswordEsc = ConvertTo-SingleQuotedText $dbPassword
$dbNameEsc = ConvertTo-SingleQuotedText $dbName
$databaseUrlEsc = ConvertTo-SingleQuotedText $databaseUrl

$redisInfo = Resolve-LocalRedisUrls
$redisBase = [string]$redisInfo.RedisBase
$bgEnabled = [string]$redisInfo.BackgroundEnabled

$commonEnv = [System.Collections.Generic.List[string]]::new()
$commonEnv.Add("`$env:DATABASE_URL = '$databaseUrlEsc'")
$commonEnv.Add("`$env:POSTGRES_HOST = '$dbHost'")
$commonEnv.Add("`$env:POSTGRES_PORT = '$dbPort'")
$commonEnv.Add("`$env:POSTGRES_USER = '$dbUserEsc'")
$commonEnv.Add("`$env:POSTGRES_PASSWORD = '$dbPasswordEsc'")
$commonEnv.Add("`$env:POSTGRES_DB = '$dbNameEsc'")
$commonEnv.Add("`$env:AUTH_SERVICE_URL = 'http://127.0.0.1:8001'")
$commonEnv.Add("`$env:USER_SERVICE_URL = 'http://127.0.0.1:8002'")
$commonEnv.Add("`$env:CUSTOMER_SERVICE_URL = 'http://127.0.0.1:8003'")
$commonEnv.Add("`$env:LEAD_SERVICE_URL = 'http://127.0.0.1:8004'")
$commonEnv.Add("`$env:AI_SERVICE_URL = 'http://127.0.0.1:8005'")
$commonEnv.Add("`$env:ANALYTICS_SERVICE_URL = 'http://127.0.0.1:8006'")
$commonEnv.Add("`$env:PRODUCT_SERVICE_URL = 'http://127.0.0.1:8007'")
$commonEnv.Add("`$env:NOTIFICATION_SERVICE_URL = 'http://127.0.0.1:8008'")
$commonEnv.Add("`$env:ORCHESTRATOR_SERVICE_URL = 'http://127.0.0.1:8009'")
$commonEnv.Add("`$env:IDENTITY_SERVICE_URL = 'http://127.0.0.1:8010'")
$commonEnv.Add("`$env:SUPER_ADMIN_SERVICE_URL = 'http://127.0.0.1:8011'")
$commonEnv.Add("`$env:DATA_PIPELINE_SERVICE_URL = 'http://127.0.0.1:8012'")

if ($redisBase) {
  $commonEnv.Add("`$env:REDIS_URL = '$redisBase/0'")
  $commonEnv.Add("`$env:CACHE_REDIS_URL = '$redisBase/1'")
  $commonEnv.Add("`$env:RATE_LIMIT_REDIS_URL = '$redisBase/2'")
  $commonEnv.Add("`$env:BACKGROUND_QUEUE_URL = '$redisBase/3'")
  $commonEnv.Add("`$env:BACKGROUND_QUEUE_ENABLED = '$bgEnabled'")
} else {
  $commonEnv.Add("`$env:REDIS_URL = 'disabled'")
  $commonEnv.Add("`$env:CACHE_REDIS_URL = 'disabled'")
  $commonEnv.Add("`$env:RATE_LIMIT_REDIS_URL = 'disabled'")
  $commonEnv.Add("`$env:BACKGROUND_QUEUE_URL = 'disabled'")
  $commonEnv.Add("`$env:BACKGROUND_QUEUE_ENABLED = 'false'")
}
$commonEnvBlock = ($commonEnv.ToArray() -join "; ")

$services = @(
  @{ Name = "auth"; Module = "services.auth_service.main:app"; Port = 8001; ServiceName = "auth-service"; Schema = "auth_service" },
  @{ Name = "user"; Module = "services.user_service.main:app"; Port = 8002; ServiceName = "user-service"; Schema = "user_service" },
  @{ Name = "customer"; Module = "services.customer_service.main:app"; Port = 8003; ServiceName = "customer-service"; Schema = "customer_service" },
  @{ Name = "lead"; Module = "services.lead_service.main:app"; Port = 8004; ServiceName = "lead-service"; Schema = "lead_service" },
  @{ Name = "ai"; Module = "services.ai_service.main:app"; Port = 8005; ServiceName = "ai-service"; Schema = "ai_service" },
  @{ Name = "analytics"; Module = "services.analytics_service.main:app"; Port = 8006; ServiceName = "analytics-service"; Schema = "analytics_service" },
  @{ Name = "product"; Module = "services.product_service.main:app"; Port = 8007; ServiceName = "product-service"; Schema = "product_service" },
  @{ Name = "notification"; Module = "services.notification_service.main:app"; Port = 8008; ServiceName = "notification-service"; Schema = "notification_service" },
  @{ Name = "agent-orchestrator"; Module = "services.agent_orchestrator.main:app"; Port = 8009; ServiceName = "agent-orchestrator-service"; Schema = "agent_orchestrator" },
  @{ Name = "identity"; Module = "services.identity_service.main:app"; Port = 8010; ServiceName = "identity-service"; Schema = "identity_service" },
  @{ Name = "super-admin"; Module = "services.super_admin_service.main:app"; Port = 8011; ServiceName = "super-admin-service"; Schema = "super_admin_service" },
  @{ Name = "data-pipeline"; Module = "services.data_pipeline_service.main:app"; Port = 8012; ServiceName = "data-pipeline"; Schema = "analytics_service" },
  @{ Name = "gateway"; Module = "api_gateway.main:app"; Port = 8000; ServiceName = "api-gateway"; Schema = "" }
)

Write-Step "Starting backend services"
foreach ($svc in $services) {
  if (Test-PortOpen -Port ([int]$svc.Port)) {
    Write-Warn "Skipping $($svc.Name): port $($svc.Port) is already in use"
    continue
  }

  $serviceNameEsc = ConvertTo-SingleQuotedText ([string]$svc.ServiceName)
  $schemaCmd = if ([string]$svc.Schema) {
    $schemaEsc = ConvertTo-SingleQuotedText ([string]$svc.Schema)
    "`$env:DB_SCHEMA = '$schemaEsc'"
  } else {
    "Remove-Item Env:DB_SCHEMA -ErrorAction SilentlyContinue"
  }

  $cmd = "$commonEnvBlock; `$env:SERVICE_NAME = '$serviceNameEsc'; `$env:PORT = '$($svc.Port)'; $schemaCmd; & '$pythonExeEsc' -m uvicorn $($svc.Module) --host 127.0.0.1 --port $($svc.Port)"
  Start-Terminal -Title "pulse-$($svc.Name):$($svc.Port)" -WorkingDirectory $backendDir -CommandText $cmd
}

if (-not $SkipFrontend) {
  Write-Step "Starting frontend (React dev server on port 3000)"
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm not found on PATH. Install Node.js LTS so the frontend can run."
  }
  $nodeModules = Join-Path $frontendDir "node_modules"
  if (-not (Test-Path -LiteralPath $nodeModules)) {
    Write-Step "Installing frontend dependencies (npm install)"
    Push-Location $frontendDir
    try {
      npm install
      if ($LASTEXITCODE -ne 0) {
        throw "npm install failed with exit code $LASTEXITCODE"
      }
    } finally {
      Pop-Location
    }
  }
  if (Test-PortOpen -Port 3000) {
    Write-Warn "Skipping frontend: port 3000 is already in use"
  } else {
    # Align with frontend/.env.development; explicit env ensures the spawned shell hits the gateway.
    # Do not force 127.0.0.1 here: browsers treat localhost vs 127.0.0.1 as different sites, so
    # SameSite=Lax refresh cookies from the API would not attach to XHR from http://localhost:3000.
    # frontend/.env.development uses http://localhost:8000; keep the same unless you override elsewhere.
    $frontendCmd = "`$env:PORT = '3000'; npm start"
    Start-Terminal -Title "pulse-frontend:3000" -WorkingDirectory $frontendDir -CommandText $frontendCmd
    Write-Info "Frontend window: pulse-frontend:3000 (npm start)"
  }
} else {
  Write-Info "Skipping frontend (-SkipFrontend provided)"
}

Write-Host ""
Write-Host "Pulse Engine local stack launch triggered." -ForegroundColor Green
Write-Host "Gateway:  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Frontend: http://127.0.0.1:3000" -ForegroundColor Green
Write-Host ("Postgres: postgresql://{0}@{1}:{2}/{3}" -f $dbUser, $dbHost, $dbPort, $dbName) -ForegroundColor Green
if ($redisBase) {
  Write-Host "Redis:    $redisBase (logical DB 0-3: main, cache, rate-limit, queue)" -ForegroundColor Green
} else {
  Write-Host "Redis:    not running (using in-memory fallbacks where applicable)" -ForegroundColor Yellow
}
Write-Host ""

if (-not $SkipHealthCheck) {
  Write-Step ("Waiting {0}s for processes to bind ports..." -f $HealthWaitSeconds)
  Start-Sleep -Seconds $HealthWaitSeconds
  $healthChecks = @(
    @{ Name = "gateway"; Url = "http://127.0.0.1:8000/health" },
    @{ Name = "auth"; Url = "http://127.0.0.1:8001/health" },
    @{ Name = "user"; Url = "http://127.0.0.1:8002/health" },
    @{ Name = "customer"; Url = "http://127.0.0.1:8003/health" },
    @{ Name = "lead"; Url = "http://127.0.0.1:8004/health" },
    @{ Name = "ai"; Url = "http://127.0.0.1:8005/health" },
    @{ Name = "analytics"; Url = "http://127.0.0.1:8006/health" },
    @{ Name = "product"; Url = "http://127.0.0.1:8007/health" },
    @{ Name = "notification"; Url = "http://127.0.0.1:8008/health" },
    @{ Name = "agent-orchestrator"; Url = "http://127.0.0.1:8009/health" },
    @{ Name = "identity"; Url = "http://127.0.0.1:8010/api/health" },
    @{ Name = "super-admin"; Url = "http://127.0.0.1:8011/health" },
    @{ Name = "data-pipeline"; Url = "http://127.0.0.1:8012/health" }
  )
  if (-not $SkipFrontend) {
    $healthChecks = $healthChecks + @(
      @{ Name = "frontend"; Url = "http://127.0.0.1:3000" }
    )
  }
  Write-Step "Health checks (HTTP)"
  foreach ($hc in $healthChecks) {
    $ok = $false
    $portHint = 0
    if ($hc.Url -match '127\.0\.0\.1:(\d+)') { $portHint = [int]$Matches[1] }
    $maxRounds = [Math]::Max(1, $HealthRetryRounds)
    if ($hc['Name'] -eq 'frontend') {
      $maxRounds = [Math]::Max($HealthRetryRounds, $FrontendHealthRetryRounds)
    }
    if ($portHint -gt 0 -and -not (Test-PortOpen -Port $portHint)) {
      $maxRounds = 1
    }
    for ($r = 0; $r -lt $maxRounds; $r++) {
      if (Test-HttpOk -Url $hc.Url) { $ok = $true; break }
      if ($r -lt ($maxRounds - 1)) {
        Start-Sleep -Seconds $HealthRetrySleepSeconds
      }
    }
    if ($ok) {
      Write-Success ("  OK  {0,-22} {1}" -f $hc.Name, $hc.Url)
    } else {
      Write-Warn ("  --  {0,-22} {1} (not ready or port skipped)" -f $hc.Name, $hc.Url)
    }
  }
}

Write-Host "To stop services, close the spawned terminal windows." -ForegroundColor Yellow
