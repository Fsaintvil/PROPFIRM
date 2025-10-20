param(
    [Parameter(Mandatory=$true)][string]$AuthToken,
    [int]$Minutes = 5,
    [double]$AutoSLPct = 0.01,
    [double]$AutoTPPct = 0.01,
    [double]$AutoStopDrawdownPct = 0.01,
    [switch]$NoWindow,
    [switch]$RunOnce
)

# Simple periodic runner for _execute_recommendations_live.py
# Usage (PowerShell):
#   .\start_periodic_live.ps1 -AuthToken <token> -Minutes 5
# To run in background as a job:
#   Start-Job -FilePath .\ops\start_periodic_live.ps1 -ArgumentList '<token>',5

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Determine script directory robustly (works inside Start-Job and interactive)
$ScriptDir = $PSCommandPath
if (-not $ScriptDir) { $ScriptDir = $MyInvocation.MyCommand.Definition }
if (-not $ScriptDir) { $ScriptDir = $PSScriptRoot }
if (-not $ScriptDir) { $ScriptDir = Get-Location }
$ScriptDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

try {
    $timestamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $LogFile = Join-Path $LogDir "periodic_live_$timestamp.log"
    # create an initial log entry so failures early in the script are captured
    "[$(Get-Date -Format o)] Starting periodic_live script" | Out-File -FilePath $LogFile -Encoding utf8 -Append
}
catch {
    Write-Error "Failed to prepare log file: $_"
    throw
}

function Invoke-LiveOnce {
    param($token)
    try {
        "[$(Get-Date -Format o)] Running live executor..." | Out-File -FilePath $LogFile -Append
        # ensure working dir is repo root for module resolution
        Push-Location -Path $RepoRoot
        $env:PYTHONPATH = $RepoRoot
        $env:ALLOW_LIVE_SEND = '1'
        $env:ALLOW_LIVE_SEND_TOKEN = $token
        # export automatic SL/TP/drawdown thresholds
        $env:AUTO_SL_PCT = [string]::Format('{0}', $AutoSLPct)
        $env:AUTO_TP_PCT = [string]::Format('{0}', $AutoTPPct)
        $env:AUTO_STOP_DRAWDOWN_PCT = [string]::Format('{0}', $AutoStopDrawdownPct)
        $exe = "${env:LOCALAPPDATA}\Programs\Python\Python313\python.exe"
        if (-not (Test-Path $exe)) { $exe = 'python' }
        & $exe -m MT5_FTMO_IA.scripts._execute_recommendations_live --auth-token $token 2>&1 | Tee-Object -FilePath $LogFile -Append
        "[$(Get-Date -Format o)] Run finished." | Out-File -FilePath $LogFile -Append
    }
    catch {
        "[$(Get-Date -Format o)] Invoke-LiveOnce failed: $_" | Out-File -FilePath $LogFile -Append
        # Do not rethrow to keep the background job alive; log and return
        return
    }
    finally {
        Pop-Location
    }
}

Write-Output "Starting periodic live runner (interval: $Minutes minutes). Log: $LogFile"
Write-Output "Press Ctrl+C to stop." | Out-File -FilePath $LogFile -Append

try {
    do {
        Invoke-LiveOnce -token $AuthToken
        if ($RunOnce) { break }
        Start-Sleep -Seconds ($Minutes * 60)
    } while ($true)
}
catch {
    "[$(Get-Date -Format o)] Periodic runner terminated with error: $_" | Out-File -FilePath $LogFile -Append
    # keep job from failing by swallowing the exception after logging
}
