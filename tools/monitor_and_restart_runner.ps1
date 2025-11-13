<#
.SYNOPSIS
    Vérifie que le runner détaché est actif et le relance si nécessaire.

.DESCRIPTION
    Ce script vérifie le dernier fichier PID écrit dans `artifacts\live_trading` (pattern
    production_detached_*.pid). Si le PID n'existe plus, le script peut relancer le
    runner détaché. Par défaut il LOG uniquement dans `artifacts\live_trading/monitor_*.log`.

    Usage as scheduled task: run every 5 minutes (created by `install_production_schtask.ps1`).

    Important: relancer le runner en mode LIVE est possible via -EnableLive, mais cela active
    les envois si d'autres guards sont configurés (control files / env). Opérations sensibles
    doivent être exécutées par un opérateur.
#>

<# Simplified args parsing to avoid param() parsing issues in some PowerShell hosts #>
$TaskName = "PROPFIRM_LiveRunner"
$StartIfMissing = $false
$EnableLive = $false
if ($args -and ($args -contains '-StartIfMissing')) { $StartIfMissing = $true }
if ($args -and ($args -contains '-EnableLive')) { $EnableLive = $true }

Set-StrictMode -Version Latest

$repoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..')
$artDir = Join-Path $repoRoot 'artifacts\live_trading'
if (-not (Test-Path $artDir)) { New-Item -ItemType Directory -Path $artDir -Force | Out-Null }

$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$log = Join-Path $artDir ("monitor_${ts}.log")

function Log { param($m) Add-Content -Path $log -Value ("$(Get-Date -Format o) - $m") }

Log "Monitor start (TaskName=$TaskName)."

# Find latest pid file
$pidFiles = @(Get-ChildItem -Path $artDir -Filter 'production_detached_*.pid' -File | Sort-Object LastWriteTime -Descending)
if ($pidFiles.Count -eq 0) {
    Log "No pid file found."
    if ($StartIfMissing) { Log "StartIfMissing set -> starting runner..." }
    else { exit 0 }
} else {
    $latestPidFile = $pidFiles[0].FullName
    $pidContent = Get-Content -Path $latestPidFile -Raw -ErrorAction SilentlyContinue
    $procId = $null
    if ($pidContent -match '\d+') { $procId = [int]($pidContent -replace '\D','') }
    Log "Found pid file: $latestPidFile (pid=$procId)"
    $procAlive = $false
    if ($procId) {
        try { Get-Process -Id $procId -ErrorAction Stop | Out-Null; $procAlive = $true } catch { $procAlive = $false }
    }
    if ($procAlive) { Log "Process $procId is alive -> nothing to do."; exit 0 }
    Log "Process $procId not found -> runner not running."
    if (-not $StartIfMissing) { Log "StartIfMissing not set -> exit without starting."; exit 0 }
}

# If we reached here, we need to start the runner
$runnerScript = Join-Path $repoRoot 'tools\run_production_detached.ps1'
$startArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File', $runnerScript)
if ($EnableLive) { $startArgs += '-ConfirmLive'; $startArgs += 'I_CONFIRM_ALLOW_MT5_SEND' }

try {
    Log "Starting detached runner: pwsh $($startArgs -join ' ')"
    $proc = Start-Process -FilePath pwsh -ArgumentList $startArgs -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    if ($proc -and $proc.Id) {
        $pidFile = Join-Path $artDir ("production_detached_$(Get-Date -Format 'yyyyMMdd_HHmmss').pid")
        Set-Content -Path $pidFile -Value $proc.Id -Force
        Log "Started process Id $($proc.Id), wrote pid file $pidFile"
    } else {
        Log "Failed to start process or retrieve PID"
    }
} catch {
    Log "Start failed: $_"
}
