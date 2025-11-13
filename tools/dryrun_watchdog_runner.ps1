<#
Start watchdog in a background pwsh process, wait a bit, create STOP flag to request shutdown,
then report whether process exited.
#>
param(
    [int]$WaitSecondsBeforeStop = 20,
    [int]$WaitAfterStopSeconds = 6
)

$watchdogScript = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'watchdog_sf_ia7.ps1'
if (-not (Test-Path $watchdogScript)) { Write-Output "MISSING_WATCHDOG_SCRIPT: $watchdogScript"; exit 2 }

Write-Output "DRYRUN: Starting watchdog ($watchdogScript)"
$proc = Start-Process pwsh -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$watchdogScript) -PassThru
Write-Output "DRYRUN: STARTED_PID=$($proc.Id)"

Write-Output "DRYRUN: Waiting $WaitSecondsBeforeStop seconds to let watchdog spawn the test bot"
Start-Sleep -Seconds $WaitSecondsBeforeStop

$stopFile = Join-Path (Get-Location).Path 'STOP_WATCHDOG.SF_IA.7'
New-Item -Path $stopFile -ItemType File -Force | Out-Null
Write-Output "DRYRUN: STOP_FLAG_CREATED=$stopFile"

Start-Sleep -Seconds $WaitAfterStopSeconds

try {
    $p = Get-Process -Id $proc.Id -ErrorAction Stop
    Write-Output "DRYRUN: PROCESS_STILL_RUNNING_PID=$($p.Id)"
} catch {
    Write-Output "DRYRUN: PROCESS_EXITED"
}

# cleanup: try remove stop file
Remove-Item -Path $stopFile -ErrorAction SilentlyContinue
