<#
Install a Windows Scheduled Task that runs the watch_active_model script every 5 minutes.

This script is safe to run. It registers a scheduled task named
"PROPFIRM_WatchActiveModel" that executes PowerShell to run the
Python script `scripts/ops/watch_active_model.py --once` and append
output to `artifacts/reports/watch_active_model.log`.

Usage (run as a user with permission to create a scheduled task):
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\ops\install_watch_task.ps1

To remove the task:
  schtasks /Delete /TN "PROPFIRM_WatchActiveModel" /F
#>

param(
  [int]$IntervalMinutes = 5,
  [switch]$UseSchtasks  # When set, use schtasks /Create (more compatible). Default: Register-ScheduledTask if available, else schtasks.
)

$taskName = 'PROPFIRM_WatchActiveModel'
# use a small wrapper ps1 that invokes python to avoid complex quoting issues
$wrapper = (Resolve-Path -Path '.\scripts\ops\run_watch_once.ps1').Path

Write-Host "Installing scheduled task '$taskName' to run every $IntervalMinutes minutes using wrapper: $wrapper"

if ($UseSchtasks) {
  # Build schtasks command: start in 1 minute, repeat every $IntervalMinutes
  $st = (Get-Date).AddMinutes(1).ToString('HH:mm')
  $tr = "pwsh -NoProfile -WindowStyle Hidden -File `"$wrapper`""
  Write-Host "Creating via schtasks starting at $st"
  schtasks /Create /SC MINUTE /MO $IntervalMinutes /TN $taskName /TR $tr /ST $st /F | Out-Null
  Write-Host "Scheduled task created via schtasks: $taskName"
} else {
  # Try Register-ScheduledTask (preferable when available)
  try {
    $minutes = $IntervalMinutes
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $minutes) -RepetitionDuration ([TimeSpan]::FromHours(72))
    $psAction = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument "-NoProfile -WindowStyle Hidden -File '$wrapper'"
    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $psAction -Description "Run watch_active_model.py every $IntervalMinutes minutes" -User $env:USERNAME -RunLevel Limited -Force
    Write-Host "Scheduled task registered via Register-ScheduledTask: $taskName"
  } catch {
    Write-Warning "Register-ScheduledTask failed, falling back to schtasks: $_"
    $st = (Get-Date).AddMinutes(1).ToString('HH:mm')
    $tr = "pwsh -NoProfile -WindowStyle Hidden -File `"$wrapper`""
    schtasks /Create /SC MINUTE /MO $IntervalMinutes /TN $taskName /TR $tr /ST $st /F | Out-Null
    Write-Host "Scheduled task created via schtasks fallback: $taskName"
  }
}

Write-Host "Install complete: $taskName"
