#!/usr/bin/env python3
"""Moniteur de fermeture des positions EURGBP + restart automatique.

Surveille les positions MT5 toutes les 30s via un sous-process Python isolé.
Quand positions=0 → arrête le robot → reset FAILED_DD → redémarre.
"""

import datetime
import json
import logging
import os
import signal
import subprocess
import sys
import time

# ── Config ──────────────────────────────────────────────────────────
POLL_INTERVAL = 30  # secondes entre chaque check de positions
TIMEOUT_HOURS = 24  # timeout max du moniteur
RESTART_DELAY = 5  # secondes entre stop et restart
HEALTH_CHECK_SECONDS = 30  # temps d'attente pour vérifier santé post-restart

# ── Paths ───────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(BASE_DIR, "runtime", "robot.pid")
STATE_FILE = os.path.join(BASE_DIR, "runtime", "robot_state.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "simple_robot.log")
MONITOR_LOG = os.path.join(BASE_DIR, "runtime", "monitor_restart.log")
ENV_FILE = os.path.join(BASE_DIR, ".env")
ENGINE_DIR = os.path.join(BASE_DIR, "engine_simple")

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(MONITOR_LOG, mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("monitor")


# ── Positions via sous-process Python isolé ─────────────────────────

GET_POS_SCRIPT = r"""
import sys, os
sys.path.insert(0, r'__ENGINE_DIR__')

with open(r'__ENV_FILE__') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            k, _, v = line.partition('=')
            os.environ[k.strip()] = v.strip()

import mt5_connector as mc
conn = mc.MT5Connector(
    login=int(os.environ.get('MT5_LOGIN', 0)),
    password=os.environ.get('MT5_PASSWORD', ''),
    server=os.environ.get('MT5_SERVER', '')
)
if not conn.connect():
    print('ERROR:MT5_CONNECT_FAIL')
    sys.exit(0)

pos = conn.get_positions()
if pos:
    for p in pos:
        print("POS:%s:%s:%s:%s:%s:%s:%s:%s" % (
            p.symbol, p.ticket, p.volume, p.price_open,
            p.price_current, p.sl, p.profit, p.time
        ))
    conn.disconnect()
else:
    print('NO_POSITIONS')
    conn.disconnect()
""".strip()


def get_positions_cmd():
    """Retourne la liste des positions via sous-process Python."""
    script = GET_POS_SCRIPT.replace("__ENGINE_DIR__", ENGINE_DIR).replace("__ENV_FILE__", ENV_FILE)
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=25,
            cwd=BASE_DIR,
        )
        output = result.stdout.strip()
        if not output or output == "NO_POSITIONS":
            return []
        if output.startswith("ERROR:MT5_CONNECT_FAIL"):
            return None  # signal d'erreur

        positions = []
        for line in output.split("\n"):
            if not line.startswith("POS:"):
                continue
            parts = line.split(":")
            if len(parts) >= 8:
                positions.append(
                    {
                        "symbol": parts[1],
                        "ticket": int(parts[2]),
                        "volume": float(parts[3]),
                        "price_open": float(parts[4]),
                        "price_current": float(parts[5]),
                        "sl": float(parts[6]),
                        "profit": float(parts[7]),
                        "time": int(parts[8]) if len(parts) > 8 else 0,
                    }
                )
        return positions
    except subprocess.TimeoutExpired:
        logger.error("Timeout get_positions (25s)")
        return None
    except Exception as e:
        logger.error(f"Erreur get_positions_cmd: {e}")
        return None


def count_positions(positions):
    """Compte les positions par symbole."""
    if not positions:
        return {}, 0, 0.0
    symbols = {}
    total_pnl = 0.0
    total_lots = 0.0
    for p in positions:
        s = p["symbol"]
        if s not in symbols:
            symbols[s] = {"count": 0, "lots": 0.0, "pnl": 0.0}
        symbols[s]["count"] += 1
        symbols[s]["lots"] += p["volume"]
        symbols[s]["pnl"] += p["profit"]
        total_pnl += p["profit"]
        total_lots += p["volume"]
    return symbols, total_lots, total_pnl


def get_robot_pid():
    """Lit le PID du robot depuis robot.pid."""
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def is_process_alive(pid):
    """Vérifie si un processus existe (cross-platform avec kill 0)."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def stop_robot(pid):
    """Arrête le robot proprement."""
    if not pid:
        logger.info("Aucun PID à arrêter")
        return

    logger.info(f"🛑 Arrêt du robot PID={pid}...")
    if is_process_alive(pid):
        # SIGTERM d'abord
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            logger.error(f"Erreur SIGTERM: {e}")

        for i in range(30):
            time.sleep(1)
            if not is_process_alive(pid):
                logger.info(f"PID {pid} arrêté ({i + 1}s)")
                break
        else:
            logger.warning(f"PID {pid} toujours vivant après 30s — taskkill")
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                time.sleep(2)
            except Exception as e:
                logger.error(f"Erreur taskkill: {e}")
    else:
        logger.info("PID déjà mort")

    # Nettoyer le PID file
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError as e:
        logger.warning(f"Nettoyage PID file: {e}")


def write_pid_file(pid):
    """Écrit le PID du robot."""
    try:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        logger.info(f"PID file écrit: {pid}")
    except OSError as e:
        logger.error(f"Erreur écriture PID file: {e}")


def start_robot():
    """Démarre main.py et retourne le PID."""
    main_py = os.path.join(BASE_DIR, "main.py")
    stdout_log = os.path.join(BASE_DIR, "runtime", "robot_stdout.log")

    logger.info(f"🚀 Démarrage du robot...")

    with open(stdout_log, "a") as out:
        out.write(f"\n--- RESTART {datetime.datetime.now().isoformat()} ---\n")

    # Lancer le processus
    try:
        proc = subprocess.Popen(
            [sys.executable, main_py],
            cwd=BASE_DIR,
            stdout=open(stdout_log, "a"),
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        logger.error(f"Erreur démarrage robot: {e}")
        return None

    logger.info(f"Process lancé: PID={proc.pid}")

    # Attendre que le PID file soit écrit (le robot crée le fichier au démarrage)
    for i in range(20):
        time.sleep(1)
        pid = get_robot_pid()
        if pid and is_process_alive(pid):
            logger.info(f"✅ PID file confirmé: {pid}")
            return pid

    # Fallback: si le process tourne mais PID file pas écrit
    if is_process_alive(proc.pid):
        logger.warning(f"⚠️ PID file non trouvé mais process {proc.pid} tourne")
        write_pid_file(proc.pid)
        return proc.pid

    logger.error("❌ Le robot n'a pas démarré")
    return None


def check_robot_healthy(new_pid):
    """Vérifie que le robot produit des logs et répond."""
    if not new_pid:
        return False
    if not is_process_alive(new_pid):
        logger.error("❌ Nouveau robot déjà mort!")
        return False

    # Vérifier que les logs grandissent
    if os.path.exists(LOG_FILE):
        size_before = os.path.getsize(LOG_FILE)
        time.sleep(HEALTH_CHECK_SECONDS)
        size_after = os.path.getsize(LOG_FILE)
        logger.info(f"Taille log: {size_before} → {size_after} bytes")
        if size_after > size_before:
            logger.info("✅ Logs en croissance — robot actif")
        else:
            logger.warning("⚠️ Logs stagnants — possible freeze")

    # Vérifier les positions
    pos = get_positions_cmd()
    if pos is None:
        logger.warning("⚠️ MT5 indisponible pour vérification positions")
    elif len(pos) > 0:
        syms, lots, pnl = count_positions(pos)
        logger.info(f"ℹ️ Positions après restart: {len(pos)}, PnL=${pnl:+.2f}")
        for s, d in sorted(syms.items()):
            logger.info(f"   {s}: {d['count']}p {d['lots']:.2f}L ${d['pnl']:+.2f}")
    else:
        logger.info("✅ Clean slate — aucune position")

    return True


def run_monitor():
    """Boucle principale de monitoring."""
    logger.info("=" * 60)
    logger.info("MONITEUR DE FERMETURE EURGBP + RESTART")
    logger.info(f"Démarrage: {datetime.datetime.now().isoformat()}")
    logger.info(f"Poll toutes les {POLL_INTERVAL}s, timeout {TIMEOUT_HOURS}h")
    logger.info("=" * 60)

    start_time = time.time()
    max_duration = TIMEOUT_HOURS * 3600

    last_log_time = 0
    last_eurgbp_count = -1

    while True:
        elapsed = time.time() - start_time
        if elapsed > max_duration:
            logger.warning(f"Timeout {TIMEOUT_HOURS}h atteint — arrêt moniteur")
            break

        # ── Vérifier les positions ──
        pos_list = get_positions_cmd()

        if pos_list is None:
            logger.warning("⚠️ MT5 indisponible, retry dans 30s")
            time.sleep(POLL_INTERVAL)
            continue

        symbols, total_lots, total_pnl = count_positions(pos_list)
        total_pos = len(pos_list)
        eurgbp = symbols.get("EURGBP", {"count": 0, "lots": 0.0, "pnl": 0.0})

        # Log seulement si changement significatif
        now = time.time()
        eurgbp_count = eurgbp["count"]
        should_log = (
            eurgbp_count != last_eurgbp_count or now - last_log_time > 300  # au moins toutes les 5 min
        )

        if should_log:
            log_parts = [f"📊 {total_pos} positions | {eurgbp_count} EURGBP"]
            for sym, data in sorted(symbols.items(), key=lambda x: -x[1]["pnl"]):
                log_parts.append(f"{sym}: {data['count']}p {data['lots']:.2f}L ${data['pnl']:+.2f}")
            log_parts.append(f"Total PnL=${total_pnl:+.2f}")
            logger.info(" | ".join(log_parts))
            last_log_time = now
            last_eurgbp_count = eurgbp_count

        # Condition d'arrêt : 0 positions
        if total_pos == 0:
            logger.info("🎯🎯🎯 TOUTES LES POSITIONS FERMÉES — déclenchement restart!")
            break

        time.sleep(POLL_INTERVAL)

    # ── PHASE DE RESTART ──────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("PHASE DE RESTART")
    logger.info("=" * 60)

    robot_pid = get_robot_pid()

    # 1. Arrêter le robot proprement
    if robot_pid:
        logger.info(f"Robot actif: PID={robot_pid}")
        stop_robot(robot_pid)
    else:
        logger.info("Aucun PID trouvé — vérification processus...")
        # Chercher un processus python exécutant main.py
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import psutil
for p in psutil.process_iter(['pid', 'cmdline']):
    try:
        cmd = ' '.join(p.info['cmdline'] or [])
        if 'main.py' in cmd:
            print(p.info['pid'])
    except:
        pass
""",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            pids = [int(pid) for pid in result.stdout.strip().split("\n") if pid.strip()]
            if pids:
                for pid in pids:
                    logger.info(f"Process python/main.py trouvé: PID={pid}")
                    stop_robot(pid)
            else:
                logger.info("Aucun process python/main.py trouvé")
        except Exception as e:
            logger.warning(f"Erreur scan processus: {e}")

    time.sleep(RESTART_DELAY)

    # 2. Reset FAILED_DD dans robot_state.json si présent
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            if state.get("challenge_status") == "FAILED_DD":
                state["challenge_status"] = "ACTIVE"
                with open(STATE_FILE, "w") as f:
                    json.dump(state, f)
                logger.info("✅ FAILED_DD reset → ACTIVE dans robot_state.json")
            else:
                logger.info(f"ℹ️ challenge_status: {state.get('challenge_status', 'NOT SET')}")
        except Exception as e:
            logger.warning(f"Erreur reset FAILED_DD: {e}")

    # 3. Nettoyer tout lock restant
    for lock_file in ["robot.pid", "robot.lock"]:
        lf = os.path.join(os.path.dirname(PID_FILE), lock_file)
        try:
            if os.path.exists(lf):
                os.remove(lf)
                logger.info(f"Nettoyé: {lf}")
        except OSError:
            pass

    # 4. Démarrer le robot
    logger.info("Démarrage du robot...")
    new_pid = start_robot()

    # 5. Vérifier la santé
    if new_pid:
        logger.info(f"Vérification santé du robot PID={new_pid}...")
        time.sleep(15)
        healthy = check_robot_healthy(new_pid)
        if healthy:
            logger.info("✅✅✅ RESTART RÉUSSI — robot opérationnel")
        else:
            logger.warning("⚠️⚠️⚠️ RESTART AVEC AVERTISSEMENTS — vérifier les logs")
    else:
        logger.error("❌❌❌ ÉCHEC RESTART — impossible de démarrer le robot")

    # 6. Rapport final
    logger.info("=" * 60)
    logger.info("RAPPORT FINAL")
    logger.info(f"Heure: {datetime.datetime.now().isoformat()}")
    logger.info(f"Log moniteur: {MONITOR_LOG}")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_monitor()
