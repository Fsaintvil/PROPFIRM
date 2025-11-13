# Registering a Scheduled Task (Windows) for detached production runs

This document shows a safe example to register a Windows Scheduled Task that starts the detached production runner in this repo.

Important safety notes
- Never schedule the task to pass the `CONFIRM_PRODUCTION` token automatically unless you have performed a full human review.
- Keep `ALLOW_MT5_SEND` at `0` for scheduled tasks unless you explicitly want live sends and you understand the financial risk.
- Test the task in dry-run mode first (no token), verify logs, then, if required, run a one-time manual start with the confirmation token.

Example: one-off scheduled task that runs the detached runner in DRY-RUN mode once at system startup

PowerShell snippet to create the task (run as administrator):

```powershell
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\Users\saint\Documents\PROPFIRM\tools\run_production_detached.ps1"'
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -Action $action -Trigger $trigger -TaskName 'PROPFIRM_RunProduction_DryRun' -Description 'Start detached production (dry-run) at startup' -User 'SYSTEM' -RunLevel Highest
```

If you want the scheduled task to run the runner daily at a specific time, use a `New-ScheduledTaskTrigger -Daily -At '03:00'` instead.

How to enable LIVE manually (recommended procedure)
1. Stop scheduled automatic starts.
2. Manually run the detached runner and provide the token interactively:

```powershell
cd C:\Users\saint\Documents\PROPFIRM
.\tools\run_production_detached.ps1 -ConfirmLive 'I_CONFIRM_ALLOW_MT5_SEND'
```

3. Monitor `artifacts\\live_trading` for the newly created `production_live_*.out.log` and `*.pid`. Verify first few minutes of logs show expected behavior.

Recovery / rollback
- To force-stop the detached run, use `Stop-Process -Id <pid>` then remove the corresponding `.pid` file.
- Keep `control\\disable_trading` and `control\\emergency_stop` files available as quick kill-switch mechanisms. Scripts in this repo check these files.

Contact/notifications
- The runner supports optional `NOTIFY_ON_CRITICAL` env variable (webhook) — configure externally if you want alerts.
