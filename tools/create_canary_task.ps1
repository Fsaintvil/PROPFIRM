<#
Helper PowerShell pour créer une tâche planifiée qui exécute le canary.
Usage: Run as Administrator on the operator host.
#>
param(
    [string]$TaskName = "PROP_Canary_Run",
    [string]$StartTime = "00:00",
    [string]$User = "$env:USERNAME",
    [string]$Description = "Lance le canary: deploy pipeline en mode controllé"
)

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -Command `$env:DRY_RUN='0'; `$env:ALLOW_MT5_SEND='1'; python C:\Users\saint\Documents\PROPFIRM\tools\deploy_live_pipeline.py"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Description $Description
Write-Host "Tâche planifiée '$TaskName' créée (démarre dans 5 minutes)."