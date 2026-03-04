#Requires -RunAsAdministrator
<#
.SYNOPSIS
    RCFlow Windows Uninstaller

.DESCRIPTION
    Removes RCFlow installation and Windows Service.
    Must be run as Administrator.

.PARAMETER InstallDir
    Installation directory (default: C:\RCFlow)

.PARAMETER KeepData
    Preserve the data\ directory (database)

.PARAMETER KeepConfig
    Preserve the .env configuration file

.PARAMETER Yes
    Skip confirmation prompt

.EXAMPLE
    .\uninstall.ps1
    .\uninstall.ps1 -KeepData -KeepConfig
#>

param(
    [string]$InstallDir = "C:\RCFlow",
    [switch]$KeepData,
    [switch]$KeepConfig,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

function Write-Info($msg)  { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "[OK]    $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }

# Check admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This uninstaller must be run as Administrator." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $InstallDir)) {
    Write-Host "[ERROR] No installation found at $InstallDir" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  This will remove the RCFlow installation at $InstallDir" -ForegroundColor Yellow
if (-not $KeepData) {
    Write-Host "  Including: database, data files" -ForegroundColor Yellow
}
if (-not $KeepConfig) {
    Write-Host "  Including: .env configuration" -ForegroundColor Yellow
}
Write-Host ""

if (-not $Yes) {
    $confirm = Read-Host "Are you sure? [y/N]"
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        Write-Host "Cancelled."
        exit 0
    }
}

# Stop and remove service
$svc = Get-Service -Name "RCFlow" -ErrorAction SilentlyContinue
if ($null -ne $svc) {
    if ($svc.Status -eq "Running") {
        Write-Info "Stopping RCFlow service..."
        Stop-Service -Name "RCFlow" -Force
        Start-Sleep -Seconds 2
        Write-Ok "Service stopped"
    }

    Write-Info "Removing Windows Service..."
    $nssmPath = Join-Path $InstallDir "nssm.exe"
    if (Test-Path $nssmPath) {
        & $nssmPath remove RCFlow confirm 2>$null
    }
    else {
        sc.exe delete RCFlow 2>$null
    }
    Start-Sleep -Seconds 1
    Write-Ok "Service removed"
}

# Backup data if requested
$backupDir = $null
$backupConfig = $null

if ($KeepData) {
    $dataDir = Join-Path $InstallDir "data"
    if (Test-Path $dataDir) {
        $backupDir = Join-Path $env:TEMP "rcflow-data-backup-$(Get-Date -Format 'yyyyMMddHHmmss')"
        Write-Info "Backing up data to $backupDir..."
        Copy-Item $dataDir -Destination $backupDir -Recurse
        Write-Ok "Data backed up"
    }
}

if ($KeepConfig) {
    $envFile = Join-Path $InstallDir ".env"
    if (Test-Path $envFile) {
        $backupConfig = Join-Path $env:TEMP "rcflow-env-backup-$(Get-Date -Format 'yyyyMMddHHmmss').env"
        Write-Info "Backing up config to $backupConfig..."
        Copy-Item $envFile -Destination $backupConfig
        Write-Ok "Config backed up"
    }
}

# Remove firewall rule
$fwRule = Get-NetFirewallRule -DisplayName "RCFlow Server" -ErrorAction SilentlyContinue
if ($null -ne $fwRule) {
    Write-Info "Removing firewall rule..."
    Remove-NetFirewallRule -DisplayName "RCFlow Server"
    Write-Ok "Firewall rule removed"
}

# Remove from PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($currentPath -like "*$InstallDir*") {
    Write-Info "Removing from system PATH..."
    $newPath = ($currentPath -split ";" | Where-Object { $_ -ne $InstallDir }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "Machine")
    Write-Ok "Removed from PATH"
}

# Remove installation directory
Write-Info "Removing $InstallDir..."
Remove-Item $InstallDir -Recurse -Force
Write-Ok "Installation removed"

Write-Host ""
Write-Ok "RCFlow has been uninstalled."

if ($backupDir) {
    Write-Host "  Data backup: $backupDir"
}
if ($backupConfig) {
    Write-Host "  Config backup: $backupConfig"
}
Write-Host ""
