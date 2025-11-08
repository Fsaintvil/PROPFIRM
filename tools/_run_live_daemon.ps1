```powershell
# Persistent daemon runner for live trading
# SECURITY: Do NOT hardcode credentials in scripts. Read from environment or a secrets store.
$ErrorActionPreference = 'Stop'
$now = Get-Date -Format "yyyyMMddTHHmmss"
$logDir = Join-Path $PSScriptRoot "..\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "daemon_$now.log"

# Require MT5 credentials from environment
if (-not $env:MT5_LOGIN) {
    Write-Output "ERROR: MT5_LOGIN not set in environment. Aborting." | Out-File -FilePath $logFile -Encoding utf8 -Append
    exit 2
}
if (-not $env:MT5_PASSWORD) {
    Write-Output "ERROR: MT5_PASSWORD not set in environment. Aborting." | Out-File -FilePath $logFile -Encoding utf8 -Append
    exit 2
}
if (-not $env:MT5_SERVER) {
    Write-Output "WARNING: MT5_SERVER not set. Defaulting to 'FTMO-Demo'" | Out-File -FilePath $logFile -Encoding utf8 -Append
    $env:MT5_SERVER = 'FTMO-Demo'
}

Write-Output "Starting live daemon at $(Get-Date -Format o) -> logging to $logFile" | Out-File -FilePath $logFile -Encoding utf8 -Append
Write-Output "Command: python -u scripts/online_live_learning.py --mode live --daemon --confirm-live --orders-per-instrument 50 --only-weekdays" | Out-File -FilePath $logFile -Encoding utf8 -Append

try {
    # Start the daemon process in a new PowerShell background job so the scheduled task can return
    Start-Job -ScriptBlock {
        param($logFile)
        python -u scripts/online_live_learning.py --mode live --daemon --confirm-live --orders-per-instrument 50 --only-weekdays 2>&1 | Tee-Object -FilePath $logFile
    } -ArgumentList $logFile | Out-Null
    Write-Output "Daemon started as background job. Exiting wrapper." | Out-File -FilePath $logFile -Encoding utf8 -Append
    exit 0
} catch {
    Write-Output "Failed to start daemon: $_" | Out-File -FilePath $logFile -Encoding utf8 -Append
    exit 1
}
```