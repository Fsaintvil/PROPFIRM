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

function Get-DaemonProcess {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "agent_daemon.py" }
}

if ($Stop) {
    Write-Host "=== ARRET ==="
    # Le daemon est le processus maître — il arrête le robot lui-même
    python "$BASE\scripts\agent_daemon.py" --stop 2>$null
    Get-DaemonProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 3
    Write-Host "Agent Daemon + Robot arretes"
    exit
}

if ($Status) {
    Write-Host "=== STATUS ==="
    $dp = Get-DaemonProcess
    if ($dp) {
        Write-Host "Agent Daemon: ACTIF (PID $($dp.ProcessId))" -ForegroundColor Green
    } else {
        Write-Host "Agent Daemon: ARRETE" -ForegroundColor Red
    }

    if (Test-Path $AGENT_STATUS) {
        $ast = Get-Content $AGENT_STATUS | ConvertFrom-Json
        $robotIcon = if ($ast.robot_alive) { "EN VIE" } else { "ARRETE" }
        Write-Host "Robot: $(if($ast.robot_alive){'[OK]'}else{'[XX]'}) $robotIcon"
        Write-Host "Council: cycle $($ast.cycle), niveau $($ast.global_level)"
        Write-Host ""
        Write-Host "-- Agents du Trading Intelligence Council --"
        foreach ($name in ($ast.agents.psobject.Properties).Name) {
            $a = $ast.agents.$name
            $icon = switch($a.level) { "GREEN" { "[G]" } "ORANGE" { "[O]" } "RED" { "[R]" } default { "[?]" } }
            Write-Host "  $icon $name`: $($a.message)"
        }
        # Messages inter-agents
        if ($ast.council_board -and $ast.council_board.Count -gt 0) {
            Write-Host ""
            Write-Host "-- Messages inter-agents recents --"
            foreach ($m in $ast.council_board[-5..-1]) {
                Write-Host "  [$($m.from) -> $($m.to)] $($m.message)"
            }
        }
    }

    if (Test-Path $LOG) {
        $last = Get-Content $LOG -Tail 1
        Write-Host ""
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
        # Fallback sur le log principal du daemon
        $mainLog = "$BASE\logs\agent_daemon.log"
        if (Test-Path $mainLog) {
            Get-Content $mainLog -Tail 20
        } else {
            Write-Host "Pas de logs daemon"
        }
    }
    exit
}

# Default: start the daemon (le daemon lance le robot comme sous-processus)
Write-Host "=== DEMARRAGE ==="

# Kill any existing instances
python "$BASE\scripts\agent_daemon.py" --stop 2>$null
Get-DaemonProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# Start daemon only — le daemon lance main.py automatiquement
Write-Host "Lancement de l'Agent Daemon (11 agents, maître du robot)..." -NoNewline
Start-Process -FilePath "python.exe" -ArgumentList "scripts\agent_daemon.py" -WorkingDirectory $BASE -WindowStyle Hidden
Start-Sleep -Seconds 5
$dp = Get-DaemonProcess
if ($dp) {
    Write-Host " PID $($dp.ProcessId)" -ForegroundColor Green
    Write-Host ""
    if (Test-Path $AGENT_STATUS) {
        $ast = Get-Content $AGENT_STATUS | ConvertFrom-Json
        Write-Host "  Council: cycle $($ast.cycle), niveau $($ast.global_level)"
        Write-Host "  Robot: $(if($ast.robot_alive){'OK - EN VIE'}else{'.. Démarrage...'})"
        Write-Host "  Agents: $(($ast.agents.psobject.Properties).Count) actifs"
    }
} else {
    Write-Host " ECHEC" -ForegroundColor Red
}

Write-Host ""
Write-Host "Commandes:"
Write-Host "  .\scripts\robot.ps1 -Status    -> etat complet (tous les agents + messages)"
Write-Host "  .\scripts\robot.ps1 -Logs      -> logs recents du robot"
Write-Host "  .\scripts\robot.ps1 -Stop      -> arreter daemon + robot"
Write-Host "  .\scripts\robot.ps1 -Monitor   -> logs du daemon"
