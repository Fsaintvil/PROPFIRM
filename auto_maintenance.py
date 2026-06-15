#!/usr/bin/env python
"""
auto_maintenance.py — Agent de Maintenance Autonome du Robot MT5 FTMO.
Tourne en boucle (toutes les 60 min par défaut) et :
  1. Vérifie la santé du robot (PID, mémoire, .tmp orphelins)
  2. Surveille WR des 100 derniers trades → alerte si dégradation
  3. Vérifie la progression du challenge FTMO
  4. Nettoie les résidus de sessions crashées
  5. Log tout dans logs/auto_maintenance.log

Usage:
    python auto_maintenance.py                    # Run once
    python auto_maintenance.py --watch            # Run in loop (every 60 min)
    python auto_maintenance.py --interval 30      # Every 30 min
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────
RUNTIME_DIR = Path("runtime")
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "auto_maintenance.log"
STATE_FILE = RUNTIME_DIR / "robot_state.json"
FTMO_REPORT = RUNTIME_DIR / "ftmo_report.json"
PERF_HISTORY = RUNTIME_DIR / "performance_history.json"
PID_FILE = RUNTIME_DIR / "robot.pid"

MONITORING_INTERVAL = 60  # minutes
WR_ALERT_THRESHOLD = 45   # % WR sur 100 trades → alerte si en dessous
WR_CRITICAL_THRESHOLD = 35  # % WR sur 100 trades → CRITIQUE
MEMORY_ALERT_MB = 3500    # Alerte si > 3.5 GB
MEMORY_CRITICAL_MB = 4500 # Critique si > 4.5 GB

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MAINT:%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("auto_maintenance")


# ── Checks ──────────────────────────────────────────────────────────
def check_pid_health() -> dict:
    """Vérifie que le robot tourne et sa mémoire."""
    result = {"alive": False, "pid": None, "memory_mb": 0, "cpu_pct": 0.0, "uptime_h": 0}
    if not PID_FILE.exists():
        logger.warning("[PID] Fichier PID introuvable — robot probablement arrêté")
        return result

    try:
        pid_str = PID_FILE.read_text().strip()
        pid = int(pid_str)
        result["pid"] = pid
        # Vérifier si le processus existe
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = 1  # SW_SHOWNORMAL
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            startupinfo=startup_info
        )
        if str(pid) in r.stdout:
            result["alive"] = True
            logger.info(f"[PID] Robot vivant — PID {pid}")
        else:
            logger.warning(f"[PID] PID {pid} trouvé mais processus mort — stale lock")
            return result

        # Mémoire via psutil si disponible
        try:
            import psutil
            proc = psutil.Process(pid)
            result["memory_mb"] = proc.memory_info().rss / 1_048_576
            result["cpu_pct"] = proc.cpu_percent(interval=0.5)
            result["uptime_h"] = (time.time() - proc.create_time()) / 3600
            logger.info(f"[MEM] {result['memory_mb']:.0f} MB | CPU {result['cpu_pct']:.1f}% | Uptime {result['uptime_h']:.1f}h")

            if result["memory_mb"] > MEMORY_CRITICAL_MB:
                logger.critical(f"[MEM] Mémoire CRITIQUE: {result['memory_mb']:.0f} MB > {MEMORY_CRITICAL_MB} MB")
            elif result["memory_mb"] > MEMORY_ALERT_MB:
                logger.warning(f"[MEM] Mémoire élevée: {result['memory_mb']:.0f} MB > {MEMORY_ALERT_MB} MB")
        except ImportError:
            pass
    except (ValueError, OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"[PID] Erreur vérification PID: {e}")

    return result


def check_orphan_tmp_files() -> int:
    """Nettoie les fichiers .tmp orphelins — retourne le nombre supprimé."""
    cleaned = 0
    for pattern in ["*.tmp", "*.tmp.*"]:
        for f in Path(".").glob(pattern):
            try:
                f.unlink()
                cleaned += 1
                logger.info(f"[CLEAN] Supprimé: {f}")
            except Exception as e:
                logger.debug(f"[CLEAN] Erreur suppression {f}: {e}")
    if RUNTIME_DIR.exists():
        for f in RUNTIME_DIR.glob("*.tmp"):
            try:
                f.unlink()
                cleaned += 1
                logger.info(f"[CLEAN] Supprimé: {f}")
            except Exception as e:
                logger.debug(f"[CLEAN] Erreur suppression {f}: {e}")
    if cleaned:
        logger.info(f"[CLEAN] {cleaned} fichier(s) .tmp orphelin(s) nettoyé(s)")
    return cleaned


def check_performance_wr() -> dict:
    """Analyse le WR des derniers trades depuis performance_history.json."""
    result = {"wr_50": None, "wr_100": None, "wr_200": None, "total_trades": 0, "alert": False, "alert_level": "OK"}
    if not PERF_HISTORY.exists():
        logger.info("[PERF] Aucun historique de performance — pas encore de trades")
        return result

    try:
        data = json.loads(PERF_HISTORY.read_text())
        rolling = data.get("rolling", {})
        recent = data.get("recent_trades", [])
        result["total_trades"] = len(recent)

        for key in ["last_50", "last_100", "last_200"]:
            entry = rolling.get(key, {})
            wr = entry.get("wr")
            if wr is not None:
                result[f"wr_{key.split('_')[1]}"] = wr
                logger.info(f"[PERF] {key}: WR={wr:.1f}% ({entry.get('trades', 0)} trades, PnL=${entry.get('pnl', 0):.0f})")

        # Comparer WR 100 vs WR 50 pour détecter dégradation
        wr100 = result.get("wr_100")
        wr50 = result.get("wr_50")
        if wr100 is not None and wr50 is not None:
            diff = wr100 - wr50
            if diff > 15:
                logger.warning(f"[PERF] ⚠️ Dégradation WR: -{diff:.1f}% sur 50 trades (était {wr100:.1f}% → {wr50:.1f}%)")
                result["alert"] = True
                result["alert_level"] = "WARNING"

        # Alerte si WR < seuil
        if wr50 is not None and wr50 < WR_CRITICAL_THRESHOLD:
            logger.critical(f"[PERF] 🔴 WR critique: {wr50:.1f}% < {WR_CRITICAL_THRESHOLD}%")
            result["alert"] = True
            result["alert_level"] = "CRITICAL"
        elif wr100 is not None and wr100 < WR_ALERT_THRESHOLD:
            logger.warning(f"[PERF] ⚠️ WR bas: {wr100:.1f}% < {WR_ALERT_THRESHOLD}%")
            result["alert"] = True
            result["alert_level"] = "WARNING"

        # Vérifier si assez de trades pour analyse significative
        if result["total_trades"] < 20:
            logger.info(f"[PERF] Échantillon insuffisant: {result['total_trades']} trades (20 nécessaires pour analyse)")
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"[PERF] Erreur lecture historique: {e}")

    return result


def check_ftmo_progress() -> dict:
    """Vérifie la progression du challenge FTMO."""
    result = {"status": "UNKNOWN", "profit_pct": 0, "dd_pct": 0, "trading_days": 0, "alert": False}

    # 1. Depuis ftmo_report.json (màj en continu par le robot)
    if FTMO_REPORT.exists():
        try:
            data = json.loads(FTMO_REPORT.read_text())
            result["status"] = data.get("status", "UNKNOWN")
            pp = data.get("profit_progress", "0%")
            result["profit_pct"] = float(str(pp).replace("%", "").replace("+", ""))
            result["dd_pct"] = float(data.get("dd_from_peak", "0%").replace("%", ""))
            result["trading_days"] = int(data.get("trading_days", 0))
            logger.info(f"[FTMO] Status={result['status']} | Profit={result['profit_pct']:.1f}% | DD={result['dd_pct']:.1f}% | Jours={result['trading_days']}")
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"[FTMO] Erreur lecture rapport: {e}")

    # 2. Depuis robot_state.json (structure plus riche)
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            status = data.get("challenge_status", result["status"])
            if status in ("FAILED_DD", "FAILED_CONSISTENCY"):
                logger.critical(f"[FTMO] 🔴 CHALLENGE ÉCHOUÉ: {status}")
                result["alert"] = True
            elif status == "PASSED":
                logger.info("[FTMO] ✅ CHALLENGE RÉUSSI!")
            result["status"] = status

            # Nombre de trades total
            n_trades = data.get("total_trades", 0)
            total_pnl = data.get("total_profit", 0)
            logger.info(f"[FTMO] Trades={n_trades} | PnL=${total_pnl:.0f}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"[FTMO] Erreur état robot: {e}")

    return result


def check_disk_space() -> dict:
    """Vérifie l'espace disque du répertoire projet."""
    result = {"size_mb": 0, "log_size_mb": 0, "runtime_size_mb": 0}
    try:
        import shutil
        total = 0
        for root, dirs, files in os.walk("."):
            # Ignorer .venv, __pycache__, .git
            skip_dirs = {".venv", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        result["size_mb"] = total / 1_048_576

        # Logs uniquement
        log_size = sum(f.stat().st_size for f in LOG_DIR.glob("*.log") if f.is_file())
        result["log_size_mb"] = log_size / 1_048_576

        # Runtime
        rt_size = sum(f.stat().st_size for f in RUNTIME_DIR.glob("*") if f.is_file())
        result["runtime_size_mb"] = rt_size / 1_048_576

        logger.info(f"[DISK] Projet={result['size_mb']:.0f} MB | Logs={result['log_size_mb']:.1f} MB | Runtime={result['runtime_size_mb']:.1f} MB")

        if result["log_size_mb"] > 500:
            logger.warning(f"[DISK] Logs volumineux: {result['log_size_mb']:.0f} MB — envisager rotation")
    except Exception as e:
        logger.debug(f"[DISK] Erreur calcul espace: {e}")
    return result


def check_python_logs_recent_errors() -> int:
    """Compte les ERROR/CRITICAL dans simple_robot.log des dernières 24h."""
    errors = 0
    log_files = list(LOG_DIR.glob("*.log"))
    if not log_files:
        return 0
    newest = max(log_files, key=lambda f: f.stat().st_mtime)
    try:
        content = newest.read_text(encoding="utf-8", errors="ignore")
        lines = content.split("\n")
        # Prendre les 500 dernières lignes
        recent = lines[-500:]
        for line in recent:
            if "ERROR" in line or "CRITICAL" in line:
                if "auto_maintenance" not in line:  # Ne pas se compter soi-même
                    errors += 1
        if errors > 0:
            logger.warning(f"[LOGS] {errors} erreurs dans les 500 dernières lignes de {newest.name}")
    except Exception as e:
        logger.debug(f"[LOGS] Erreur lecture logs: {e}")
    return errors


def verify_state_consistency() -> dict:
    """Vérifie la cohérence entre les fichiers d'état."""
    result = {"consistent": True, "issues": []}
    try:
        # Vérifier que shield_state.json et robot_state.json sont synchronisés
        shield_file = RUNTIME_DIR / "shield_state.json"
        if STATE_FILE.exists() and shield_file.exists():
            main_state = json.loads(STATE_FILE.read_text())
            shield_state = json.loads(shield_file.read_text())
            if main_state.get("status") != shield_state.get("status"):
                result["consistent"] = False
                result["issues"].append(f"Status différent: main={main_state.get('status')} vs shield={shield_state.get('status')}")
            if main_state.get("consecutive_losses") != shield_state.get("consecutive_losses"):
                result["issues"].append(f"consecutive_losses différent: main={main_state.get('consecutive_losses')} vs shield={shield_state.get('consecutive_losses')}")
            if result["issues"]:
                for issue in result["issues"]:
                    logger.warning(f"[STATE] {issue}")
        else:
            logger.debug("[STATE] Fichiers d'état partiellement absents (normal au 1er démarrage)")
    except Exception as e:
        logger.debug(f"[STATE] Erreur vérification cohérence: {e}")
    return result


# ── Rapport ─────────────────────────────────────────────────────────
def generate_report(results: dict) -> str:
    """Génère un rapport texte consolidé."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "=" * 60,
        f"  RAPPORT AUTO-MAINTENANCE — {timestamp}",
        "=" * 60,
        "",
        f"  PID:    {'✅ VIVANT' if results['pid']['alive'] else '❌ MORT / Aucun'} (PID {results['pid']['pid'] or 'N/A'})",
        f"  Mémoire: {results['pid']['memory_mb']:.0f} MB / CPU {results['pid']['cpu_pct']:.1f}% / Uptime {results['pid']['uptime_h']:.1f}h",
        f"  Challenge: {results['ftmo']['status']} | Profit {results['ftmo']['profit_pct']:.1f}% | DD {results['ftmo']['dd_pct']:.1f}% | {results['ftmo']['trading_days']} jours",
        "",
        "  ── Performances ──",
    ]

    wr = results["wr"]
    if wr.get("wr_100") is not None:
        lines.append(f"  WR 100: {wr['wr_100']:.1f}% | WR 50: {wr.get('wr_50', 0):.1f}% | WR 200: {wr.get('wr_200', 0):.1f}%")
    lines.append(f"  Total trades analysés: {wr['total_trades']}")
    if wr["alert"]:
        lines.append(f"  ⚠️ ALERTE ({wr['alert_level']})")

    disk = results["disk"]
    lines.extend([
        "",
        f"  ── Disque ──",
        f"  Projet: {disk['size_mb']:.0f} MB | Logs: {disk['log_size_mb']:.1f} MB | Runtime: {disk['runtime_size_mb']:.1f} MB",
        f"  .tmp nettoyés: {results['cleaned_tmp']}",
        f"  Erreurs logs récentes: {results['log_errors']}",
    ])

    state = results["state"]
    if not state["consistent"]:
        lines.append(f"  ⚠️ État incohérent: {len(state['issues'])} divergence(s)")

    lines.append("=" * 60)
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────
def run_once() -> dict:
    """Exécute tous les checks une fois et retourne les résultats."""
    logger.info("── Début cycle de maintenance ──")
    results = {
        "pid": check_pid_health(),
        "wr": check_performance_wr(),
        "ftmo": check_ftmo_progress(),
        "disk": check_disk_space(),
        "cleaned_tmp": check_orphan_tmp_files(),
        "log_errors": check_python_logs_recent_errors(),
        "state": verify_state_consistency(),
    }

    report = generate_report(results)
    logger.info(f"\n{report}")
    logger.info("── Fin cycle de maintenance ──")
    return results


def watch_loop(interval_minutes: int):
    """Boucle infinie avec intervalle configurable."""
    logger.info(f"🚀 Mode WATCH activé — cycle toutes les {interval_minutes} min")
    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"[FATAL] Erreur dans cycle: {e}", exc_info=True)
        time.sleep(interval_minutes * 60)


def main():
    parser = argparse.ArgumentParser(description="Auto-Maintenance du Robot MT5 FTMO")
    parser.add_argument("--watch", action="store_true", help="Mode boucle continue")
    parser.add_argument("--interval", type=int, default=MONITORING_INTERVAL,
                        help=f"Intervalle en minutes (défaut: {MONITORING_INTERVAL})")
    parser.add_argument("--once", action="store_true", help="Exécution unique (défaut)")
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    RUNTIME_DIR.mkdir(exist_ok=True)

    if args.watch:
        watch_loop(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
