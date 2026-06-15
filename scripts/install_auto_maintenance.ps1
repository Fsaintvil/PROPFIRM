<#
.SYNOPSIS
    Install/Remove/Status/Test the auto-maintenance scheduled task
.DESCRIPTION
    Creates a Windows scheduled task that runs auto_maintenance.py every hour.
.PARAMETER Status
    Show current task status
.PARAMETER Remove
    Remove the scheduled task
.PARAMETER Test
    Run once to test
#>

param(
    [switch]$Status,
    [switch]$Remove,
    [switch]$Test
)

$TaskName = "MT5_FTMO_AutoMaintenance"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $ProjectRoot "auto_maintenance.py"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $ScriptPath)) {
    Write-Error "Script not found: $ScriptPath"
    exit 1
}

# ---- Status ----
if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Task not installed" -ForegroundColor Red
    } else {
        Write-Host "Task: $TaskName" -ForegroundColor Cyan
        Write-Host "  State: $($task.State)" -ForegroundColor Green
        Write-Host "  Last run: $($task.LastRunTime)"
        Write-Host "  Last result: $($task.LastTaskResult)"
    }

    $proc = Get-Process -Name "python*" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*auto_maintenance*" }
    if ($proc) {
        Write-Host "auto_maintenance.py running (PID $($proc.Id))" -ForegroundColor Green
    }
    return
}

# ---- Remove ----
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Task removed: $TaskName" -ForegroundColor Green
    return
}

# ---- Test ----
if ($Test) {
    Write-Host "Running auto_maintenance.py once..." -ForegroundColor Cyan
    & python $ScriptPath
    Write-Host "Done (exit code: $LASTEXITCODE)" -ForegroundColor Green
    return
}

# ---- Install ----
Write-Host "Installing auto-maintenance..." -ForegroundColor Cyan

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Action = New-ScheduledTaskAction -Execute "pythonw.exe" `
    -Argument "$ScriptPath --watch --interval 60" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date "00:00") `
    -RepetitionInterval (New-TimeSpan -Minutes 60) `
    -RepetitionDuration (New-TimeSpan -Days 365)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

try {
    Register-ScheduledTask -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "Auto-maintenance for MT5 FTMO robot" `
        -Force

    Write-Host "Task created: $TaskName" -ForegroundColor Green
    Write-Host "  Cycle: every 60 minutes" -ForegroundColor Gray
    Write-Host "  User: SYSTEM" -ForegroundColor Gray
}
catch {
    Write-Warning "SYSTEM task failed, trying current user..."
    $UserPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $UserPrincipal `
        -Description "Auto-maintenance for MT5 FTMO robot" `
        -Force
    Write-Host "Task created (user: $env:USERNAME)" -ForegroundColor Green
}

Start-Sleep -Seconds 1
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ""
    Write-Host "Summary:" -ForegroundColor Cyan
    Write-Host "  Name: $TaskName" -ForegroundColor White
    Write-Host "  State: $($task.State)" -ForegroundColor White
    Write-Host "  Next run: $($task.NextRunTime)" -ForegroundColor White
    Write-Host ""
    Write-Host "Status: .\scripts\install_auto_maintenance.ps1 -Status" -ForegroundColor Gray
    Write-Host "Remove: .\scripts\install_auto_maintenance.ps1 -Remove" -ForegroundColor Gray
    Write-Host "Test:   .\scripts\install_auto_maintenance.ps1 -Test" -ForegroundColor Gray
    Write-Host "Logs:   $LogDir\auto_maintenance.log" -ForegroundColor Gray
}
else {
    Write-Error "Failed to create task"
}
