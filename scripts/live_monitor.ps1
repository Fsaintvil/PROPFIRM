<#
.SYNOPSIS
    Surveillance continue du robot MT5 FTMO — mise a jour toutes les 15s
.DESCRIPTION
    Affiche en direct : etat FTMO, daemon agents, heartbeat, dernieres lignes log.
    Tourne dans une fenetre separee, 24/7.
    Compatible PowerShell 5.1+.
#>

$root = "C:\Users\saint\Documents\MT5_FTMO_IA.7"

while ($true) {
    Clear-Host
    Write-Host "============================================" -ForegroundColor Cyan
    $now = Get-Date
    Write-Host "  MONITORING EN DIRECT - $($now.ToString('yyyy-MM-dd HH:mm:ss'))" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  Ferme cette fenetre pour arreter" -ForegroundColor DarkGray
    Write-Host ""

    # ---- PID Robot ----
    $pid_path = "$root\runtime\robot.pid"
    $pid_exists = Test-Path $pid_path
    if ($pid_exists) {
        $pid_val = Get-Content $pid_path -ErrorAction SilentlyContinue
        $proc = Get-Process -Id $pid_val -ErrorAction SilentlyContinue
        if ($proc) {
            $ram = [math]::Round($proc.WorkingSet64 / 1MB, 0)
            Write-Host "[ROBOT] OK - PID $pid_val (RAM: $ram MB)" -ForegroundColor Green
        } else {
            Write-Host "[ROBOT] PID lock present mais PROCESSUS MORT" -ForegroundColor Red
        }
    } else {
        Write-Host "[ROBOT] PAS DE PID - ARRETE" -ForegroundColor Red
    }

    # ---- Daemon PID ----
    $daemon_pid_path = "$root\runtime\agent_daemon.pid"
    $daemon_pid_exists = Test-Path $daemon_pid_path
    if ($daemon_pid_exists) {
        $dpid = Get-Content $daemon_pid_path -ErrorAction SilentlyContinue
        $dproc = Get-Process -Id $dpid -ErrorAction SilentlyContinue
        if ($dproc) {
            Write-Host "[DAEMON] OK - PID $dpid" -ForegroundColor Green
        } else {
            Write-Host "[DAEMON] PID lock present mais PROCESSUS MORT" -ForegroundColor Red
        }
    }

    # ---- FTMO Report ----
    $report_path = "$root\runtime\ftmo_report.json"
    $report_exists = Test-Path $report_path
    if ($report_exists) {
        $report_content = Get-Content $report_path -Raw -ErrorAction SilentlyContinue
        if ($report_content) {
            $report = $report_content | ConvertFrom-Json
            if ($report) {
                Write-Host ""
                Write-Host "=== CHALLENGE FTMO ===" -ForegroundColor Yellow
                Write-Host "  Status:       $($report.status)" -ForegroundColor White
                Write-Host "  Balance:      $($report.balance)" -ForegroundColor White
                Write-Host "  Equity:       $($report.equity)" -ForegroundColor White
                Write-Host "  PnL:          +$($report.pnl)" -ForegroundColor Green
                Write-Host "  Progress:     $($report.profit_progress)" -ForegroundColor White
                Write-Host "  DD from peak: $($report.dd_from_peak)" -ForegroundColor White
                Write-Host "  Daily PnL:    $($report.daily_pnl)" -ForegroundColor White
                Write-Host "  Trades:       $($report.total_trades) | WR: $($report.win_rate)" -ForegroundColor White
                Write-Host "  Jours:        $($report.trading_days) | Consec losses: $($report.consecutive_losses)" -ForegroundColor White
                if ($report.status -eq "ACTIVE") {
                    Write-Host "  >>> ACTIF <<<" -ForegroundColor Green
                }
            }
        }
    } else {
        Write-Host ""
        Write-Host "[ERR] Pas de rapport FTMO" -ForegroundColor Red
    }

    # ---- Agent Daemon Status ----
    $agent_path = "$root\runtime\agent_status.json"
    $agent_exists = Test-Path $agent_path
    if ($agent_exists) {
        $agent_content = Get-Content $agent_path -Raw -ErrorAction SilentlyContinue
        if ($agent_content) {
            $daemon = $agent_content | ConvertFrom-Json
            if ($daemon) {
                Write-Host ""
                Write-Host "=== AGENT DAEMON (Council) ===" -ForegroundColor Magenta
                $nivelColor = "White"
                if ($daemon.nivel -eq "GREEN") { $nivelColor = "Green" }
                if ($daemon.nivel -eq "ORANGE") { $nivelColor = "Yellow" }
                if ($daemon.nivel -eq "RED") { $nivelColor = "Red" }
                Write-Host "  Niveau: $($daemon.global_level) | Robot alive: $($daemon.robot_alive)" -ForegroundColor $nivelColor
                if ($daemon.agents) {
                    $agent_names = $daemon.agents.PSObject.Properties.Name
                    Write-Host "  Agents actifs: $($agent_names.Count)"
                    foreach ($agent_name in $agent_names) {
                        $agent = $daemon.agents.$agent_name
                        $color = "Green"
                        if ($agent.level -eq "ORANGE") { $color = "Yellow" }
                        if ($agent.level -eq "RED") { $color = "Red" }
                        Write-Host "    [$color] $agent_name : $($agent.level)" -ForegroundColor $color
                    }
                }
            }
        }
    }

    # ---- Heartbeat ----
    $hb_path = "$root\runtime\heartbeat.txt"
    $hb_exists = Test-Path $hb_path
    if ($hb_exists) {
        $hb_content = Get-Content $hb_path -ErrorAction SilentlyContinue
        if ($hb_content) {
            $hb_time = [DateTime]::Parse($hb_content)
            $age = [int]((Get-Date) - $hb_time.ToLocalTime()).TotalSeconds
            $hbColor = "Green"
            $hbMsg = "OK"
            if ($age -ge 30 -and $age -lt 120) {
                $hbColor = "Yellow"
                $hbMsg = "LENT"
            }
            if ($age -ge 120) {
                $hbColor = "Red"
                $hbMsg = "BLOQUE"
            }
            Write-Host ""
            Write-Host "[HEARTBEAT] $age s ($hbMsg)" -ForegroundColor $hbColor
        }
    }

    # ---- Dernieres lignes log ----
    $log_path = "$root\logs\simple_robot.log"
    $log_exists = Test-Path $log_path
    if ($log_exists) {
        $log = Get-Content $log_path -Tail 3 -ErrorAction SilentlyContinue
        if ($log) {
            Write-Host ""
            Write-Host "=== DERNIERS LOGS ===" -ForegroundColor Green
            foreach ($l in $log) {
                Write-Host "  $l" -ForegroundColor Gray
            }
        }
    }

    # ---- Positions check ----
    $state_path = "$root\runtime\robot_state.json"
    $state_exists = Test-Path $state_path
    if ($state_exists) {
        $state_content = Get-Content $state_path -Raw -ErrorAction SilentlyContinue
        if ($state_content) {
            $pos = $state_content | ConvertFrom-Json
            if ($pos -and $pos.open_positions -and $pos.open_positions.Count -gt 0) {
                Write-Host ""
                Write-Host "=== POSITIONS OUVERTES ($($pos.open_positions.Count)) ===" -ForegroundColor Blue
                foreach ($p in $pos.open_positions) {
                    Write-Host "  $($p.symbol) $($p.type) | Lots: $($p.volume) | PnL: $($p.profit)" -ForegroundColor White
                }
            }
        }
    }

    Write-Host ""
    Write-Host "============================================" -ForegroundColor DarkGray
    Write-Host "  Prochain raffraichissement dans 15s..." -ForegroundColor DarkGray

    Start-Sleep -Seconds 15
}
