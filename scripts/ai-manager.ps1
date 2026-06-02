param(
    [switch]$Status,
    [switch]$Stop
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG = "$BASE\logs\ai_manager.log"

function Log { param($msg) $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts - $msg" | Out-File -FilePath $LOG -Append; Write-Host "$ts - $msg" }

if ($Stop) {
    Log "AI Manager arrete"
    exit
}

if ($Status) {
    Write-Host "=== STATUS AI MANAGER ==="
    if (Test-Path $LOG) { Write-Host "Dernieres entrees:"; Get-Content $LOG -Tail 5 }
    else { Write-Host "Pas de log" }
    exit
}

Log "=== AI Manager demarre ==="
Log "Mode: Surveillance autonome du robot MT5 FTMO"
Log "PID: $((Get-Process -Id $pid).Id)"

$checkInterval = 120  # 2 minutes entre chaque check
$errorCount = 0
$maxErrorsBeforeAlert = 3

while ($true) {
    $now = Get-Date
    $issues = @()

    # Check 1: Robot process
    $robotProc = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main\.py" }
    if (-not $robotProc) {
        $issues += "ROBOT_ARRETE"
        Log "ALERTE: Robot ne tourne pas!"
    }

    # Check 2: PID lock
    $pidFile = "$BASE\runtime\robot.pid"
    if (Test-Path $pidFile) {
        $savedPid = Get-Content $pidFile -Raw | ForEach-Object { $_.Trim() }
        $alive = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
        if (-not $alive -and $savedPid) {
            $issues += "PID_LOCK_ZOMBIE"
            Log "ALERTE: PID lock zombie ($savedPid) — nettoyage necessaire"
        }
    }

    # Check 3: Logs recents
    $logFile = "$BASE\logs\simple_robot.log"
    if (Test-Path $logFile) {
        $lastModified = (Get-Item $logFile).LastWriteTime
        $ageMinutes = ($now - $lastModified).TotalMinutes
        if ($ageMinutes -gt 5) {
            $issues += "LOGS_FIGES"
            Log "ALERTE: Logs figes depuis $([math]::Round($ageMinutes)) min"
        }
        $recentLines = Get-Content -Path $logFile -Tail 100
        $errors = $recentLines | Select-String -Pattern "ERROR|CRITICAL|Traceback"
        if ($errors.Count -gt 0) {
            $lastError = $errors[-1].Line
            Log "Erreurs recentes detectees: $($errors.Count) dans les 100 dernieres lignes"
        }
    } else {
        $issues += "PAS_DE_LOG"
    }

    # Check 4: FTMO metrics
    $reportFile = "$BASE\runtime\ftmo_report.json"
    if (Test-Path $reportFile) {
        try {
            $report = Get-Content $reportFile -Raw | ConvertFrom-Json
            $ddPct = $report.drawdown_pct
            if ($ddPct -gt 8.0) {
                $issues += "DD_ELEVE"
                Log "ALERTE: Drawdown a $ddPct%"
            }
        } catch { }
    }

    # Action
    if ($issues.Count -gt 0) {
        $errorCount++
        $issueStr = $issues -join ", "
        Log "Problemes: $issueStr (count=$errorCount)"

        if ($errorCount -ge $maxErrorsBeforeAlert) {
            if ($issues -contains "ROBOT_ARRETE" -or $issues -contains "PID_LOCK_ZOMBIE") {
                Log "ACTION: Tentative de redemarrage du robot..."
                Remove-Item "$BASE\runtime\robot.pid" -Force -ErrorAction SilentlyContinue
                $env:PYTHONPATH = "$BASE"
                Start-Process -WindowStyle Hidden -FilePath "python.exe" -ArgumentList "main.py" `
                    -WorkingDirectory $BASE
                Start-Sleep -Seconds 5
                Log "Redemarrage initie, verification dans 30s..."
                $errorCount = 0
            }
        }
    } else {
        if ($errorCount -gt 0) { $errorCount-- }
    }

    Start-Sleep -Seconds $checkInterval
}
