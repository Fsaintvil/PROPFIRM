param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Logs,
    [switch]$Monitor
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG = "$BASE\logs\simple_robot.log"
$DAEMON_LOG = "$BASE\logs\agent_daemon_out.log"
$AGENT_STATUS = "$BASE\runtime\agent_status.json"

function Get-RobotProcess {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main.py" }
}

function Get-DaemonProcess {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "agent_daemon.py" }
}

if ($Stop) {
    Write-Host "=== ARRET ==="
    Get-RobotProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-DaemonProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    # Fallback: appeler --stop sur le daemon
    python "$BASE\scripts\agent_daemon.py" --stop 2>$null
    Start-Sleep -Seconds 2
    Write-Host "Robot + Agent Daemon arretes"
    exit
}

if ($Status) {
    Write-Host "=== STATUS ==="
    $rp = Get-RobotProcess
    $dp = Get-DaemonProcess
    Write-Host "Robot: $(if($rp){'✔ PID '+$rp.ProcessId}else{'✘ ARRETE'})"
    Write-Host "Agent Daemon: $(if($dp){'✔ PID '+$dp.ProcessId}else{'✘ ARRETE'})"
    if (Test-Path $AGENT_STATUS) {
        $ast = Get-Content $AGENT_STATUS | ConvertFrom-Json
        Write-Host "Council: cycle $($ast.cycle), niveau $($ast.global_level)"
    }
    if (Test-Path $LOG) {
        $last = Get-Content $LOG -Tail 1
        Write-Host "Dernier log robot: $last"
    }
    if (Test-Path "$BASE\runtime\ftmo_report.json") {
        Get-Content "$BASE\runtime\ftmo_report.json" | ConvertFrom-Json | Format-List
    }
    exit
}

if ($Logs) {
    if (Test-Path $LOG) {
        Get-Content $LOG -Tail 30
    } else {
        Write-Host "Pas de logs"
    }
    exit
}

if ($Monitor) {
    Write-Host "=== LOGS AGENT DAEMON ==="
    if (Test-Path $DAEMON_LOG) {
        Get-Content $DAEMON_LOG -Tail 20
    } else {
        Write-Host "Pas de logs daemon"
    }
    exit
}

# Default: start everything
Write-Host "=== DEMARRAGE ==="

# Kill any existing instances
Get-RobotProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-DaemonProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
python "$BASE\scripts\agent_daemon.py" --stop 2>$null
Start-Sleep -Seconds 2

# Start robot
Write-Host "Lancement du robot..." -NoNewline
Start-Process -FilePath "pythonw.exe" -ArgumentList "main.py" -WorkingDirectory $BASE -WindowStyle Hidden
Start-Sleep -Seconds 4
$rp = Get-RobotProcess
if ($rp) {
    Write-Host " PID $($rp.ProcessId)" -ForegroundColor Green
} else {
    Write-Host " ECHEC" -ForegroundColor Red
}

# Start agent daemon
Write-Host "Lancement de l'Agent Daemon (11 agents)..." -NoNewline
Start-Process -FilePath "python.exe" -ArgumentList "scripts\agent_daemon.py" -WorkingDirectory $BASE -WindowStyle Hidden
Start-Sleep -Seconds 4
$dp = Get-DaemonProcess
if ($dp) {
    Write-Host " PID $($dp.ProcessId)" -ForegroundColor Green
    if (Test-Path $AGENT_STATUS) {
        $ast = Get-Content $AGENT_STATUS | ConvertFrom-Json
        Write-Host "Council: cycle $($ast.cycle), niveau $($ast.global_level)"
    }
} else {
    Write-Host " ECHEC" -ForegroundColor Red
}

Write-Host ""
Write-Host "Commandes:"
Write-Host "  .\scripts\robot.ps1 -Status    -> voir etat"
Write-Host "  .\scripts\robot.ps1 -Logs     -> logs recents"
Write-Host "  .\scripts\robot.ps1 -Stop     -> arreter tout"
Write-Host "  .\scripts\robot.ps1 -Monitor  -> logs agent daemon"
