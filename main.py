"""Orchestrateur MT5 FTMO MOM20x3 — démarrage, arrêt, signal handling, PID lock.

Le TradingEngine (boucle de trading, positions, signaux, calibration) est dans
engine_simple/trading_engine.py. Ce fichier gère uniquement l'orchestration :
- PID lock / mutex Windows (anti-doublon)
- Signal handler SIGTERM/SIGINT (arrêt propre)
- Logging setup (rotation, format)
- _clean_orphan_tmp_files (nettoyage pré-démarrage)
- main() entry point
"""

import json
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from engine_simple.trading_engine import TradingEngine, _acquire_lock, _release_lock

# ── Logging setup ─────────────────────────────────────────────────────
log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
handler = RotatingFileHandler(
    "logs/simple_robot.log",
    maxBytes=10_485_760,
    backupCount=14,
    encoding="utf-8",
)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logging.getLogger("graphviz").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("font_manager").setLevel(logging.WARNING)
logger = logging.getLogger("robot")


# ── Nettoyage des .tmp orphelins (pré-démarrage) ─────────────────────
def _clean_orphan_tmp_files(glob_pattern="*.tmp"):
    """H-04: Nettoie les fichiers .tmp orphelins de sessions crashées."""
    import glob as _glob

    for f in _glob.glob(glob_pattern):
        try:
            Path(f).unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[CLEAN] Orphelin .tmp ignoré: {f} ({e})")
    # Nettoie aussi dans runtime/
    runtime_dir = Path("runtime")
    if runtime_dir.exists():
        for f in runtime_dir.glob("*.tmp"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")
        for f in runtime_dir.glob("*.json.tmp.*"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")
        for f in runtime_dir.glob("*.json.tmp"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")


# ── Nettoyage des flags watchdog (pré-démarrage) ──────────────────────
def _clean_watchdog_flags():
    """Nettoie les flags d'arrêt laissés par la session précédente.

    Un robot.stop.flag / robot.halt.flag résiduel désactive le watchdog
    externe (scripts/process_watchdog.py → "Auto-restart disabled, monitoring
    only"), l'empêchant de ressusciter le robot en cas de gel/crash
    (gel 1h52 le 20/08 : 05:21→07:13 sans résurrection car robot.stop.flag
    datait de 19/08 23:52).

    Pattern aligné sur scripts/robot.ps1 (L.197-205) : on nettoie
    robot.stop.flag ET robot.halt.flag — un démarrage = volonté explicite de
    relancer (comme un lancement via robot.ps1).

    ⚠️ stop_for_day.flag N'EST PAS nettoyé : il est écrit par le kill-switch /
    ai-manager pour arrêter le trading pour la journée. Le nettoyer ici
    annulerait une décision de stop d'urgence (DD>10%, daily loss>1.8%) si le
    robot était relancé manuellement (Start-Process python main.py). C'est le
    watchdog/ai-manager qui le retire quand la condition de stop est levée.
    """
    runtime_dir = Path("runtime")
    if not runtime_dir.exists():
        return
    for name in ("robot.stop.flag", "robot.halt.flag"):
        flag = runtime_dir / name
        try:
            if flag.exists():
                flag.unlink(missing_ok=True)
                logger.info(f"[CLEAN] Flag watchdog résiduel supprimé: {flag}")
        except Exception as e:
            logger.debug(f"[CLEAN] Flag watchdog ignoré: {flag} ({e})")


# ── Signal handler for graceful shutdown ──────────────────────────────
_shutdown_requested = False
_robot_instance: "FTMO_SIMPLE | None" = None


def _signal_handler(signum, frame):
    """SIGTERM/SIGINT handler — graceful shutdown with position cleanup."""
    global _shutdown_requested
    if _shutdown_requested:
        return
    _shutdown_requested = True
    sig_name = signal.Signals(signum).name
    logger.warning(f"[SIGNAL] Reçu {sig_name} — arrêt propre en cours...")
    robot = _robot_instance
    if robot is not None:
        try:
            robot.stop()
        except Exception as e:
            logger.error(f"[SIGNAL] Erreur pendant stop(): {e}")


# ── Classe orchestrateur ──────────────────────────────────────────────
class FTMO_SIMPLE(TradingEngine):
    """Orchestrateur FTMO — héritage complet du TradingEngine.

    Toute la logique de trading (boucle, positions, signaux, calibration)
    est dans TradingEngine. Cette classe est un alias pour l'orchestration :
    - Le signal handler peut appeler stop() sur l'instance
    - main() crée l'instance et gère le PID lock
    """

    pass


# ── Entry point ───────────────────────────────────────────────────────
def main():
    Path("logs").mkdir(exist_ok=True)
    Path("runtime").mkdir(exist_ok=True)
    _clean_orphan_tmp_files()
    # 🔧 FIX 20 Août 2026 (Auto-Fixer): nettoyer les flags watchdog résiduels
    # AVANT robot.start() — un robot.stop.flag daté désactiverait la
    # résurrection du watchdog externe (gel 1h52 le 20/08). Ne nettoie PAS
    # stop_for_day.flag (décision kill-switch conservée).
    _clean_watchdog_flags()

    # 🐛 FIX C3: Enregistrer les handlers SIGTERM/SIGINT pour arrêt propre
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    _acquire_lock()
    try:
        global _robot_instance
        robot = FTMO_SIMPLE()
        _robot_instance = robot
        robot.start()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
