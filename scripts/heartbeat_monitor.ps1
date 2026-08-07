<#
.SYNOPSIS
VPS Heartbeat Monitor — external circuit breaker for MT5 FTMO Robot.

Monitors both heartbeats (main.py + watchdog.py) and restarts on stall.
Designed for Windows Task Scheduler (every 5 min) or manual daemon mode.

Usage:
  .\scripts\heartbeat_monitor.ps1          # One-shot check (for Task Scheduler)
  .\scripts\heartbeat_monitor.ps1 -Daemon  # Continuous loop (every 60s)
  .\scripts\heartbeat_monitor.ps1 -Install # Create Task Scheduler entry
  .\scripts\heartbeat_monitor.ps1 -Remove  # Remove Task Scheduler entry
#>

param(
    [switch]$Daemon,
    [switch]$Install,
    [switch]$Remove,
    [int]$HeartbeatTimeout = 180,
    [int]$WatchdogTimeout = 300,
    [int]$CheckInterval = 60
)

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$LogFile = Join-Path $ProjectRoot "logs\heartbeat_monitor.log"
$MainHeartbeat = Join-Path $ProjectRoot "runtime\heartbeat.txt"
$WatchdogHeartbeat = Join-Path $ProjectRoot "runtime\watchdog_heartbeat.txt"
$PidFile = Join-Path $ProjectRoot "runtime\robot.pid"
$MainScript = Join-Path $ProjectRoot "main.py"
$RobotScript = Join-Path $ProjectRoot "scripts\robot.ps1"
$PythonExe = "C:\Users\saint\AppData\Local\Programs\Python\Python310\pythonw.exe"

function Write-Log {
    param([string]$Msg, [string]$Level = "INFO")
    $Line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Msg"
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    if ($Level -in @("WARN", "ERROR")) { Write-Warning $Line } else { Write-Host $Line }
}

function Get-FileAge {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $LastWrite = (Get-Item $Path).LastWriteTimeUtc
    return [int]([DateTime]::UtcNow - $LastWrite).TotalSeconds
}

function Restart-Robot {
    Write-Log "RESTART: delegating to robot.ps1 (kills old processes, clears flags, syncs clock, starts main.py)" -Level "WARN"
    # 🔧 FIX 07 Août 2026: délègue à robot.ps1 au lieu de relancer pythonw.exe en direct.
    # Le robot tourne en python.exe (lancé par robot.ps1), pas pythonw.exe — l'ancien
    # Restart-Robot ne tuait PAS le bon processus et créait un DOUBLON.
    # robot.ps1 gère : kill des instances existantes, cleanup lock stale, clear des
    # flags watchdog (stop/halt), nettoyage budget restarts, sync clock, lancement.
    try {
        $proc = Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$RobotScript`""
        ) -WindowStyle Hidden -PassThru
        # Donne à robot.ps1 le temps de lancer main.py (Start-Sleep 8s + vérif 3s)
        if (-not $proc.WaitForExit(30000)) {
            Write-Log "RESTART: robot.ps1 still running after 30s (process may be up)" -Level "WARN"
        } else {
            Write-Log "RESTART: robot.ps1 exited (code $($proc.ExitCode))"
        }
    } catch {
        Write-Log "RESTART FAILED: $_" -Level "ERROR"
    }
}

function Check-MainHeartbeat {
    $Age = Get-FileAge -Path $MainHeartbeat
    if ($Age -eq $null) {
        Write-Log "INFO: heartbeat.txt not found (robot may not have started)"
        return $false
    }
    if ($Age -gt $HeartbeatTimeout) {
        Write-Log "CHECK: main.py heartbeat stale ($Age`s > $HeartbeatTimeout`s)" -Level "WARN"
        return $false
    }
    return $true
}

function Check-WatchdogHeartbeat {
    $Age = Get-FileAge -Path $WatchdogHeartbeat
    if ($Age -eq $null) {
        Write-Log "INFO: watchdog_heartbeat.txt not found (watchdog may not have started)"
        return $null
    }
    if ($Age -gt $WatchdogTimeout) {
        Write-Log "CHECK: watchdog.py heartbeat stale ($Age`s > $WatchdogTimeout`s)" -Level "WARN"
        return $false
    }
    return $true
}

function Check-ProcessPid {
    if (-not (Test-Path $PidFile)) { return $false }
    $PidValue = Get-Content $PidFile -Raw | ForEach-Object { $_.Trim() }
    if (-not $PidValue) { return $false }
    $Proc = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
    return ($null -ne $Proc)
}

function Run-Check {
    Write-Log "=== CHECK ==="
    $MainOk = Check-MainHeartbeat
    $WatchdogOk = Check-WatchdogHeartbeat
    $ProcOk = Check-ProcessPid

    if (-not $MainOk -and $ProcOk) {
        Write-Log "STATUS: heartbeat stale but process alive (possible MT5 freeze)"
    } elseif (-not $MainOk -and -not $ProcOk) {
        Write-Log "STATUS: robot DEAD (no heartbeat, no process)" -Level "WARN"
        Restart-Robot
    } else {
        Write-Log "STATUS: OK (main=$MainOk watchdog=$WatchdogOk process=$ProcOk)"
    }
}

function Install-TaskScheduler {
    $TaskName = "MT5_Robot_HearbeatMonitor"
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 999)
    # 🔧 FIX 07 Août 2026: tourne en tant que saint (session interactive, comme la
    # tâche MT5_FTMO_DailyCheckpoint qui fonctionne) au lieu de SYSTEM. Le robot doit
    # être lancé dans la même session que le terminal MT5 (session utilisateur).
    $CurrentUser = "$env:COMPUTERNAME\$env:USERNAME"
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Highest
    # Le robot doit tourner 24/7 → désactive les restrictions batterie qui
    # empêcheraient le watchdog de se lancer quand le PC est sur batterie.
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Principal $Principal -Settings $Settings `
        -Description "MT5 FTMO Robot heartbeat monitor (external watchdog every 5 min)" -Force
    Write-Host "Task Scheduler entry '$TaskName' created (runs every 5 minutes as $CurrentUser)"
}

function Remove-TaskScheduler {
    $TaskName = "MT5_Robot_HearbeatMonitor"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Task Scheduler entry '$TaskName' removed"
}

# --- MAIN ---
# Ensure log directory exists
$LogDir = Split-Path $LogFile -Parent
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

if ($Install) { Install-TaskScheduler; return }
if ($Remove)  { Remove-TaskScheduler; return }
if ($Daemon) {
    Write-Log "Daemon mode started (check every ${CheckInterval}s)"
    while ($true) {
        Run-Check
        Start-Sleep -Seconds $CheckInterval
    }
} else {
    Run-Check
}
