param(
    [switch]$Status,
    [switch]$Stop
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG = "$BASE\logs\ai_manager.log"

function Log { param($msg) $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts - $msg" | Out-File -FilePath $LOG -Append; Write-Host "$ts - $msg" }

function Convert-Numeric {
    param($Value)
    if ($null -eq $Value) { return 0 }
    $s = $Value.ToString().Trim() -replace '[%$,]','' -replace '\s',''
    if ($s -eq '') { return 0 }
    try { return [double]$s } catch { return 0 }
}

# Fichier signal pour "stop journée" — créé par ai-manager, respecté par le robot
$STOP_DAY_FILE = "$BASE\runtime\stop_for_day.flag"

if ($Stop) {
    Log "AI Manager arrete"
    exit
}

if ($Status) {
    Write-Host "=== STATUS AI MANAGER ==="
    Write-Host "PID fichier: $(try { Get-Content "$BASE\runtime\ai_manager.pid" -ErrorAction Stop } catch { 'N/A' })"
    if (Test-Path $LOG) {
        Write-Host ""
        Write-Host "Dernieres entrees (5):"
        Get-Content $LOG -Tail 5
    } else { Write-Host "Pas de log" }
    Write-Host ""
    Write-Host "=== PROCESSUS ==="
    $robot = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "main\.py" }
    if ($robot) { 
        $rmb = [math]::Round($robot.WorkingSetSize/1MB, 1)
        Write-Host " Robot:    PID $($robot.ProcessId) - ACTIF (${rmb}MB)" 
    } else { 
        Write-Host " Robot:    ARRETE" 
    }
    Write-Host " Stop flag: $(if(Test-Path $STOP_DAY_FILE){'ACTIF (stop journee)'}else{'inactif'})"
    $oc = Get-CimInstance Win32_Process -Filter "Name='OpenCode.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -notmatch "--type=" }
    if ($oc) { Write-Host " OpenCode: PID $($oc.ProcessId) - ACTIF" } else { Write-Host " OpenCode: ARRETE" }
    
    # Auto-stop state
    $autoStateFile = "$BASE\runtime\auto_state.json"
    if (Test-Path $autoStateFile) {
        try {
            $as = Get-Content $autoStateFile -Raw | ConvertFrom-Json
            Write-Host " Auto-stop: $(if($as.auto_paused){'PAUSE jusqua ' + $as.auto_paused_until}else{'ACTIF'})"
        } catch {}
    }
    exit
}

# Sauvegarder notre PID
try { $PID | Out-File "$BASE\runtime\ai_manager.pid" -Force } catch {}

Log "=== AI Manager demarre ==="
Log "Mode: Surveillance robot MT5 + Auto-Stop + Watchdog Memoire"
Log "PID: $((Get-Process -Id $pid).Id)"
Log "Stop flag: $STOP_DAY_FILE"

$checkInterval = 120   # 2 min
$errorCount = 0
$maxErrorsBeforeAlert = 3
$script:heartbeatCount = 0
$restartCount = 0
$maxRestartsPerDay = 5
$lastRestartDate = (Get-Date).Date

while ($true) {
    $now = Get-Date
    $issues = @()

    # Reset compteur de restart si nouveau jour
    if ($now.Date -gt $lastRestartDate) {
        $restartCount = 0
        $lastRestartDate = $now.Date
    }

    # ── CHECK 1: Robot MT5 (python main.py) ──
    $robotProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main\.py" }
    if (-not $robotProc) {
        $issues += "ROBOT_ARRETE"
        Log "ALERTE: Robot MT5 ne tourne pas!"
    } else {
        # Vérifier le PID lock
        $pidFile = "$BASE\runtime\robot.pid"
        if (Test-Path $pidFile) {
            $savedPid = (Get-Content $pidFile -Raw).Trim()
            if ($savedPid -and $savedPid -ne $robotProc.ProcessId) {
                Log "WARN: PID lock ($savedPid) different du processus ($($robotProc.ProcessId))"
                Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
                $savedPid | Out-File $pidFile -Force
            }
        }
        
        # 🆕 Memory check — si >2GB, restart
        try {
            $memMB = [math]::Round($robotProc.WorkingSetSize / 1MB, 1)
            if ($memMB -gt 2000) {
                $issues += "MEMOIRE_ELEVEE"
                Log "ALERTE: Memoire robot ${memMB}MB > 2000MB → restart force"
            } elseif ($memMB -gt 1500) {
                Log "WARN: Memoire robot ${memMB}MB (alerte a 2000)"
            }
        } catch { Log "ERR mem: $_" }
    }

    # ── CHECK 2: Stop flag (créé si daily loss trop haut) ──
    $stopFlagExists = Test-Path $STOP_DAY_FILE
    if ($stopFlagExists) {
        $flagAge = ($now - (Get-Item $STOP_DAY_FILE).LastWriteTime).TotalMinutes
        if ($flagAge -gt 1440) {  # Flag > 24h → nettoyage auto
            Log "Nettoyage stop flag (age=$([math]::Round($flagAge))h)"
            Remove-Item $STOP_DAY_FILE -Force -ErrorAction SilentlyContinue
            $stopFlagExists = $false
        }
    }

    # ── CHECK 3: FTMO metrics ──
    $reportFile = "$BASE\runtime\ftmo_report.json"
    if (Test-Path $reportFile) {
        try {
            $report = Get-Content $reportFile -Raw | ConvertFrom-Json
        } catch {
            Log "ERREUR lecture json: $_"
            continue
        }
        try { $ddPct = Convert-Numeric $report.drawdown_pct } catch { $ddPct = 0 }
        try { $dailyPnl = Convert-Numeric $report.daily_pnl } catch { $dailyPnl = 0 }
        try { $dailyLossPct = Convert-Numeric $report.daily_loss_pct } catch { $dailyLossPct = 0 }
        try { $consecutiveLosses = Convert-Numeric $report.consecutive_losses } catch { $consecutiveLosses = 0 }
        try { $ftmoStatus = $report.status } catch { $ftmoStatus = "UNKNOWN" }

        try {
            if ($ftmoStatus -eq "FAILED_DD" -or $ftmoStatus -eq "FAILED_CONSISTENCY") {
                $issues += "FTMO_FAILED"
                Log "ALERTE CRITIQUE: Challenge $ftmoStatus"
            }
        } catch { Log "ERR check1: $_" }

        try {
            if ($ddPct -gt 8.0) {
                $issues += "DD_ELEVE"
                Log "ALERTE: Drawdown a $ddPct% (limite 10%)"
            }
        } catch { Log "ERR check2: $_" }

        # 🆕 Daily loss check — si >1.5%, créer stop flag pour la journée
        try {
            if ($dailyLossPct -gt 0.015) {
                $issues += "DAILY_LOSS"
                Log "ALERTE: Daily loss $($dailyLossPct*100)% > 1.5% → STOP journee"
                # Créer le flag de stop journée
                "Daily loss $($dailyLossPct*100)% le $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File $STOP_DAY_FILE -Force
                # Tuer le robot proprement
                if ($robotProc) {
                    Log "ACTION: Arret du robot pour aujourd'hui"
                    taskkill /F /PID $($robotProc.ProcessId) 2>&1 | Out-Null
                }
            }
        } catch { Log "ERR dl: $_" }

        # 🆕 Daily PnL check (montant)
        try {
            $dailyLossThreshold = -1500  # -$1,500
            if ($dailyPnl -lt $dailyLossThreshold) {
                $issues += "DAILY_LOSS_CASH"
                Log "ALERTE: Perte journaliere de $( [math]::Abs($dailyPnl) )$ > $( [math]::Abs($dailyLossThreshold) )$ → STOP journee"
                "$([math]::Abs($dailyPnl))$ loss le $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File $STOP_DAY_FILE -Force
                if ($robotProc) {
                    Log "ACTION: Arret du robot pour aujourd'hui"
                    taskkill /F /PID $($robotProc.ProcessId) 2>&1 | Out-Null
                }
            }
        } catch { Log "ERR dl2: $_" }

        try {
            if ($consecutiveLosses -ge 3) {
                Log "WARN: $consecutiveLosses pertes consecutives"
            }
        } catch { Log "ERR check4: $_" }
    }

    # ── CHECK 4: Logs robot ──
    $logFile = "$BASE\logs\simple_robot.log"
    if (Test-Path $logFile) {
        $lastModified = (Get-Item $logFile).LastWriteTime
        $ageMinutes = ($now - $lastModified).TotalMinutes
        if ($ageMinutes -gt 5 -and -not $stopFlagExists) {
            $issues += "LOGS_FIGES"
            Log "ALERTE: Logs figes depuis $([math]::Round($ageMinutes)) min"
        }
        $recentLines = Get-Content -Path $logFile -Tail 100 -ErrorAction SilentlyContinue
        if ($recentLines) {
            $errors = $recentLines | Select-String -Pattern "ERROR|CRITICAL|Traceback"
            if ($errors.Count -gt 0) {
                $lastError = $errors[-1].Line.Trim()
                Log "Erreurs: $($errors.Count) dans les 100 dernieres lignes — derniere: $lastError"
            }
        }
    } else {
        $issues += "PAS_DE_LOG"
    }

    # ── CHECK 5: Rotation des logs trop gros ──
    if (Test-Path $LOG) {
        $logSize = (Get-Item $LOG).Length
        if ($logSize -gt 5MB) {
            $sizeMb = [math]::Round($logSize/1MB, 1)
            $sizeMbStr = $sizeMb.ToString() + "MB"
            Log "Rotation log ai_manager ($sizeMbStr)"
            Move-Item $LOG "$LOG.old" -Force
        }
    }

    # ── ACTION ──
    if ($issues.Count -gt 0) {
        $errorCount++
        $issueStr = $issues -join ", "
        Log "Problemes: $issueStr (compteur=$errorCount)"

        if ($errorCount -ge $maxErrorsBeforeAlert) {
            # Ne PAS redémarrer si le flag stop journée est actif
            if ($stopFlagExists -or (Test-Path $STOP_DAY_FILE)) {
                Log "INFO: Stop journee actif → pas de redemarrage automatique"
            } elseif ($restartCount -ge $maxRestartsPerDay) {
                Log "LIMITE: $maxRestartsPerDay redemarrages aujourd'hui → pas de nouveau restart"
            } elseif ($issues -contains "ROBOT_ARRETE") {
                Log "ACTION: Redemarrage du robot MT5..."
                Remove-Item "$BASE\runtime\robot.pid" -Force -ErrorAction SilentlyContinue
                $env:PYTHONPATH = "$BASE"
                Start-Process -WindowStyle Hidden -FilePath "python.exe" -ArgumentList "main.py" -WorkingDirectory $BASE
                Start-Sleep -Seconds 5
                $restartCount++
                Log "Redemarrage initie (restart #$restartCount aujourd'hui)"
                $errorCount = 0
            } elseif ($issues -contains "MEMOIRE_ELEVEE") {
                Log "ACTION: Restart pour memoire elevee..."
                if ($robotProc) { taskkill /F /PID $($robotProc.ProcessId) 2>&1 | Out-Null }
                Start-Sleep -Seconds 2
                Remove-Item "$BASE\runtime\robot.pid" -Force -ErrorAction SilentlyContinue
                Start-Process -WindowStyle Hidden -FilePath "python.exe" -ArgumentList "main.py" -WorkingDirectory $BASE
                $restartCount++
                Log "Restart memoire initie (restart #$restartCount aujourd'hui)"
                $errorCount = 0
            }
        }
    } else {
        if ($errorCount -gt 0) { $errorCount-- }
    }

    # Heartbeat toutes les 10 min
    if (($script:heartbeatCount % 5) -eq 0) {
        $memVal = if ($robotProc) { try { [math]::Round($robotProc.WorkingSetSize/1MB,1) } catch { 0 } } else { 0 }
        $robotStatus = if ($robotProc) { "${memVal}MB" } else { "ARRETE" }
        $flagStatus = if ($stopFlagExists) { "STOP_JOUR" } else { "OK" }
        Log "Heartbeat: Robot=$robotStatus Flag=$flagStatus Restarts=$restartCount/$maxRestartsPerDay"
    }
    $script:heartbeatCount++

    Start-Sleep -Seconds $checkInterval
}
