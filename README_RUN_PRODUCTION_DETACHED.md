# README: run_production_detached.ps1

This file is a helper to start the production process in detached mode and save the PID + logs.

## Example Scheduled Task (Register-ScheduledTask) snippet

Open an elevated PowerShell and run the following (adapt paths and user):

```powershell
$action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument "-NoProfile -WindowStyle Hidden -File `"C:\path\to\tools\run_production_detached.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 06:00AM
$principal = New-ScheduledTaskPrincipal -UserId 'DOMAIN\\Username' -LogonType Password -RunLevel Highest
Register-ScheduledTask -TaskName 'PROPFIRM_RunProduction' -Action $action -Trigger $trigger -Principal $principal -Description 'Start PROPFIRM production (detached)'
```

Notes:

* Use a service account with rights and non-expiring password if possible.
* Ensure the working dir and python are accessible by that account.
* Add an email/notification wrapper on failure if required (external script).
