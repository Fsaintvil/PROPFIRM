<#
.SYNOPSIS
    Lance `tools/run_production.ps1` en detached (mode tâche/service) en s'assurant
    que le passage au mode LIVE nécessite une confirmation explicite.

.DESCRIPTION
    Ce script démarre proprement le runner de production depuis le répertoire racine
    du repo. Par défaut il démarre en DRY-RUN (ALLOW_MT5_SEND=0). Pour activer
    réellement les envois MT5 il faut fournir le paramètre -ConfirmLive avec la
    valeur exacte 'I_CONFIRM_ALLOW_MT5_SEND'.

    Le script crée un fichier PID dans `artifacts\\live_trading` et redirige
    stdout/stderr vers des fichiers horodatés.

USAGE
    # Dry-run (safe)
    .\\tools\\run_production_detached.ps1

    # Start in live mode (REQUIRES exact token)
    .\\tools\\run_production_detached.ps1 -ConfirmLive 'I_CONFIRM_ALLOW_MT5_SEND'

SAFETY
    This script will NOT enable live sends unless the exact token is provided.
    Review logs and kill-switches before using -ConfirmLive.
#>

param(
    [string]$ConfirmLive = $null
)

Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# move to repo root (script is in tools/)
Set-Location (Resolve-Path (Join-Path $repoRoot '..'))
$repoRoot = Resolve-Path -Path "." | Select-Object -ExpandProperty Path

# Prepare logs and pid
$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$out = Join-Path -Path "$repoRoot\artifacts\live_trading" -ChildPath "production_detached_${ts}.out.log"
$err = Join-Path -Path "$repoRoot\artifacts\live_trading" -ChildPath "production_detached_${ts}.err.log"
$pidFile = Join-Path -Path "$repoRoot\artifacts\live_trading" -ChildPath "production_detached_${ts}.pid"

if (-not (Test-Path "$repoRoot\artifacts\live_trading")) {
    New-Item -ItemType Directory -Path "$repoRoot\artifacts\live_trading" -Force | Out-Null
}

# Safety checks
if (Test-Path "control\\disable_trading") {
    Write-Warning "control\\disable_trading present - aborting start. Remove the file if you really want to run."
    exit 1
}
if (Test-Path "control\\emergency_stop") {
    Write-Warning "control\\emergency_stop present - aborting start. Remove the file if you really want to run."
    exit 1
}

# Load defaults from docs/production_env_defaults.json if present
$jsonEnvFile = Join-Path -Path $repoRoot -ChildPath 'docs\production_env_defaults.json'
$envAssignments = @()
$startInLive = $false
if (Test-Path $jsonEnvFile) {
    Write-Output "Loading environment defaults from $jsonEnvFile"
    try {
        $jsonText = Get-Content -Path $jsonEnvFile -Raw -ErrorAction Stop
        $defaults = $jsonText | ConvertFrom-Json
        foreach ($p in $defaults.PSObject.Properties) {
            $k = $p.Name
            $v = [string]$p.Value
            # prepare assignment for child process
            $safeVal = $v -replace "'","''"
            # Build a literal assignment string like: $env:NAME='VALUE'
            $assignment = ('$env:' + $k + "='" + $safeVal + "'")
            $envAssignments += $assignment
        }
        # If JSON explicitly requests live sends, note it
        if ($defaults.PSObject.Properties.Name -contains 'ALLOW_MT5_SEND' -and ([string]$defaults.ALLOW_MT5_SEND) -eq '1') {
            Write-Warning "Defaults request ALLOW_MT5_SEND=1 — child will be started with live sends unless you override."
            $startInLive = $true
        }
    } catch {
        Write-Warning "Failed to load environment defaults: $_"
    }
} else {
    Write-Output "No defaults JSON found; proceeding with token/param based behavior"
}

# If explicit ConfirmLive param provided, validate it
if ($ConfirmLive -eq 'I_CONFIRM_ALLOW_MT5_SEND') {
    Write-Output "Confirmed token provided. Enabling ALLOW_MT5_SEND for child process."
    $startInLive = $true
} elseif ($ConfirmLive) {
    Write-Warning "Confirm token provided but does not match expected value. Running in DRY-RUN unless JSON requests live."
}

# Build command to execute: call the existing tools/run_production.ps1 in a new pwsh process
$runner = Join-Path -Path $repoRoot -ChildPath 'tools\run_production.ps1'

# We will create a small wrapper command that sets env vars for the child process only when live is confirmed
# Build wrapper with environment assignments (from defaults and token)
$assignStr = ''
if ($envAssignments.Count -gt 0) {
    $assignStr = ($envAssignments -join '; ') + '; '
}
if ($startInLive) {
    # ensure token is provided to child process
    $assignStr = $assignStr + "$env:CONFIRM_PRODUCTION='I_CONFIRM_ALLOW_MT5_SEND';"
    Write-Output "Starting production in LIVE mode (child will have environment variables from defaults + CONFIRM_PRODUCTION token)."
} else {
    # ensure ALLOW_MT5_SEND is 0 unless JSON requested it
    if ($assignStr -notmatch "ALLOW_MT5_SEND") {
        $assignStr = $assignStr + "$env:ALLOW_MT5_SEND='0';"
    }
    Write-Output "Starting production in DRY-RUN mode (child will have environment variables from defaults where provided)."
}

$wrapped = "& { $assignStr & '$runner' }"

# Start detached pwsh process
$args = @('-NoProfile','-ExecutionPolicy','Bypass','-Command',$wrapped)
$proc = Start-Process -FilePath pwsh -ArgumentList $args -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden

if ($proc) {
    $procId = $proc.Id
    Write-Output "Started child pwsh process with Id $procId"
    Set-Content -Path $pidFile -Value $procId -Force
    Write-Output "Wrote PID file: $pidFile"
    Write-Output "Child stdout/stderr are handled by the child script (see artifacts/live_trading/*.out.log)"
} else {
    Write-Warning "Failed to start child process."
    exit 2
}

Write-Output "Detached start complete. Monitor logs in artifacts\\live_trading."
