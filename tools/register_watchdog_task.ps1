param(
    [string]$TaskName = "Watchdog_SF_IA7",
    [ValidateSet('Logon','DailyInterval')][string]$TriggerType = 'Logon',
    [int]$IntervalMinutes = 30,
    [string]$User = "$env:USERNAME",
    [switch]$RunHighest
)

$scriptPath = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'watchdog_sf_ia7.ps1')
if (-not (Test-Path $scriptPath)) { Write-Error "watchdog script not found at $scriptPath"; exit 2 }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

switch ($TriggerType) {
    'Logon' { $trigger = New-ScheduledTaskTrigger -AtLogOn }
    'DailyInterval' { $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration ([TimeSpan]::MaxValue) }
}

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive
if ($RunHighest.IsPresent) { $principal.RunLevel = 'Highest' }

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force

Write-Output "TASK_REGISTERED: $TaskName"
