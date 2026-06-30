param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Logs,
    [switch]$Monitor,
    [switch]$Dashboard,
    [switch]$DashboardHtml,
    [switch]$LaunchMT5,
    [switch]$Watch
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG = "$BASE\logs\simple_robot.log"
$PID_FILE = "$BASE\runtime\robot.pid"
$FTMO_REPORT = "$BASE\runtime\ftmo_report.json"
$DASHBOARD_SCRIPT = "$BASE\scripts\daily_dashboard.py"
$MT5_PATH = "C:\Program Files\MetaTrader 5\terminal64.exe"

function Get-RobotProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main.py" }
}

function Get-RobotPid {
    if (Test-Path $PID_FILE) {
        try {
            $content = Get-Content $PID_FILE -Raw
            if ($content) { return [int]$content.Trim() }
        } catch { return $null }
    }
    return $null
}

function Test-ProcessAlive {
    param([int]$TargetPid)
    if (-not $TargetPid) { return $false }
    try {
        $p = Get-Process -Id $TargetPid -ErrorAction Stop
        return -not $p.HasExited
    } catch { return $false }
}

if ($Stop) {
    Write-Host "=== ARRET ==="
    $lockPid = Get-RobotPid
    if ($lockPid) {
        Write-Host "Robot PID $lockPid detected. Stopping..."
        $proc = Get-WmiObject Win32_Process -Filter "ProcessId=$lockPid" -ErrorAction SilentlyContinue
        if ($proc) { $proc.Terminate() | Out-Null }
    }
    Get-RobotProcess | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Killed PID $($_.ProcessId)"
    }
    $remaining = Get-Process -Name python -ErrorAction SilentlyContinue
    if ($remaining) {
        Write-Host "Cleaning $($remaining.Count) remaining python processes..."
        $remaining | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "Robot stopped"
    exit
}

if ($Status) {
    Write-Host "=== STATUS ==="
    $rp = Get-RobotProcess
    $lockPid = Get-RobotPid
    $alive = Test-ProcessAlive -TargetPid $lockPid

    if ($rp -or $alive) {
        $actualPid = if ($rp) { $rp.ProcessId } else { $lockPid }
        Write-Host "Robot: [OK] ACTIVE (PID $actualPid)" -ForegroundColor Green
    } else {
        Write-Host "Robot: [XX] STOPPED" -ForegroundColor Red
        if ($lockPid) { Write-Host "  Lock file: PID $lockPid (stale)" -ForegroundColor Yellow }
    }

    if (Test-Path $FTMO_REPORT) {
        Write-Host ""
        Write-Host "-- FTMO Report --"
        $rpt = Get-Content $FTMO_REPORT | ConvertFrom-Json
        Write-Host "  Balance:      $($rpt.balance)"
        Write-Host "  Equity:       $($rpt.equity)"
        Write-Host "  PnL:          $($rpt.pnl)"
        Write-Host "  DD from peak: $($rpt.dd_from_peak)"
        Write-Host "  Status:       $($rpt.status)"
        Write-Host "  Win Rate:     $($rpt.win_rate)"
        Write-Host "  Consec Loss:  $($rpt.consecutive_losses)"
        Write-Host "  Trading Days: $($rpt.trading_days)"
    }

    if (Test-Path $LOG) {
        $last = Get-Content $LOG -Tail 1
        Write-Host ""
        Write-Host "Last log line: $last"
    }

    $posLine = Get-Content $LOG -Tail 20 | Select-String -Pattern "Positions.*Par symbole" | Select-Object -Last 1
    if ($posLine) {
        Write-Host ""
        Write-Host "-- Positions --"
        Write-Host $posLine.Line
    }
    exit
}

if ($Logs) {
    if (Test-Path $LOG) {
        Get-Content $LOG -Tail 50
    } else {
        Write-Host "No logs found"
    }
    exit
}

if ($Monitor) {
    Write-Host "=== LOGS ==="
    if (Test-Path $LOG) {
        Get-Content $LOG -Tail 20
    } else {
        Write-Host "No logs found"
    }
    exit
}

if ($Dashboard) {
    Write-Host "=== DASHBOARD ==="
    python "$DASHBOARD_SCRIPT"
    exit
}

if ($DashboardHtml) {
    Write-Host "=== GENERATION DASHBOARD HTML ==="
    python "$DASHBOARD_SCRIPT" --html
    exit
}

if ($Watch) {
    Write-Host "=== DASHBOARD WATCH ==="
    python "$DASHBOARD_SCRIPT" --watch
    exit
}

if ($LaunchMT5) {
    Write-Host "=== LANCEMENT MT5 ==="
    if (Test-Path $MT5_PATH) {
        Start-Process -FilePath $MT5_PATH
        Write-Host "MT5 Terminal lancé" -ForegroundColor Green
    } else {
        Write-Host "MT5 introuvable: $MT5_PATH" -ForegroundColor Red
    }
    exit
}

# Default: start
Write-Host "=== DEMARRAGE ==="

# Kill existing instances
$existing = Get-RobotProcess
if ($existing) {
    Write-Host "Existing instance found (PID $($existing.ProcessId)). Stopping..."
    $existing | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 3
}

# Clean stale lock file
$stalePid = Get-RobotPid
if ($stalePid -and -not (Test-ProcessAlive -Pid $stalePid)) {
    Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
    Write-Host "Lock file cleaned (stale PID $stalePid)"
}

# Optional: launch MT5 first
$autoMt5 = $env:ROBOT_LAUNCH_MT5 -eq "1"
if ($autoMt5) {
    Write-Host ""
    Write-Host "Launching MT5 (ROBOT_LAUNCH_MT5=1)..."
    if (Test-Path $MT5_PATH) {
        Start-Process -FilePath $MT5_PATH
        Start-Sleep -Seconds 5
    } else {
        Write-Host "  MT5 introuvable" -ForegroundColor Yellow
    }
}

# Launch main.py
Write-Host "Starting robot (main.py)..." -NoNewline
Start-Process -WindowStyle Hidden -FilePath "python.exe" -ArgumentList "main.py" -WorkingDirectory $BASE
Start-Sleep -Seconds 8

# Verify
$rp = Get-RobotProcess
if ($rp) {
    Write-Host " OK" -ForegroundColor Green
    Write-Host "  PID: $($rp.ProcessId)"
    Start-Sleep -Seconds 3
    $pidFromLock = Get-RobotPid
    if ($pidFromLock) { Write-Host "  Lock file: PID $pidFromLock" }
    if (Test-Path $FTMO_REPORT) {
        $rpt = Get-Content $FTMO_REPORT | ConvertFrom-Json
        Write-Host "  Balance: $($rpt.balance) | DD: $($rpt.dd_from_peak) | Status: $($rpt.status)"
    }
} else {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "Last log lines:" -ForegroundColor Yellow
    if (Test-Path $LOG) { Get-Content $LOG -Tail 5 }
}

Write-Host ""
Write-Host "Commands:"
Write-Host "  .\scripts\robot.ps1                 -> start robot"
Write-Host "  .\scripts\robot.ps1 -Status         -> full status"
Write-Host "  .\scripts\robot.ps1 -Logs           -> recent logs"
Write-Host "  .\scripts\robot.ps1 -Stop           -> stop robot"
Write-Host "  .\scripts\robot.ps1 -Monitor        -> recent logs"
Write-Host "  .\scripts\robot.ps1 -Dashboard      -> rapport console"
Write-Host "  .\scripts\robot.ps1 -DashboardHtml  -> rapport HTML"
Write-Host "  .\scripts\robot.ps1 -Watch          -> monitoring continu"
Write-Host "  .\scripts\robot.ps1 -LaunchMT5      -> lancer MT5"
Write-Host ""
Write-Host "Env vars:"
Write-Host "  `$env:ROBOT_LAUNCH_MT5=1  -> MT5 auto au démarrage"
