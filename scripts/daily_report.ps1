<#
.SYNOPSIS
    Rapport quotidien du robot MT5 FTMO — bilan complet
.DESCRIPTION
    Affiche le bilan actuel: challenge FTMO, performances par symbole,
    tendances glissantes, alertes et recommandations.
.PARAMETER Status
    Affiche juste le statut rapide (1 ligne)
.PARAMETER Watch
    Mode monitoring continu (rafraîchit toutes les 60s)
.EXAMPLE
    .\scripts\daily_report.ps1           # Rapport complet
    .\scripts\daily_report.ps1 -Status   # Statut rapide
    .\scripts\daily_report.ps1 -Watch    # Monitoring continu
#>
param(
    [switch]$Status,
    [switch]$Watch
)

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

if ($Status) {
    # Mode statut rapide — une seule ligne
    $report = Get-Content "$root\runtime\ftmo_report.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    if (-not $report) {
        Write-Host "[ERR] Robot arrete ou pas de rapport" -ForegroundColor Red
        exit 1
    }
    $pid_check = Get-Process -Id (Get-Content "$root\runtime\robot.pid" -ErrorAction SilentlyContinue) -ErrorAction SilentlyContinue
    $running = if ($pid_check) { "[OK]" } else { "[OFF]" }
    
    Write-Host "$running FTMO [$($report.status)] Balance: $($report.balance) Progress: $($report.profit_progress) DD: $($report.dd_from_peak) Trades: $($report.total_trades) Jours: $($report.trading_days)/30"
    exit 0
}

if ($Watch) {
    # Mode monitoring continu
    $count = 0
    while ($true) {
        Clear-Host
        Write-Host "=== MONITORING EN DIRECT == $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
        Write-Host "Appuyez sur Ctrl+C pour arreter`n" -ForegroundColor DarkGray
        
        # PID check
        $pid_path = "$root\runtime\robot.pid"
        if (Test-Path $pid_path) {
            $pid_val = Get-Content $pid_path -ErrorAction SilentlyContinue
            $proc = Get-Process -Id $pid_val -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "[OK] Robot actif (PID $pid_val, RAM: $([math]::Round($proc.WorkingSet64 / 1MB)) MB)" -ForegroundColor Green
            } else {
                Write-Host "[ERR] PID lock present mais processus mort" -ForegroundColor Red
            }
        } else {
            Write-Host "[ERR] Robot arrete" -ForegroundColor Red
        }
        
        # FTMO report
        $report = Get-Content "$root\runtime\ftmo_report.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
        if ($report) {
            Write-Host "`n=== CHALLENGE FTMO ===" -ForegroundColor Yellow
            Write-Host "  Status: $($report.status) | Balance: $($report.balance) | Equity: $($report.equity)"
            Write-Host "  Progression: $($report.profit_progress) | DD: $($report.dd_from_peak)"
            Write-Host "  Jours: $($report.trading_days)/30 | Trades: $($report.total_trades)"
        }
        
        # Heartbeat age
        $hb = Get-Content "$root\runtime\heartbeat.txt" -ErrorAction SilentlyContinue
        if ($hb) {
            try {
                $hb_time = [DateTime]::Parse($hb)
                $age = [int]((Get-Date) - $hb_time.ToLocalTime()).TotalSeconds
                if ($age -lt 30) {
                    Write-Host "`n[HEARTBEAT] $age s (OK)" -ForegroundColor Green
                } elseif ($age -lt 120) {
                    Write-Host "`n[HEARTBEAT] $age s (lent)" -ForegroundColor Yellow
                } else {
                    Write-Host "`n[HEARTBEAT] $age s (BLOQUE)" -ForegroundColor Red
                }
            } catch {
                Write-Host "`n[HEARTBEAT] inaccessible" -ForegroundColor DarkGray
            }
        }
        
        $count++
        Start-Sleep -Seconds 15
    }
    exit 0
}

# Mode rapport complet
Write-Host "=== GENERATION DU RAPPORT ===" -ForegroundColor Cyan
python "$root\scripts\daily_report.py" 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[OK] Rapport stocke dans runtime/daily_report.json" -ForegroundColor Green
} else {
    Write-Host "`n[ERR] Erreur lors de la generation du rapport" -ForegroundColor Red
}
