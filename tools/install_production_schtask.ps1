<#
.SYNOPSIS
    Installe deux Scheduled Tasks utiles pour la production:
      - Un task qui démarre le runner détaché au démarrage système
      - Un task qui exécute périodiquement le monitor pour relancer le runner si nécessaire

.DESCRIPTION
    Ce script crée (ou remplace) deux tâches planifiées Windows via `schtasks.exe`.
    Par défaut la tâche qui démarre le runner ne passe PAS le token live. Ceci est
    volontaire pour éviter d'activer les envois MT5 sans contrôle explicite. Si vous
    voulez que la task démarre en mode LIVE automatiquement, lancez ce script avec
    -EnableLive (nécessite une attention: active les envois MT5 si ALLOW_MT5_SEND est 1).

.NOTES
    Doit être exécuté en tant qu'administrateur pour créer une Scheduled Task système.
#>

<# Simplified argument handling: some environments have issues with param() parsing.
   Support simple args: -EnableLive and -WhatIf passed positionally or named via $args. #>
$TaskName = "PROPFIRM_LiveRunner"
$RunnerScriptPath = ""
$MonitorScriptPath = ""
$EnableLive = $false
$WhatIf = $false
if ($args -and ($args -contains '-EnableLive')) { $EnableLive = $true }
if ($args -and ($args -contains '-WhatIf')) { $WhatIf = $true }

Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RunnerScriptPath) { $RunnerScriptPath = Join-Path -Path $scriptDir -ChildPath 'run_production_detached.ps1' }
if (-not $MonitorScriptPath) { $MonitorScriptPath = Join-Path -Path $scriptDir -ChildPath 'monitor_and_restart_runner.ps1' }

$runner = (Resolve-Path $RunnerScriptPath).Path
$monitor = (Resolve-Path $MonitorScriptPath).Path

Write-Output "TaskName: $TaskName"
Write-Output "Runner script: $runner"
Write-Output "Monitor script: $monitor"

if ($WhatIf) {
    Write-Output "WhatIf: no task will be created. Use this to review the planned commands."
}

# Build the command for the runner task
$runnerAction = "pwsh -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
if ($EnableLive) {
    Write-Warning "EnableLive specified: the created task will pass the live confirmation token to the runner. Ensure you understand this will enable MT5 sends if other guards are in place."
    $runnerAction = $runnerAction + " -ConfirmLive 'I_CONFIRM_ALLOW_MT5_SEND'"
}

$monitorAction = "pwsh -NoProfile -ExecutionPolicy Bypass -File `"$monitor`" -TaskName `"$TaskName`""

Write-Output "Runner action: $runnerAction"
Write-Output "Monitor action: $monitorAction"

if ($WhatIf) { return }

# Create the runner task on system startup
Write-Output "Creating/Updating Scheduled Task: $TaskName (ONSTART)"
try {
    # Prefer Register-ScheduledTask (PowerShell ScheduledTasks module) to avoid quoting issues
    $actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
    if ($EnableLive) { $actionArgs = $actionArgs + " -ConfirmLive 'I_CONFIRM_ALLOW_MT5_SEND'" }
    $action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument $actionArgs
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force
    Write-Output "Registered Scheduled Task (Register-ScheduledTask) for $TaskName"
} catch {
    Write-Warning "Register-ScheduledTask failed or module unavailable: $_ - falling back to schtasks.exe"
    $cmdCreateRunner = "schtasks /Create /TN `"$TaskName`" /TR `"$runnerAction`" /SC ONSTART /RL HIGHEST /F"
    Write-Output $cmdCreateRunner
    Invoke-Expression $cmdCreateRunner
}

# Create monitor task every 5 minutes
$monitorTaskName = "$TaskName-Monitor"
Write-Output "Creating/Updating Scheduled Task: $monitorTaskName (Every 5 minutes)"
try {
    $monArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$monitor`" -TaskName `"$TaskName`""
    $action2 = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument $monArgs
    # Create a trigger that repeats every 5 minutes indefinitely (large duration)
    $trigger2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName $monitorTaskName -Action $action2 -Trigger $trigger2 -Principal $principal -Force
    Write-Output "Registered Scheduled Task (Register-ScheduledTask) for $monitorTaskName"
} catch {
    Write-Warning "Register-ScheduledTask for monitor failed: $_ - falling back to schtasks.exe"
    $cmdCreateMonitor = "schtasks /Create /TN `"$monitorTaskName`" /TR `"$monitorAction`" /SC MINUTE /MO 5 /F /RL HIGHEST"
    Write-Output $cmdCreateMonitor
    Invoke-Expression $cmdCreateMonitor
}

Write-Output "Scheduled tasks creation attempted. Review Task Scheduler for verification."
