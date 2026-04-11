#Requires -RunAsAdministrator
<#
.SYNOPSIS
    RCFlow Windows Installer

.DESCRIPTION
    Installs RCFlow as a Windows Service using NSSM (Non-Sucking Service Manager).
    Must be run as Administrator.

.PARAMETER InstallDir
    Installation directory (default: C:\RCFlow)

.PARAMETER Port
    Server port (default: 53890)

.PARAMETER NoService
    Skip Windows Service setup

.PARAMETER Unattended
    Non-interactive mode with all defaults

.EXAMPLE
    .\install.ps1
    .\install.ps1 -InstallDir "D:\RCFlow" -Port 9000
    .\install.ps1 -Unattended
#>

param(
    [string]$InstallDir = "C:\RCFlow",
    [int]$Port = 53890,
    [switch]$NoService,
    [switch]$Unattended
)

$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Info($msg)  { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "[OK]    $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[ERROR] $msg" -ForegroundColor Red }

function Generate-ApiKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes) -replace '[/+=]', '' | Select-Object -First 1
}

function Download-Nssm {
    param([string]$DestDir)

    $nssmPath = Join-Path $DestDir "nssm.exe"
    if (Test-Path $nssmPath) {
        return $nssmPath
    }

    $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $zipPath = Join-Path $env:TEMP "nssm.zip"
    $extractPath = Join-Path $env:TEMP "nssm-extract"

    Write-Info "Downloading NSSM..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $nssmUrl -OutFile $zipPath -UseBasicParsing
    }
    catch {
        Write-Err "Failed to download NSSM from $nssmUrl"
        Write-Err "You can manually download NSSM and place nssm.exe in $DestDir"
        return $null
    }

    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

    # Find the correct architecture binary
    $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    $nssmExe = Get-ChildItem -Path $extractPath -Recurse -Filter "nssm.exe" |
        Where-Object { $_.DirectoryName -like "*$arch*" } |
        Select-Object -First 1

    if ($null -eq $nssmExe) {
        $nssmExe = Get-ChildItem -Path $extractPath -Recurse -Filter "nssm.exe" | Select-Object -First 1
    }

    if ($null -ne $nssmExe) {
        Copy-Item $nssmExe.FullName -Destination $nssmPath
        Write-Ok "NSSM downloaded"
    }
    else {
        Write-Err "Could not find nssm.exe in downloaded archive"
        return $null
    }

    # Cleanup
    Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $extractPath -Recurse -Force -ErrorAction SilentlyContinue

    return $nssmPath
}

# ── Check admin ──────────────────────────────────────────────────────────────

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "This installer must be run as Administrator."
    Write-Err "Right-click PowerShell and select 'Run as Administrator', then re-run this script."
    exit 1
}

# ── Determine bundle directory ───────────────────────────────────────────────

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $ScriptDir "rcflow.exe"))) {
    Write-Err "Cannot find rcflow.exe in $ScriptDir"
    Write-Err "Run this script from inside the extracted bundle directory."
    exit 1
}

$BundleVersion = "unknown"
$versionFile = Join-Path $ScriptDir "VERSION"
if (Test-Path $versionFile) {
    $BundleVersion = (Get-Content $versionFile -Raw).Trim()
}

# ── Banner ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "       RCFlow Installer v$BundleVersion (Windows)    " -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

# ── Check existing installation ──────────────────────────────────────────────

$Upgrading = $false
if ((Test-Path $InstallDir) -and (Test-Path (Join-Path $InstallDir "rcflow.exe"))) {
    $existingVersion = "unknown"
    $existingVersionFile = Join-Path $InstallDir "VERSION"
    if (Test-Path $existingVersionFile) {
        $existingVersion = (Get-Content $existingVersionFile -Raw).Trim()
    }
    Write-Warn "Existing installation detected: v$existingVersion at $InstallDir"
    Write-Info "Upgrading to v$BundleVersion. Data and configuration will be preserved."
    $Upgrading = $true
    Write-Host ""
}

# ── Interactive configuration ────────────────────────────────────────────────

if (-not $Upgrading -and -not $Unattended) {
    $input = Read-Host "Install directory [$InstallDir]"
    if ($input) { $InstallDir = $input }

    $input = Read-Host "Server port [$Port]"
    if ($input) { $Port = [int]$input }
}

Write-Info "Install directory: $InstallDir"
Write-Info "Server port:       $Port"
Write-Host ""

# ── Stop existing service ────────────────────────────────────────────────────

$existingService = Get-Service -Name "RCFlow" -ErrorAction SilentlyContinue
if ($null -ne $existingService -and $existingService.Status -eq "Running") {
    Write-Info "Stopping existing RCFlow service..."
    Stop-Service -Name "RCFlow" -Force
    Start-Sleep -Seconds 2
    Write-Ok "Service stopped"
}

# ── Create install directory ─────────────────────────────────────────────────

Write-Info "Installing to $InstallDir..."
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

# Copy executable
Copy-Item (Join-Path $ScriptDir "rcflow.exe") -Destination (Join-Path $InstallDir "rcflow.exe") -Force

# Copy _internal directory
$internalSrc = Join-Path $ScriptDir "_internal"
$internalDst = Join-Path $InstallDir "_internal"
if (Test-Path $internalSrc) {
    if (Test-Path $internalDst) { Remove-Item $internalDst -Recurse -Force }
    Copy-Item $internalSrc -Destination $internalDst -Recurse
}

# Copy tool definitions
$toolsSrc = Join-Path $ScriptDir "tools"
$toolsDst = Join-Path $InstallDir "tools"
if (Test-Path $toolsSrc) {
    New-Item -ItemType Directory -Path $toolsDst -Force | Out-Null
    Copy-Item "$toolsSrc\*.json" -Destination $toolsDst -Force
    Write-Ok "Tool definitions installed"
}

# Copy alembic migrations
$migSrc = Join-Path $ScriptDir "migrations"
$migDst = Join-Path $InstallDir "migrations"
if (Test-Path $migSrc) {
    if (Test-Path $migDst) { Remove-Item $migDst -Recurse -Force }
    Copy-Item $migSrc -Destination $migDst -Recurse
    Write-Ok "Database migrations installed"
}

# Copy alembic.ini
$iniSrc = Join-Path $ScriptDir "alembic.ini"
if (Test-Path $iniSrc) {
    Copy-Item $iniSrc -Destination (Join-Path $InstallDir "alembic.ini") -Force
}

# Copy VERSION
Copy-Item (Join-Path $ScriptDir "VERSION") -Destination (Join-Path $InstallDir "VERSION") -Force

# Copy uninstall script
$uninstallSrc = Join-Path $ScriptDir "uninstall.ps1"
if (Test-Path $uninstallSrc) {
    Copy-Item $uninstallSrc -Destination (Join-Path $InstallDir "uninstall.ps1") -Force
}

Write-Ok "Files installed"

# ── Create data directories ──────────────────────────────────────────────────

New-Item -ItemType Directory -Path (Join-Path $InstallDir "data") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $InstallDir "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $InstallDir "certs") -Force | Out-Null

# ── Create settings.json configuration ────────────────────────────────────────

$jsonFile = Join-Path $InstallDir "settings.json"
if (-not (Test-Path $jsonFile)) {
    Write-Info "Creating default configuration..."

    $ApiKey = Generate-ApiKey

    $dbPath = (Join-Path $InstallDir "data\rcflow.db") -replace '\\', '/'
    $toolsPath = (Join-Path $InstallDir "tools") -replace '\\', '/'

    $jsonContent = @"
{
  "RCFLOW_HOST": "0.0.0.0",
  "RCFLOW_PORT": "$Port",
  "RCFLOW_API_KEY": "$ApiKey",
  "RCFLOW_BACKEND_ID": "",
  "DATABASE_URL": "sqlite+aiosqlite:///$dbPath",
  "WS_ALLOWED_ORIGINS": "",
  "WSS_ENABLED": "true",
  "SSL_CERTFILE": "",
  "SSL_KEYFILE": "",
  "LLM_PROVIDER": "anthropic",
  "ANTHROPIC_API_KEY": "",
  "ANTHROPIC_MODEL": "claude-sonnet-4-6",
  "AWS_REGION": "us-east-1",
  "AWS_ACCESS_KEY_ID": "",
  "AWS_SECRET_ACCESS_KEY": "",
  "OPENAI_API_KEY": "",
  "OPENAI_MODEL": "gpt-5.4",
  "CODEX_API_KEY": "",
  "TITLE_MODEL": "",
  "TASK_MODEL": "",
  "GLOBAL_PROMPT": "",
  "PROJECTS_DIR": "~\Projects",
  "TOOLS_DIR": "$toolsPath",
  "TOOL_AUTO_UPDATE": "true",
  "TOOL_UPDATE_INTERVAL_HOURS": "6",
  "SESSION_INPUT_TOKEN_LIMIT": "0",
  "SESSION_OUTPUT_TOKEN_LIMIT": "0",
  "ARTIFACT_INCLUDE_PATTERN": "*.md",
  "ARTIFACT_EXCLUDE_PATTERN": "node_modules/**,__pycache__/**,.git/**,.venv/**,venv/**,.env/**,build/**,dist/**,target/**,*.pyc",
  "ARTIFACT_AUTO_SCAN": "true",
  "ARTIFACT_MAX_FILE_SIZE": "5242880",
  "LINEAR_API_KEY": "",
  "LINEAR_TEAM_ID": "",
  "LINEAR_SYNC_ON_STARTUP": "false",
  "TELEMETRY_RETENTION_DAYS": "90",
  "LOG_LEVEL": "INFO"
}
"@

    Set-Content -Path $jsonFile -Value $jsonContent -Encoding UTF8

    # Write the API key to a restricted file instead of printing it to the
    # console (which may be captured in transcripts or CI logs).
    $keyFile = Join-Path $InstallDir "initial-key.txt"
    Set-Content -Path $keyFile -Value $ApiKey -Encoding UTF8
    # Restrict read access to Administrators and SYSTEM only
    $acl = Get-Acl $keyFile
    $acl.SetAccessRuleProtection($true, $false)
    $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "Administrators", "FullControl", "Allow"
    )
    $systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "SYSTEM", "FullControl", "Allow"
    )
    $acl.AddAccessRule($adminRule)
    $acl.AddAccessRule($systemRule)
    Set-Acl -Path $keyFile -AclObject $acl

    Write-Ok "Configuration created with generated API key"
    Write-Host ""
    Write-Host "  API key saved to: $keyFile" -ForegroundColor Yellow
    Write-Host "  Read with (Admin PowerShell): Get-Content '$keyFile'" -ForegroundColor Yellow
    Write-Host "  Delete after copying: Remove-Item '$keyFile'" -ForegroundColor Yellow
    Write-Host "  Config file: $jsonFile" -ForegroundColor Yellow
    Write-Host ""
}
else {
    Write-Ok "Existing configuration preserved at $jsonFile"
}

# ── Run database migrations ──────────────────────────────────────────────────

Write-Info "Running database migrations..."
try {
    $exe = Join-Path $InstallDir "rcflow.exe"
    $migrationTimeout = 120  # seconds
    $proc = Start-Process -FilePath $exe -ArgumentList "migrate" -WorkingDirectory $InstallDir -NoNewWindow -PassThru -Wait:$false
    if ($proc.WaitForExit($migrationTimeout * 1000)) {
        if ($proc.ExitCode -eq 0) {
            Write-Ok "Database migrations complete"
        } else {
            Write-Warn "Migration exited with code $($proc.ExitCode)"
            Write-Warn "You can retry with: cd $InstallDir && .\rcflow.exe migrate"
        }
    } else {
        Write-Warn "Migration timed out after ${migrationTimeout}s — killing process"
        $proc.Kill()
        $proc.WaitForExit(5000) | Out-Null
        Write-Warn "You can retry with: cd $InstallDir && .\rcflow.exe migrate"
    }
}
catch {
    Write-Warn "Migration failed: $_"
    Write-Warn "You can retry with: cd $InstallDir && .\rcflow.exe migrate"
}

# ── Setup Windows Service ────────────────────────────────────────────────────

if (-not $NoService) {
    Write-Info "Setting up Windows Service..."

    $nssmPath = Download-Nssm -DestDir $InstallDir

    if ($null -ne $nssmPath) {
        # Remove existing service if present
        $existingSvc = Get-Service -Name "RCFlow" -ErrorAction SilentlyContinue
        if ($null -ne $existingSvc) {
            Write-Info "Removing existing service registration..."
            & $nssmPath remove RCFlow confirm 2>$null
            Start-Sleep -Seconds 1
        }

        $rcflowExe = Join-Path $InstallDir "rcflow.exe"

        # Install service
        & $nssmPath install RCFlow $rcflowExe
        & $nssmPath set RCFlow DisplayName "RCFlow Action Server"
        & $nssmPath set RCFlow Description "RCFlow WebSocket action server for natural language tool execution"
        & $nssmPath set RCFlow AppDirectory $InstallDir
        & $nssmPath set RCFlow Start SERVICE_AUTO_START
        & $nssmPath set RCFlow AppStdout (Join-Path $InstallDir "logs\service-stdout.log")
        & $nssmPath set RCFlow AppStderr (Join-Path $InstallDir "logs\service-stderr.log")
        & $nssmPath set RCFlow AppRotateFiles 1
        & $nssmPath set RCFlow AppRotateBytes 10485760

        Write-Ok "Windows Service installed"

        # Start the service
        Write-Info "Starting RCFlow service..."
        Start-Service -Name "RCFlow"
        Start-Sleep -Seconds 3

        $svc = Get-Service -Name "RCFlow"
        if ($svc.Status -eq "Running") {
            Write-Ok "RCFlow is running!"
        }
        else {
            Write-Warn "Service may have failed to start. Check logs at: $InstallDir\logs\"
        }
    }
    else {
        Write-Warn "NSSM not available. Service not registered."
        Write-Warn "You can run RCFlow manually: $InstallDir\rcflow.exe"
    }
}

# ── Add to PATH (optional) ──────────────────────────────────────────────────

$currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($currentPath -notlike "*$InstallDir*") {
    Write-Info "Adding $InstallDir to system PATH..."
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$InstallDir", "Machine")
    Write-Ok "Added to PATH (restart shell to take effect)"
}

# ── Firewall rule ────────────────────────────────────────────────────────────

$fwRule = Get-NetFirewallRule -DisplayName "RCFlow Server" -ErrorAction SilentlyContinue
if ($null -eq $fwRule) {
    Write-Info "Creating firewall rule for port $Port..."
    New-NetFirewallRule -DisplayName "RCFlow Server" `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $Port `
        -Action Allow `
        -Profile Any | Out-Null
    Write-Ok "Firewall rule created"
}

# ── Done ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "         Installation complete!               " -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Install directory:  $InstallDir"
Write-Host "  Configuration:      $envFile"
Write-Host "  Data directory:     $InstallDir\data"
Write-Host "  Logs directory:     $InstallDir\logs"
Write-Host ""
Write-Host "  Service commands (PowerShell as Admin):"
Write-Host "    Get-Service RCFlow              # Check status"
Write-Host "    Restart-Service RCFlow          # Restart"
Write-Host "    Stop-Service RCFlow             # Stop"
Write-Host "    Get-Content $InstallDir\logs\service-stdout.log -Tail 50  # View logs"
Write-Host ""
Write-Host "  Edit configuration:"
Write-Host "    notepad $envFile"
Write-Host "    Restart-Service RCFlow"
Write-Host ""
Write-Host "  Uninstall:"
Write-Host "    $InstallDir\uninstall.ps1"
Write-Host ""

if (-not $Upgrading) {
    Write-Host "  IMPORTANT: Edit $envFile to set your ANTHROPIC_API_KEY" -ForegroundColor Yellow
    Write-Host "  before using the server." -ForegroundColor Yellow
    Write-Host ""
}
