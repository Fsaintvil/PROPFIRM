try {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $repoRoot = (Resolve-Path (Join-Path $root '..')).Path
    $wrapper = Join-Path -Path $repoRoot -ChildPath 'tools\run_live_controller_wrapper.cmd'
    $action = New-ScheduledTaskAction -Execute $wrapper
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
    Register-ScheduledTask -TaskName 'PROPFIRM_LiveRunController' -Action $action -Trigger $trigger -Principal $principal -Force
    Write-Output "Registered task PROPFIRM_LiveRunController"
    Start-ScheduledTask -TaskName 'PROPFIRM_LiveRunController'
    Start-Sleep -Seconds 6
    $logPath = Join-Path -Path $repoRoot -ChildPath 'artifacts\live_trading\live_run_controller.log'
    Get-Content $logPath -Tail 200 -ErrorAction SilentlyContinue
} catch {
    Write-Error "Failed to register/start task: $_"
    exit 1
}