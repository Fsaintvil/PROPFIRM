param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Logs,
    [switch]$Monitor
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG = "$BASE\logs\simple_robot.log"
$MONITOR_LOG = "$BASE\logs\monitor.log"

function Get-RobotProcess {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main.py" }
}

function Get-MonitorProcess {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "monitor.py" }
}

if ($Stop) {
    Write-Host "=== ARRET ==="
    Get-RobotProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-MonitorProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Write-Host "Robot + Monitor arretes"
    exit
}

if ($Status) {
    Write-Host "=== STATUS ==="
    $rp = Get-RobotProcess
    $mp = Get-MonitorProcess
    Write-Host "Robot: $(if($rp){'✔ PID '+$rp.ProcessId}else{'✘ ARRETE'})"
    Write-Host "Monitor: $(if($mp){'✔ PID '+$mp.ProcessId}else{'✘ ARRETE'})"
    if (Test-Path $LOG) {
        $last = Get-Content $LOG -Tail 1
        Write-Host "Dernier log: $last"
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
    Write-Host "=== LOGS MONITOR ==="
    if (Test-Path $MONITOR_LOG) {
        Get-Content $MONITOR_LOG -Tail 20
    } else {
        Write-Host "Pas de logs monitor"
    }
    exit
}

# Default: start everything
Write-Host "=== DEMARRAGE ==="

# Kill any existing instances
Get-RobotProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-MonitorProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
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

# Start monitor
Write-Host "Lancement du moniteur..." -NoNewline
Start-Process -FilePath "python.exe" -ArgumentList "scripts\monitor.py" -WorkingDirectory $BASE -WindowStyle Hidden
Start-Sleep -Seconds 3
$mp = Get-MonitorProcess
if ($mp) {
    Write-Host " PID $($mp.ProcessId)" -ForegroundColor Green
} else {
    Write-Host " ECHEC" -ForegroundColor Red
}

Write-Host ""
Write-Host "Commandes:"
Write-Host "  .\scripts\robot.ps1 -Status    -> voir etat"
Write-Host "  .\scripts\robot.ps1 -Logs     -> logs recents"
Write-Host "  .\scripts\robot.ps1 -Stop     -> arreter tout"
Write-Host "  .\scripts\robot.ps1 -Monitor  -> logs moniteur"
