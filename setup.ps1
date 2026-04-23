<#
.SYNOPSIS
    Bootstraps and starts the Pulse Engine stack in a secure, Docker-first way.

.DESCRIPTION
    - Repairs or creates local Python 3.10+ virtual environments (root + backend).
    - Generates a secure .env file when required values are missing and writes
      a sanitized .env.example next to it.
    - Uses a writable workspace-local Docker config to avoid Windows permission
      issues with the default %USERPROFILE%\.docker location.
    - Validates the Docker Compose configuration before attempting to bring it up.
    - Builds (when needed) and starts the full backend microservice stack plus
      the React frontend in detached mode.
    - Defaults WhatsApp delivery to Meta API with optional external bridge.
    - Optionally verifies the frontend production build so the UI is ready to
      serve locally.

.EXAMPLE
    .\setup.ps1

.EXAMPLE
    .\setup.ps1 -CheckOnly

.EXAMPLE
    .\setup.ps1 -SkipFrontendBuild -ForceRecreateVenv
#>

param(
    [switch]$CheckOnly,
    [switch]$SkipFrontendBuild,
    [switch]$ForceRecreateVenv
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$envPath = Join-Path $root ".env"
$envExamplePath = Join-Path $root ".env.example"
$dockerConfigDir = Join-Path $root ".docker"
$dockerConfigPath = Join-Path $dockerConfigDir "config.json"
$rootVenvDir = Join-Path $root ".venv"
$backendVenvDir = Join-Path $backendDir ".venv"
$workspaceTempDir = Join-Path $root ".tmp"
$workspaceVirtualenvAppData = Join-Path $workspaceTempDir "virtualenv-appdata"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Info([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Gray
}

function Write-Success([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Yellow
}

function Initialize-WorkspaceTempDirectory {
    foreach ($path in @($workspaceTempDir, $workspaceVirtualenvAppData)) {
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }
    $env:TEMP = $workspaceTempDir
    $env:TMP = $workspaceTempDir
}

function Test-PathInsideWorkspace {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CandidatePath
    )

    $resolvedWorkspace = [System.IO.Path]::GetFullPath($root).TrimEnd('\')
    $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath).TrimEnd('\')
    return $resolvedCandidate.StartsWith($resolvedWorkspace, [System.StringComparison]::OrdinalIgnoreCase)
}

function Remove-WorkspaceDirectorySafely {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath
    )

    if (-not (Test-Path $TargetPath)) {
        return
    }
    if (-not (Test-PathInsideWorkspace -CandidatePath $TargetPath)) {
        throw "Refusing to remove path outside workspace: $TargetPath"
    }

    $resolved = [System.IO.Path]::GetFullPath($TargetPath)
    Write-Warn "Removing corrupted directory: $resolved"
    Get-ChildItem -LiteralPath $resolved -Force -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            if ($_.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
                $_.Attributes = ($_.Attributes -bxor [System.IO.FileAttributes]::ReadOnly)
            }
        } catch {
        }
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function ConvertTo-EnvValue([string]$Value) {
    if ($null -eq $Value) {
        return ""
    }
    return ($Value -replace "`r", "") -replace "`n", "\n"
}

function New-SecureToken {
    param(
        [int]$Bytes = 32
    )

    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buffer)
    } finally {
        $rng.Dispose()
    }
    $token = [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    return $token
}

function New-FernetKey {
    $buffer = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buffer)
    } finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
}

function Get-FileMap {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $map = [ordered]@{}
    if (-not (Test-Path $Path)) {
        return $map
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = [string]$rawLine
        if (-not $line) {
            continue
        }
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separatorIndex = $trimmed.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }
        $key = $trimmed.Substring(0, $separatorIndex).Trim()
        $value = $trimmed.Substring($separatorIndex + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($key) {
            $map[$key] = $value
        }
    }

    return $map
}

function Save-EnvMap {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Map,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $orderedKeys = @(
        "ENVIRONMENT",
        "APP_ENV",
        "FRONTEND_URL",
        "FRONTEND_BASE_URL",
        "APP_URL",
        "REACT_APP_BACKEND_URL",
        "CORS_ORIGINS",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DATABASE_URL",
        "REDIS_URL",
        "CACHE_REDIS_URL",
        "RATE_LIMIT_REDIS_URL",
        "BACKGROUND_QUEUE_URL",
        "BACKGROUND_QUEUE_ENABLED",
        "BACKGROUND_QUEUE_MAX_RETRIES",
        "BACKGROUND_QUEUE_VISIBILITY_TIMEOUT",
        "BACKGROUND_QUEUE_JOB_TIMEOUT_SECONDS",
        "BACKGROUND_QUEUE_JOB_TTL_SECONDS",
        "JWT_SECRET",
        "INTERNAL_SERVICE_SECRET",
        "META_CREDENTIALS_ENCRYPTION_KEY",
        "APP_ENCRYPTION_KEY",
        "DEFAULT_TENANT_ID",
        "DEFAULT_TENANT_API_KEY",
        "DEFAULT_TENANT_SALT",
        "TENANT_API_KEYS",
        "TENANT_SALTS",
        "IDENTITY_PUBLIC_TENANT",
        "IDENTITY_ADMIN_EMAIL",
        "IDENTITY_ADMIN_PASSWORD",
        "TENANT_RATE_LIMIT_PER_MINUTE",
        "WEBHOOK_VERIFY_TOKEN",
        "META_WEBHOOK_SECRET",
        "WHATSAPP_WEBHOOK_SECRET",
        "FACEBOOK_WEBHOOK_SECRET",
        "INSTAGRAM_WEBHOOK_SECRET",
        "WEB_CHAT_WEBHOOK_SECRET",
        "EXTERNAL_WEBHOOK_SECRET",
        "WHATSAPP_BRIDGE_SECRET",
        "BRIDGE_SECRET",
        "BRIDGE_PORT",
        "WHATSAPP_MODE",
        "WHATSAPP_BRIDGE_URL",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "PHONE_NUMBER_ID",
        "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "META_APP_ID",
        "META_APP_SECRET",
        "FACEBOOK_APP_ID",
        "FACEBOOK_APP_SECRET",
        "INSTAGRAM_APP_ID",
        "INSTAGRAM_APP_SECRET",
        "WEB_CHAT_WIDGET_KEY",
        "META_VERIFY_TOKEN",
        "META_API_VERSION",
        "META_WEBHOOK_IP_ALLOWLIST",
        "META_WEBHOOK_RATE_LIMIT_PER_MINUTE",
        "MY_WHATSAPP_NUMBER",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICE_FREE",
        "STRIPE_PRICE_PRO",
        "STRIPE_PRICE_ENTERPRISE",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AUTH_SERVICE_URL",
        "USER_SERVICE_URL",
        "CUSTOMER_SERVICE_URL",
        "LEAD_SERVICE_URL",
        "AI_SERVICE_URL",
        "ANALYTICS_SERVICE_URL",
        "PRODUCT_SERVICE_URL",
        "NOTIFICATION_SERVICE_URL",
        "IDENTITY_SERVICE_URL",
        "SUPER_ADMIN_SERVICE_URL"
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# Pulse Engine local environment")
    $lines.Add("# Generated by setup.ps1")
    $lines.Add("")

    foreach ($key in $orderedKeys) {
        if ($Map.Contains($key)) {
            $lines.Add("$key=$(ConvertTo-EnvValue -Value ([string]$Map[$key]))")
        }
    }

    foreach ($entry in $Map.GetEnumerator() | Sort-Object Key) {
        if ($orderedKeys -contains $entry.Key) {
            continue
        }
        $lines.Add("$($entry.Key)=$(ConvertTo-EnvValue -Value ([string]$entry.Value))")
    }

    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function Import-DotEnvToProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $map = Get-FileMap -Path $Path
    foreach ($entry in $map.GetEnumerator()) {
        Set-Item -Path ("Env:" + $entry.Key) -Value ([string]$entry.Value)
    }
}

function Test-PythonVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [string[]]$Arguments = @()
    )

    try {
        $versionText = & $Executable @($Arguments + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")) 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        $versionString = (($versionText | Out-String).Trim())
        if (-not $versionString) {
            return $null
        }
        $version = [Version]$versionString
        if ($version.Major -lt 3 -or ($version.Major -eq 3 -and $version.Minor -lt 10)) {
            return $null
        }
        return $version
    } catch {
        return $null
    }
}

function Get-SystemPythonSpec {
    $candidateSpecs = New-Object System.Collections.Generic.List[hashtable]
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $pyCommand = Get-Command py -ErrorAction SilentlyContinue

    foreach ($venvPath in @(
        (Join-Path $rootVenvDir "Scripts\python.exe"),
        (Join-Path $backendVenvDir "Scripts\python.exe")
    )) {
        if ($venvPath -and (Test-Path -LiteralPath $venvPath)) {
            $candidateSpecs.Add(@{ Path = $venvPath; Args = @() })
        }
    }

    if ($pyCommand) {
        foreach ($versionArg in @("-3.12", "-3.11", "-3.10", "-3")) {
            $candidateSpecs.Add(@{ Path = $pyCommand.Source; Args = @($versionArg) })
        }
    }

    if ($pythonCommand -and $pythonCommand.Source -notmatch "WindowsApps") {
        $candidateSpecs.Add(@{ Path = $pythonCommand.Source; Args = @() })
    }

    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:ProgramFiles\Python310\python.exe",
        "$env:ProgramFiles\PostgreSQL\15\pgAdmin 4\python\python.exe",
        "$env:ProgramFiles\PostgreSQL\16\pgAdmin 4\python\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($path in $commonPaths) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            $candidateSpecs.Add(@{ Path = $path; Args = @() })
        }
    }

    foreach ($candidate in $candidateSpecs) {
        $path = [string]$candidate.Path
        if (-not $path -or -not (Test-Path -LiteralPath $path)) {
            continue
        }
        if ($path -match "WindowsApps") {
            continue
        }
        $version = Test-PythonVersion -Executable $path -Arguments $candidate.Args
        if ($version) {
            return @{
                Path = $path
                Args = $candidate.Args
                Version = $version
            }
        }
    }

    return $null
}

function Test-VenvHealthy {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvDir
    )

    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        return $false
    }
    return [bool](Test-PythonVersion -Executable $venvPython)
}

function Invoke-PythonSpec {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Spec,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$PassthruOutput,
        [switch]$SuppressStderr
    )

    if ($PassthruOutput) {
        if ($SuppressStderr) {
            return & $Spec.Path @($Spec.Args + $Arguments) 2>$null
        }
        return & $Spec.Path @($Spec.Args + $Arguments)
    }
    if ($SuppressStderr) {
        & $Spec.Path @($Spec.Args + $Arguments) 2>$null
    } else {
        & $Spec.Path @($Spec.Args + $Arguments)
    }
    return $LASTEXITCODE
}

function Initialize-Venv {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$BasePython,
        [Parameter(Mandatory = $true)]
        [string]$VenvDir,
        [Parameter(Mandatory = $true)]
        [string]$DisplayName
    )

    $activeVenvDir = $VenvDir
    $venvPython = Join-Path $activeVenvDir "Scripts\python.exe"
    $needsRecreate = $ForceRecreateVenv

    if (Test-Path -LiteralPath $activeVenvDir) {
        if (-not (Test-VenvHealthy -VenvDir $activeVenvDir)) {
            $needsRecreate = $true
        }
    }

    if ($needsRecreate) {
        try {
            Remove-WorkspaceDirectorySafely -TargetPath $activeVenvDir
        } catch {
            $fallbackDir = "$VenvDir.rebuilt"
            Write-Warn "Could not remove $DisplayName virtual environment. Falling back to: $fallbackDir"
            $activeVenvDir = $fallbackDir
            $venvPython = Join-Path $activeVenvDir "Scripts\python.exe"
            if (Test-Path -LiteralPath $activeVenvDir) {
                try {
                    Remove-WorkspaceDirectorySafely -TargetPath $activeVenvDir
                } catch {
                    throw "Failed to clear both $VenvDir and fallback path $fallbackDir"
                }
            }
        }
    }

    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Info "Creating $DisplayName virtual environment..."
        $exitCode = Invoke-PythonSpec -Spec $BasePython -Arguments @("-m", "venv", $activeVenvDir) -SuppressStderr
        if ($exitCode -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            Write-Warn "python -m venv was unavailable. Retrying with virtualenv..."
            $exitCode = Invoke-PythonSpec -Spec $BasePython -Arguments @("-m", "virtualenv", "--app-data", $workspaceVirtualenvAppData, $activeVenvDir)
        }
        if ($exitCode -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw "Failed to create virtual environment: $DisplayName"
        }
    }

    if (-not (Test-VenvHealthy -VenvDir $activeVenvDir)) {
        throw "Virtual environment is still unhealthy after recreation: $DisplayName"
    }

    $venvSpec = @{
        Path = $venvPython
        Args = @()
        VenvDir = $activeVenvDir
    }

    Write-Info "Upgrading pip tooling in $DisplayName virtual environment..."
    $upgradeExit = Invoke-PythonSpec -Spec $venvSpec -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
    if ($upgradeExit -ne 0) {
        throw "Failed to prepare pip tooling in $DisplayName virtual environment"
    }

    return $venvSpec
}

function Initialize-EnvFile {
    Write-Step "Ensuring .env exists and is secure"

    $map = Get-FileMap -Path $envPath
    $appOrigin = "http://localhost:3000"
    $backendOrigin = "http://localhost:8000"
    $tenantId = if (($map["DEFAULT_TENANT_ID"] | Out-String).Trim()) { $map["DEFAULT_TENANT_ID"] } else { "demo_tenant" }
    $defaultTenantApiKey = if (($map["DEFAULT_TENANT_API_KEY"] | Out-String).Trim()) { $map["DEFAULT_TENANT_API_KEY"] } else { New-SecureToken -Bytes 24 }
    $defaultTenantSalt = if (($map["DEFAULT_TENANT_SALT"] | Out-String).Trim()) { $map["DEFAULT_TENANT_SALT"] } else { New-SecureToken -Bytes 24 }
    $postgresUser = if (($map["POSTGRES_USER"] | Out-String).Trim()) { $map["POSTGRES_USER"] } else { "pulse_engine_app" }
    $postgresPassword = if (($map["POSTGRES_PASSWORD"] | Out-String).Trim()) { $map["POSTGRES_PASSWORD"] } else { New-SecureToken -Bytes 24 }
    $postgresDb = if (($map["POSTGRES_DB"] | Out-String).Trim()) { $map["POSTGRES_DB"] } else { "pulse_engine" }

    $defaults = [ordered]@{
        ENVIRONMENT                    = "development"
        APP_ENV                        = "development"
        FRONTEND_URL                   = $appOrigin
        FRONTEND_BASE_URL              = $appOrigin
        APP_URL                        = $appOrigin
        REACT_APP_BACKEND_URL          = $backendOrigin
        CORS_ORIGINS                   = "http://localhost:3000,http://127.0.0.1:3000,https://app.pulse-engine.example"
        POSTGRES_HOST                  = "postgres"
        POSTGRES_PORT                  = "5432"
        POSTGRES_USER                  = $postgresUser
        POSTGRES_PASSWORD              = $postgresPassword
        POSTGRES_DB                    = $postgresDb
        DATABASE_URL                   = "postgresql://$postgresUser`:$postgresPassword@postgres:5432/$postgresDb"
        REDIS_URL                      = "redis://redis:6379/0"
        CACHE_REDIS_URL                = "redis://redis:6379/1"
        RATE_LIMIT_REDIS_URL           = "redis://redis:6379/2"
        BACKGROUND_QUEUE_URL           = "redis://redis:6379/3"
        BACKGROUND_QUEUE_ENABLED       = "true"
        BACKGROUND_QUEUE_MAX_RETRIES   = "5"
        BACKGROUND_QUEUE_VISIBILITY_TIMEOUT = "300"
        BACKGROUND_QUEUE_JOB_TIMEOUT_SECONDS = "600"
        BACKGROUND_QUEUE_JOB_TTL_SECONDS = "86400"
        JWT_SECRET                     = New-SecureToken -Bytes 48
        INTERNAL_SERVICE_SECRET        = New-SecureToken -Bytes 32
        META_CREDENTIALS_ENCRYPTION_KEY = New-FernetKey
        APP_ENCRYPTION_KEY             = New-SecureToken -Bytes 32
        DEFAULT_TENANT_ID              = $tenantId
        DEFAULT_TENANT_API_KEY         = $defaultTenantApiKey
        DEFAULT_TENANT_SALT            = $defaultTenantSalt
        TENANT_API_KEYS                = "$tenantId`:$defaultTenantApiKey"
        TENANT_SALTS                   = "$tenantId`:$defaultTenantSalt"
        IDENTITY_PUBLIC_TENANT         = "public_unification"
        IDENTITY_ADMIN_EMAIL           = "admin@pulse-engine.local"
        IDENTITY_ADMIN_PASSWORD        = New-SecureToken -Bytes 24
        TENANT_RATE_LIMIT_PER_MINUTE   = "1000"
        WEBHOOK_VERIFY_TOKEN           = New-SecureToken -Bytes 18
        META_WEBHOOK_SECRET            = New-SecureToken -Bytes 32
        WHATSAPP_WEBHOOK_SECRET        = New-SecureToken -Bytes 32
        FACEBOOK_WEBHOOK_SECRET        = New-SecureToken -Bytes 32
        INSTAGRAM_WEBHOOK_SECRET       = New-SecureToken -Bytes 32
        WEB_CHAT_WEBHOOK_SECRET        = New-SecureToken -Bytes 32
        EXTERNAL_WEBHOOK_SECRET        = New-SecureToken -Bytes 32
        WHATSAPP_BRIDGE_SECRET         = New-SecureToken -Bytes 24
        BRIDGE_SECRET                  = New-SecureToken -Bytes 24
        BRIDGE_PORT                    = "3001"
        WHATSAPP_MODE                  = "meta"
        WHATSAPP_BRIDGE_URL            = "http://host.docker.internal:3001"
        WHATSAPP_ACCESS_TOKEN          = "replace-with-meta-whatsapp-access-token"
        WHATSAPP_PHONE_NUMBER_ID       = "replace-with-whatsapp-phone-number-id"
        PHONE_NUMBER_ID                = "replace-with-whatsapp-phone-number-id"
        WHATSAPP_BUSINESS_ACCOUNT_ID   = "replace-with-whatsapp-business-account-id"
        META_APP_ID                    = "replace-with-meta-app-id"
        META_APP_SECRET                = "replace-with-meta-app-secret"
        FACEBOOK_APP_ID                = "replace-with-facebook-app-id"
        FACEBOOK_APP_SECRET            = "replace-with-facebook-app-secret"
        INSTAGRAM_APP_ID               = "replace-with-instagram-app-id"
        INSTAGRAM_APP_SECRET           = "replace-with-instagram-app-secret"
        WEB_CHAT_WIDGET_KEY            = ""
        META_VERIFY_TOKEN              = New-SecureToken -Bytes 18
        META_API_VERSION               = "v21.0"
        META_WEBHOOK_IP_ALLOWLIST      = ""
        META_WEBHOOK_RATE_LIMIT_PER_MINUTE = "120"
        MY_WHATSAPP_NUMBER             = ""
        STRIPE_SECRET_KEY              = "replace-with-stripe-secret-key"
        STRIPE_WEBHOOK_SECRET          = "replace-with-stripe-webhook-secret"
        STRIPE_PRICE_FREE              = "replace-with-stripe-price-id-free"
        STRIPE_PRICE_PRO               = "replace-with-stripe-price-id-pro"
        STRIPE_PRICE_ENTERPRISE        = "replace-with-stripe-price-id-enterprise"
        OPENAI_API_KEY                 = ""
        GEMINI_API_KEY                 = ""
        ANTHROPIC_API_KEY              = ""
        AUTH_SERVICE_URL               = "http://auth:8001"
        USER_SERVICE_URL               = "http://user:8002"
        CUSTOMER_SERVICE_URL           = "http://customer:8003"
        LEAD_SERVICE_URL               = "http://lead:8004"
        AI_SERVICE_URL                 = "http://ai:8005"
        ANALYTICS_SERVICE_URL          = "http://analytics:8006"
        PRODUCT_SERVICE_URL            = "http://product:8007"
        NOTIFICATION_SERVICE_URL       = "http://notification:8008"
        IDENTITY_SERVICE_URL           = "http://identity:8010"
        SUPER_ADMIN_SERVICE_URL        = "http://super-admin:8011"
    }

    foreach ($entry in $defaults.GetEnumerator()) {
        $current = ""
        if ($map.Contains($entry.Key)) {
            $current = ([string]$map[$entry.Key]).Trim()
        }
        if (-not $current) {
            $map[$entry.Key] = $entry.Value
            continue
        }
        if ($entry.Key -in @("DATABASE_URL")) {
            continue
        }
    }

    $map["DATABASE_URL"] = "postgresql://$($map["POSTGRES_USER"]):$($map["POSTGRES_PASSWORD"])@postgres:5432/$($map["POSTGRES_DB"])"
    $map["TENANT_API_KEYS"] = "$($map["DEFAULT_TENANT_ID"]):$($map["DEFAULT_TENANT_API_KEY"])"
    $map["TENANT_SALTS"] = "$($map["DEFAULT_TENANT_ID"]):$($map["DEFAULT_TENANT_SALT"])"
    if (-not (($map["FRONTEND_BASE_URL"] | Out-String).Trim())) {
        $map["FRONTEND_BASE_URL"] = ($map["FRONTEND_URL"] | Out-String).Trim()
    }
    if (-not (($map["APP_URL"] | Out-String).Trim())) {
        $map["APP_URL"] = ($map["FRONTEND_BASE_URL"] | Out-String).Trim()
    }
    if (-not (($map["REACT_APP_BACKEND_URL"] | Out-String).Trim())) {
        $map["REACT_APP_BACKEND_URL"] = $backendOrigin
    }
    if (-not (($map["WHATSAPP_BRIDGE_SECRET"] | Out-String).Trim())) {
        $legacyBridgeSecret = ($map["BRIDGE_SECRET"] | Out-String).Trim()
        $map["WHATSAPP_BRIDGE_SECRET"] = if ($legacyBridgeSecret) { $legacyBridgeSecret } else { New-SecureToken -Bytes 24 }
    }
    $map["BRIDGE_SECRET"] = ($map["WHATSAPP_BRIDGE_SECRET"] | Out-String).Trim()
    if (-not (($map["WHATSAPP_PHONE_NUMBER_ID"] | Out-String).Trim())) {
        $map["WHATSAPP_PHONE_NUMBER_ID"] = ($map["PHONE_NUMBER_ID"] | Out-String).Trim()
    }
    if (-not (($map["PHONE_NUMBER_ID"] | Out-String).Trim())) {
        $map["PHONE_NUMBER_ID"] = ($map["WHATSAPP_PHONE_NUMBER_ID"] | Out-String).Trim()
    }
    if (-not (($map["META_CREDENTIALS_ENCRYPTION_KEY"] | Out-String).Trim())) {
        $map["META_CREDENTIALS_ENCRYPTION_KEY"] = New-FernetKey
    }
    if (-not (($map["APP_ENCRYPTION_KEY"] | Out-String).Trim())) {
        $map["APP_ENCRYPTION_KEY"] = New-SecureToken -Bytes 32
    }

    Save-EnvMap -Map $map -Path $envPath

    if (-not (Test-Path -LiteralPath $envExamplePath)) {
        $exampleMap = [ordered]@{}
        foreach ($entry in $map.GetEnumerator()) {
            $value = [string]$entry.Value
            if ($entry.Key -match "SECRET|TOKEN|PASSWORD|KEY|SALT") {
                if (-not $value) {
                    $exampleMap[$entry.Key] = ""
                } elseif ($value -like "replace-with-*") {
                    $exampleMap[$entry.Key] = $value
                } else {
                    $exampleMap[$entry.Key] = "<set-me>"
                }
            } else {
                $exampleMap[$entry.Key] = $value
            }
        }
        Save-EnvMap -Map $exampleMap -Path $envExamplePath
    }

    Import-DotEnvToProcess -Path $envPath
    Write-Success "Environment file ready: $envPath"
}

function Initialize-LocalDockerConfig {
    Write-Step "Preparing Docker config"

    if (-not (Test-Path -LiteralPath $dockerConfigDir)) {
        New-Item -ItemType Directory -Path $dockerConfigDir | Out-Null
    }
    if (-not (Test-Path -LiteralPath $dockerConfigPath)) {
        [System.IO.File]::WriteAllText(
            $dockerConfigPath,
            "{}",
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    try {
        Get-Content -LiteralPath $dockerConfigPath -TotalCount 1 | Out-Null
    } catch {
        [System.IO.File]::WriteAllText(
            $dockerConfigPath,
            "{}",
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    $env:DOCKER_CONFIG = $dockerConfigDir
    Write-Success "Using workspace-local Docker config: $dockerConfigDir"
}

function Get-NodeCommand {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm.cmd was not found. Install Node.js 18+ and retry."
    }
    return $npm.Source
}

function Get-DockerCommand {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        throw "Docker was not found. Install Docker Desktop and retry."
    }
    return $docker.Source
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory=$true)]
        [string[]] $Arguments
    )

    $docker = Get-DockerCommand
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $docker "compose" "-p" "pulse-v3" "--env-file" $envPath @Arguments | Out-Host
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    return $exitCode
}

function Install-PythonDependencies {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$RootVenv,
        [Parameter(Mandatory = $true)]
        [hashtable]$BackendVenv
    )

    Write-Step "Installing local Python dependencies (optional; Docker containers build their own)"

    $requirementsPath = Join-Path $root "requirements.txt"
    $identityRequirementsPath = Join-Path $backendDir "services\identity_service\requirements.txt"
    $failures = 0

    if (-not (Test-Path -LiteralPath $requirementsPath)) {
        Write-Warn "requirements.txt not found at $requirementsPath; skipping local pip install."
        return
    }

    if ((Invoke-PythonSpec -Spec $RootVenv -Arguments @("-m", "pip", "install", "-r", $requirementsPath)) -ne 0) {
        Write-Warn "Failed to install root Python requirements (continuing; Docker stack will provision its own)."
        $failures++
    }
    if ((Invoke-PythonSpec -Spec $BackendVenv -Arguments @("-m", "pip", "install", "-r", $requirementsPath)) -ne 0) {
        Write-Warn "Failed to install backend Python requirements (continuing)."
        $failures++
    }
    if (Test-Path -LiteralPath $identityRequirementsPath) {
        if ((Invoke-PythonSpec -Spec $BackendVenv -Arguments @("-m", "pip", "install", "-r", $identityRequirementsPath)) -ne 0) {
            Write-Warn "Failed to install identity-service Python requirements (continuing)."
            $failures++
        }
    }

    if ($failures -eq 0) {
        Write-Success "Local Python environments are ready."
    } else {
        Write-Warn "Local Python dependency install had $failures non-fatal failure(s)."
    }
}

function Invoke-FrontendBuild {
    if ($SkipFrontendBuild) {
        Write-Warn "Frontend build skipped (-SkipFrontendBuild)."
        return
    }

    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "package.json"))) {
        Write-Warn "Frontend package.json not found; skipping local build."
        return
    }

    Write-Step "Verifying frontend build (Docker image will also build this)"
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) {
        Write-Warn "npm not found in PATH; skipping local frontend build. Docker image will still build the UI."
        return
    }

    Push-Location $frontendDir
    try {
        & $npm.Source install
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "npm install failed locally; continuing (Docker image will rebuild)."
            return
        }

        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "npm run build failed locally; continuing (Docker image will rebuild)."
            return
        }
    } finally {
        Pop-Location
    }

    Write-Success "Frontend build completed."
}

function Test-Compose {
    Write-Step "Validating Docker Compose configuration"
    if ((Invoke-Compose -Arguments @("config", "--quiet")) -ne 0) {
        throw "docker compose config validation failed."
    }
    Write-Success "Docker Compose configuration is valid."
}

function Start-ComposeStack {
    Write-Step "Starting Pulse Engine services"
    $composeUpExit = Invoke-Compose -Arguments @("up", "-d", "--build", "--wait", "--wait-timeout", "600")
    if ($composeUpExit -ne 0) {
        Write-Warn "docker compose up returned exit code $composeUpExit; probing gateway..."
        $gwOk = $false
        for ($i = 0; $i -lt 25; $i++) {
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/healthz" -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop
                if ($r.StatusCode -eq 200) { $gwOk = $true; break }
            } catch {}
            Start-Sleep -Seconds 3
        }
        if (-not $gwOk) {
            throw "docker compose up failed ($composeUpExit) and gateway did not respond."
        }
        Write-Warn "Gateway is healthy despite compose exit code; continuing."
    }
    Write-Success "Docker stack is starting in detached mode."
}

function Show-ComposeServices {
    Write-Step "Current Docker services"
    $docker = Get-DockerCommand
    & $docker "compose" "--env-file" $envPath "ps"
}

Write-Step "Resolving Python 3.10+ runtime"
Initialize-WorkspaceTempDirectory
$basePython = Get-SystemPythonSpec
if (-not $basePython) {
    throw "No usable Python 3.10+ runtime was found. Install Python 3.10+ from python.org and retry."
}
Write-Success "Using Python $($basePython.Version) via $($basePython.Path)"

Write-Step "Repairing local virtual environments"
$localPythonReady = $true
try {
    $rootVenv = Initialize-Venv -BasePython $basePython -VenvDir $rootVenvDir -DisplayName "repo root"
    $backendVenv = Initialize-Venv -BasePython $basePython -VenvDir $backendVenvDir -DisplayName "backend"
    Write-Success "Virtual environments repaired successfully."
} catch {
    $localPythonReady = $false
    Write-Warn "Virtual environment repair failed: $($_.Exception.Message)"
    Write-Warn "Continuing in Docker-first mode using the detected base interpreter."
    $rootVenv = @{
        Path = $basePython.Path
        Args = $basePython.Args
        VenvDir = "(base interpreter fallback)"
    }
    $backendVenv = @{
        Path = $basePython.Path
        Args = $basePython.Args
        VenvDir = "(base interpreter fallback)"
    }
}

Initialize-EnvFile
Initialize-LocalDockerConfig

if ($CheckOnly) {
    Test-Compose
    Write-Step "Check-only summary"
    Write-Info "Python: $($basePython.Path)"
    Write-Info "Root virtualenv: $($rootVenv.VenvDir)"
    Write-Info "Backend virtualenv: $($backendVenv.VenvDir)"
    Write-Info "Environment file: $envPath"
    Write-Info "Docker config: $dockerConfigDir"
    Write-Info "Compose file: $(Join-Path $root 'docker-compose.yml')"
    Write-Success "Preflight checks completed successfully."
    exit 0
}

Write-Step "Checking Docker Desktop availability"
$dockerExe = Get-DockerCommand
& $dockerExe version 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but not responding, or the current user cannot access the Docker daemon. Start Docker Desktop and retry."
}
Write-Success "Docker is available."

if ($localPythonReady) {
    Install-PythonDependencies -RootVenv $rootVenv -BackendVenv $backendVenv
} else {
    Write-Warn "Skipping local Python dependency install because repaired virtual environments were not available."
}
Invoke-FrontendBuild
Test-Compose
Start-ComposeStack
Show-ComposeServices

Write-Host ""
Write-Host "+-------------------------------------------------------------------+" -ForegroundColor Green
Write-Host "|                     Pulse Engine is starting                      |" -ForegroundColor Green
Write-Host "+-------------------------------------------------------------------+" -ForegroundColor Green
Write-Host "| API Gateway          -> http://localhost:8000                    |" -ForegroundColor Green
Write-Host "| Auth Service         -> http://localhost:8001                    |" -ForegroundColor Green
Write-Host "| User Service         -> http://localhost:8002                    |" -ForegroundColor Green
Write-Host "| Customer Service     -> http://localhost:8003                    |" -ForegroundColor Green
Write-Host "| Lead Service         -> http://localhost:8004                    |" -ForegroundColor Green
Write-Host "| AI Service           -> http://localhost:8005                    |" -ForegroundColor Green
Write-Host "| Analytics Service    -> http://localhost:8006                    |" -ForegroundColor Green
Write-Host "| Product Service      -> http://localhost:8007                    |" -ForegroundColor Green
Write-Host "| Notification Service -> http://localhost:8008                    |" -ForegroundColor Green
Write-Host "| Identity Service     -> http://localhost:8010                    |" -ForegroundColor Green
Write-Host "| Super Admin Service  -> internal only (super-admin:8011)         |" -ForegroundColor Green
Write-Host "| WhatsApp Bridge      -> run manually in terminal (optional)       |" -ForegroundColor Green
Write-Host "| Frontend build       -> ./frontend/build                         |" -ForegroundColor Green
Write-Host "+-------------------------------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Success "Next steps:"
Write-Info "1. Visit http://localhost:8000/api/healthz to confirm the gateway is healthy."
Write-Info "2. Run 'docker compose -p pulse-v3 --env-file .env logs -f gateway' for live startup logs."
Write-Info "3. Use .\start.ps1 for quick restarts; use .\start.ps1 -FullRebuild for a destructive Docker purge + image rebuild (resets DB volumes)."
Write-Info "4. Optional bridge: 'cd backend/whatsapp_bridge; npm install; node bridge.js'."
Write-Info "5. Dev mode frontend: 'cd frontend; npm start' (requires API gateway already running)."
Write-Info "6. Replace placeholder Meta and Stripe credentials in .env before enabling those integrations."
