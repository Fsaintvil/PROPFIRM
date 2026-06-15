param(
    [switch]$Status,
    [switch]$Stop
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG = "$BASE\logs\council_daemon.log"
$PID_FILE = "$BASE\runtime\council_daemon.pid"

function Log { param($msg) $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts - $msg" | Out-File -FilePath $LOG -Append; Write-Host "$ts - $msg" }

if ($Stop) {
    if (Test-Path $PID_FILE) {
        $pid = (Get-Content $PID_FILE -Raw).Trim()
        if ($pid) {
            Log "Arrêt du Council Daemon (PID $pid)..."
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
            Log "Council Daemon arrêté."
        }
    } else {
        Log "Aucun PID trouvé."
    }
    # Nettoyer aussi les processus python qui tournent council_orchestrator
    Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "council_orchestrator" } | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        Log "Nettoyage PID $($_.Id)"
    }
    exit
}

if ($Status) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════╗"
    Write-Host "║    T R A D I N G   C O U N C I L    ║"
    Write-Host "║         État du Daemon               ║"
    Write-Host "╚══════════════════════════════════════╝"
    Write-Host ""
    if (Test-Path $PID_FILE) {
        $pid = (Get-Content $PID_FILE -Raw).Trim()
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Council Daemon: ✅ ACTIF (PID $pid)"
            Write-Host "  Mémoire:       $([math]::Round($proc.WorkingSet64/1MB,1)) MB"
            Write-Host "  CPU:           $([math]::Round($proc.CPU,1))s"
            Write-Host "  Démarrage:     $($proc.StartTime)"
        } else {
            Write-Host "  Council Daemon: ❌ PID FILE ORPHELIN ($pid)"
            Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "  Council Daemon: ❌ ARRÊTÉ"
    }

    # Dernier verdict
    $verdictFile = "$BASE\runtime\council\latest_verdict.json"
    if (Test-Path $verdictFile) {
        Write-Host ""
        Write-Host "  Dernier verdict:"
        try {
            $verdict = Get-Content $verdictFile -Raw | ConvertFrom-Json
            if ($verdict.final_verdict) {
                Write-Host "    Verdict:       $($verdict.final_verdict.verdict)"
                Write-Host "    Confiance:     $($verdict.final_verdict.confidence)"
                Write-Host "    Agents:        $($verdict.opinions.Count)"
                Write-Host "    Timestamp:     $($verdict.timestamp)"
            } else {
                Write-Host "    Verdict:       $($verdict.verdict)"
                Write-Host "    Timestamp:     $($verdict.timestamp)"
            }
        } catch {
            Write-Host "    (impossible de lire)"
        }
    }

    # Discussions récentes
    $councilLog = "$BASE\runtime\council\council_log.jsonl"
    if (Test-Path $councilLog) {
        $lines = (Get-Content $councilLog | Measure-Object -Line).Lines
        Write-Host "  Discussions:   $lines entrées"
        $lastLine = Get-Content $councilLog -Tail 1
        try {
            $last = $lastLine | ConvertFrom-Json
            Write-Host "  Dernière:      cycle $($last.cycle) — $($last.timestamp)"
        } catch {}
    }
    Write-Host ""
    exit
}

# ── DÉMARRAGE ──

Log "=== Council Daemon démarrage ==="

# Vérifier si déjà en cours
if (Test-Path $PID_FILE) {
    $oldPid = (Get-Content $PID_FILE -Raw).Trim()
    $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($oldProc) {
        Log "Council Daemon déjà actif (PID $oldPid). Utilisez -Stop d'abord."
        exit
    } else {
        Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
    }
}

# Créer le répertoire council
New-Item -ItemType Directory -Path "$BASE\runtime\council" -Force -ErrorAction SilentlyContinue | Out-Null

# Lancer le daemon Python
Log "Lancement du Council Orchestrator Python..."
$env:PYTHONPATH = $BASE
$pythonArgs = @(
    "-u",  # unbuffered output
    "$BASE\engine_simple\council_orchestrator.py"
)

try {
    $proc = Start-Process -WindowStyle Hidden -FilePath "python.exe" -ArgumentList $pythonArgs -WorkingDirectory $BASE -PassThru -ErrorAction Stop
    Start-Sleep -Seconds 3

    # Vérifier qu'il tourne
    if ($proc.HasExited) {
        Log "ERREUR: Le processus Python s'est arrêté immédiatement"
        # Vérifier les logs
        if (Test-Path "$BASE\logs\council.log") {
            $errLog = Get-Content "$BASE\logs\council.log" -Tail 5
            Log "Dernières lignes du log council:"
            $errLog | ForEach-Object { Log "  $_" }
        }
        exit 1
    }

    # Sauvegarder le PID
    $proc.Id | Out-File $PID_FILE -Force
    Log "Council Daemon démarré (PID $($proc.Id))"

    # Attendre le premier heartbeat
    Start-Sleep -Seconds 5
    if (Test-Path "$BASE\runtime\council\heartbeat.txt") {
        Log "Premier heartbeat confirmé."
    } else {
        Log "ATTENTION: Pas de heartbeat détecté."
    }

    Write-Host ""
    Write-Host "╔══════════════════════════════════════╗"
    Write-Host "║   TRADING INTELLIGENCE COUNCIL ACTIF ║"
    Write-Host "╚══════════════════════════════════════╝"
    Write-Host "  PID:        $($proc.Id)"
    Write-Host "  Cycle:      60s (monitor) / 5min (CIO) / 15min (Full Council)"
    Write-Host "  Agents:     15"
    Write-Host "  Skills:     6"
    Write-Host "  Log:        logs\council.log"
    Write-Host "  Discussions: runtime\council\"
    Write-Host "  Voir état:  .\scripts\council-daemon.ps1 -Status"
    Write-Host "  Arrêter:    .\scripts\council-daemon.ps1 -Stop"
    Write-Host ""

} catch {
    Log "ERREUR démarrage: $_"
    exit 1
}
