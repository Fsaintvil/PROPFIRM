#!/usr/bin/env python3
"""Process-level watchdog for MT5 FTMO Robot.

PROBLEM: The thread-based watchdog (external watchdog thread in main.py) cannot
run when MT5 blocks the Python GIL (Global Interpreter Lock) during C-extension
API calls (copy_rates_from_pos, positions_get, order_send, etc.). This causes
the robot to freeze for hours (observed: 4h30, 12h25 blocks).

SOLUTION: A completely separate PROCESS that:
1. Is spawned at startup by main.py (engine_simple/trading_engine.py)
2. Monitors the heartbeat file every 30 seconds
3. If heartbeat is stale (> timeout), kills the main process via taskkill /F
4. Windows auto-releases the named mutex when the process dies
5. Spawns a new main.py instance to replace the killed one

🔧 FIX 03 Aout 2026 (auto-resurrection):
The OLD watchdog called `sys.exit(0)` when the target process DIED (not frozen).
This meant that if the robot crashed (e.g. MT5 connection abandoned after 10
retries -> "IPC timeout" -> process exit), NOTHING resurrected it. The robot
stayed dead for days (observed: dead for ~2 days, 02/08 14:04 -> 03/08 00:28).

The robot was only brought back by a MANUAL `robot.ps1`. Root cause: the
watchdog only detected GIL freezes (alive + stale heartbeat), but not full
process death.

NEW BEHAVIOR:
A. Health checks:
   - Process ALIVE + heartbeat FRESH  -> OK
   - Process ALIVE + heartbeat STALE  -> GIL freeze -> kill + restart
   - Process DEAD                     -> crash -> restart (NEW)

B. Anti-resurrection loop / anti-storm protections:
   - A graceful-shutdown flag (runtime/robot.stop.flag) is written by
     `robot.ps1 -Stop` BEFORE killing. If present, the watchdog does NOT
     resurrect (respects intentional shutdown).
   - A restart cooldown (RESTART_COOLDOWN_S, default 30s) prevents a tight
     kill/restart loop.
   - A restart budget (MAX_RESTARTS in RESTART_WINDOW) halts resurrection
     after too many rapid crashes (e.g. MT5 permanently unreachable) and
     writes a HALT flag for operator attention.

C. The restarting watchdog hands control back: the newly-spawned main.py
   launches a FRESH watchdog of its own (via trading_engine line ~1110).
   The old (this) watchdog then exits, so there is never more than one
   watchdog per robot generation.

USAGE:
    python scripts/process_watchdog.py <PID> <heartbeat_file> [timeout_seconds]

This script is intentionally simple and has ZERO dependencies on the robot's
internal modules (no MT5 imports, no robot imports). It is a standalone process.
"""

import os
import sys
import time
import subprocess
import signal
from datetime import datetime
from pathlib import Path

# ── Auto-restart guard constants ────────────────────────────────────────────
RESTART_COOLDOWN_S = int(os.environ.get("ROBOT_WATCHDOG_COOLDOWN_S", "30"))  # min between respawns
RESTART_WINDOW_S = int(os.environ.get("ROBOT_WATCHDOG_WINDOW_S", "600"))  # 10-min window
MAX_RESTARTS = int(os.environ.get("ROBOT_WATCHDOG_MAX_RESTARTS", "5"))  # restarts allowed in window

# 🔧 FIX 17 Août 2026: le watchdog écrit AUSSI dans un fichier dédié (log durable).
# Problème observé : quand le robot parent meurt, le handle stderr hérité via Popen
# (stdout=_wd_err, stderr=_wd_err dans trading_engine) peut devenir invalide → les
# logs de résurrection "CRITICAL DEAD"/"Spawned" disparaissent dans le vide. On ne
# peut plus diagnostiquer pourquoi le watchdog n'a pas relancé. Désormais chaque
# watchdog ouvre SON PROPRE fichier runtime/watchdog_pid.log (append) et loggue en
# parallèle stderr + fichier. Si un futur crash ne relance pas, la preuve existera.
_WATCHDOG_LOG_DIR = Path(__file__).resolve().parent.parent / "runtime"
_WATCHDOG_FILE_HANDLE = None
_expected_creation_time = None  # 🔧 FIX 17 Août 2026: création attendue du process cible (anti PID reuse)


def _get_process_creation_time(pid: int):
    """Retourne le temps de création (FILETIME) d'un process Windows, ou None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
    if not handle:
        return None
    try:
        from ctypes import c_ulonglong, byref

        creation = c_ulonglong(0)
        exit_t = c_ulonglong(0)
        kernel_t = c_ulonglong(0)
        user_t = c_ulonglong(0)
        if kernel32.GetProcessTimes(
            handle, byref(creation), byref(exit_t), byref(kernel_t), byref(user_t)
        ):
            return creation.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def _init_file_log() -> None:
    """Ouvre (append) le fichier de log dédié à CE watchdog (par PID)."""
    global _WATCHDOG_FILE_HANDLE
    try:
        _WATCHDOG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _WATCHDOG_FILE_HANDLE = open(
            _WATCHDOG_LOG_DIR / f"watchdog_{os.getpid()}.log",
            "a",
            encoding="utf-8",
        )
    except Exception:
        _WATCHDOG_FILE_HANDLE = None


def log(msg: str, level: str = "INFO") -> None:
    """Simple logging to stderr AND dedicated file (never interferes with main output)."""
    line = f"[WATCHDOG_PROC] {level} {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    if _WATCHDOG_FILE_HANDLE is not None:
        try:
            _WATCHDOG_FILE_HANDLE.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
            _WATCHDOG_FILE_HANDLE.flush()
        except Exception:
            pass


def get_process_status(pid: int) -> bool:
    """Check if a process is alive on Windows."""
    if os.name == "nt":
        # 🔧 FIX 06 Août 2026: remplace tasklist par OpenProcess (ctypes).
        # Le fix du 05/08 (errors="replace") ne suffisait PAS : le reader thread
        # de subprocess décode quand même la sortie tasklist (cp1252) en UTF-8
        # → UnicodeDecodeError → result.stdout=None → TypeError "NoneType is not
        # iterable" → catch → return True (assume alive) → le watchdog NE
        # DÉTECTAIT PLUS les crashes et ne ressuscitait JAMAIS le robot.
        # OpenProcess est fiable (pas de parsing de sortie, pas d'encodage),
        # 100× plus rapide et retourne un booléen sans ambiguïté.
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_INVALID_PARAMETER = 87  # PID inexistant
        handle = None
        kernel32 = None
        try:
            # use_last_error=True → ctypes.get_last_error() retourne le vrai
            # code d'erreur Windows (sinon il renvoie 0 → mauvais jugement).
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
            if not handle:
                err = ctypes.get_last_error()
                if err == ERROR_INVALID_PARAMETER:
                    return False  # Le PID n'existe pas → processus mort
                return True  # Autre erreur → conservateur (assume vivant)
            # 🔧 FIX 17 Août 2026: vérifier l'IDENTITÉ du process derrière le PID.
            # Scénario observé: le robot 3060 tué à 18:07:49, mais OpenProcess(3060)
            # retournait VIVANT → le PID avait été RECYCLÉ par Windows vers un autre
            # process → le watchdog croyait le robot vivant → pas de résurrection.
            # Un PID recyclé a un temps de création DIFFÉRENT de celui du robot
            # d'origine: on le détecte via GetProcessTimes.
            from ctypes import c_ulonglong, byref

            creation = c_ulonglong(0)
            exit_t = c_ulonglong(0)
            kernel_t = c_ulonglong(0)
            user_t = c_ulonglong(0)
            if kernel32.GetProcessTimes(
                handle,
                byref(creation),
                byref(exit_t),
                byref(kernel_t),
                byref(user_t),
            ):
                # Comparer avec le temps de création attendu (passé via une var globale)
                if _expected_creation_time and creation.value != _expected_creation_time:
                    # PID recyclé: le process derrière ce PID n'est PAS notre robot
                    return False
            return True
        except Exception as e:
            log(f"OpenProcess check failed: {e}", "WARN")
            return True  # Assume alive on error (conservative)
        finally:
            if handle and kernel32 is not None:
                kernel32.CloseHandle(handle)
    else:
        # Unix: use os.kill with signal 0
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def kill_process(pid: int) -> bool:
    """Force-kill a process on Windows using taskkill /F."""
    if os.name == "nt":
        try:
            # 🐛 FIX 05 Août 2026: errors='replace' (même raison que get_process_status)
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
            success = result.returncode == 0
            if success:
                log(f"Process {pid} killed successfully")
            else:
                log(f"Failed to kill process {pid}: {result.stderr.strip()}", "WARN")
            return success
        except subprocess.TimeoutExpired:
            log(f"Timeout killing process {pid}", "ERROR")
            return False
        except Exception as e:
            log(f"Error killing process {pid}: {e}", "ERROR")
            return False
    else:
        # Unix: SIGKILL
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError as e:
            log(f"Error killing process {pid}: {e}", "ERROR")
            return False


def spawn_new_instance() -> bool:
    """Spawn a new main.py process to replace the dead/killed one."""
    main_py = Path(__file__).parent.parent / "main.py"
    if not main_py.exists():
        log(f"main.py not found at {main_py}", "CRITICAL")
        return False
    try:
        subprocess.Popen([sys.executable, str(main_py)], cwd=str(main_py.parent))
        log("Spawned new main.py instance")
        return True
    except Exception as e:
        log(f"Failed to spawn new instance: {e}", "ERROR")
        return False


def flag_path(heartbeat_path: Path, name: str) -> Path:
    """Resolve a sibling flag file next to the heartbeat file (same runtime/ dir)."""
    return heartbeat_path.parent / name


def is_graceful_stop_requested(heartbeat_path: Path) -> bool:
    """True if robot.ps1 -Stop wrote a shutdown flag (intentional stop)."""
    flag = flag_path(heartbeat_path, "robot.stop.flag")
    return flag.exists()


def mark_halt(heartbeat_path: Path) -> None:
    """Write a HALT flag so the next manual start is required (operator attention)."""
    halt = flag_path(heartbeat_path, "robot.halt.flag")
    try:
        halt.write_text(f"halted {datetime.utcnow().isoformat()}\n")
        log(f"HALT flag written: {halt}", "CRITICAL")
    except Exception as e:
        log(f"Failed to write HALT flag: {e}", "ERROR")


def restart_budget_exhausted(heartbeat_path: Path) -> bool:
    """Track restart timestamps; return True if too many restarts in the window.

    A simple state file stores the timestamps of recent restarts. If more than
    MAX_RESTARTS have occurred within RESTART_WINDOW seconds, we stop and ask
    for operator attention (prevents an MT5-down kill/restart storm).
    """
    state = flag_path(heartbeat_path, "watchdog_restarts.txt")
    now = time.time()
    recent = []
    try:
        if state.exists():
            for line in state.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = float(line)
                    if now - ts <= RESTART_WINDOW_S:
                        recent.append(ts)
                except ValueError:
                    pass
    except Exception as e:
        log(f"Error reading restart state: {e}", "WARN")

    recent.append(now)
    try:
        state.write_text("\n".join(f"{t:.2f}" for t in recent[-50:]) + "\n")
    except Exception as e:
        log(f"Error writing restart state: {e}", "WARN")

    if len(recent) > MAX_RESTARTS:
        log(f"CRITICAL: {len(recent)} restarts in {RESTART_WINDOW_S}s (max {MAX_RESTARTS}) — HALTING auto-restart")
        mark_halt(heartbeat_path)
        return True
    return False


def attempt_restart(heartbeat_path: Path) -> None:
    """Resurrect the robot with a cooldown + budget guard. Does NOT return on exit."""
    # Respect intentional shutdown
    if is_graceful_stop_requested(heartbeat_path):
        log("Graceful shutdown flag present — NOT restarting. Exiting watchdog.")
        sys.exit(0)

    log("Attempting to resurrect robot...")

    # Cooldown: waits out RESTART_COOLDOWN_S since the previous attempt
    # (looping quietly) so we don't hammer the CPU if MT5 is briefly down.
    tried_at = time.time()
    while True:
        if flag_path(heartbeat_path, "robot.halt.flag").exists() or is_graceful_stop_requested(heartbeat_path):
            log("HALT or shutdown flag detected — exiting without restart.")
            sys.exit(0)

        # If the robot is somehow already alive again (e.g. a race), let it live.
        # (We don't know the new PID here, so rely on the heartbeat getting fresh
        #  after the cooldown window.)
        if time.time() - tried_at >= RESTART_COOLDOWN_S:
            if restart_budget_exhausted(heartbeat_path):
                sys.exit(1)
            if spawn_new_instance():
                # Spawn success. The new main.py spawns its OWN watchdog on
                # startup (trading_engine._start_process_watchdog). This old
                # watchdog hands off and exits — exactly one watchdog per gen.
                log("Handing off to new robot generation's watchdog.")
                sys.exit(0)
            else:
                # Spawn failed (main.py missing, etc.): hash a HALT and exit.
                mark_halt(heartbeat_path)
                sys.exit(1)
        time.sleep(5)


def main():
    if len(sys.argv) < 3:
        print("Usage: python process_watchdog.py <PID> <heartbeat_file> [timeout_seconds]", file=sys.stderr)
        sys.exit(1)

    try:
        target_pid = int(sys.argv[1])
    except ValueError:
        print(f"Invalid PID: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    heartbeat_path = Path(sys.argv[2])
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 300  # Default: 5 min

    my_pid = os.getpid()
    _init_file_log()
    # 🔧 FIX 17 Août 2026: capturer le temps de création du process cible au
    # démarrage → permet à get_process_status de détecter le PID reuse (un PID
    # recyclé a une création différente → considéré mort → résurrection).
    global _expected_creation_time
    _expected_creation_time = _get_process_creation_time(target_pid)
    log(f"Started monitoring PID={target_pid} (created={_expected_creation_time or 'unknown'}) heartbeat={heartbeat_path} timeout={timeout}s")
    log("Auto-resurrection ENABLED (restart on process death; cooldown + budget + stop-flag guards active)")

    # If a halt flag survived from a previous crash-run, do not re-resurrect
    # blindly; the operator must remove it or it would loop forever after a
    # real failure. (Still monitor, but refuse AUTO-restart: log loudly.)
    auto_restart_disabled = (
        is_graceful_stop_requested(heartbeat_path) or flag_path(heartbeat_path, "robot.halt.flag").exists()
    )

    check_interval = 30  # seconds between checks
    consecutive_stalls = 0
    max_stalls = 3  # Kill after 3 consecutive stale reads (~90s + timeout = ~6.5min typical)

    # 🔧 FIX 11 Août 2026: deadline MONOTONIC au lieu de time.sleep pur.
    # Après une reprise de veille S3, le sleep(30) peut être PERDU (waitable-timer
    # Windows qui ne se réveille jamais) → le watchdog reste suspendu indéfiniment
    # alors que le robot tourne → gel non détecté (observé 11/08 : CPU figé à 0.5s).
    # On découpe l'attente en slices de 5s avec un deadline monotone : même si un
    # slice est perdu au réveil, la prochaine itération rattrape immédiatement.
    # NB: une veille machine complète suspend quand même le processus (aucun code
    # ne tourne) — ce fix ne protège que contre la perte du timer APRÈS la reprise.
    _next_check = time.monotonic() + check_interval
    _loop_count = 0  # 🔧 FIX 17 Août 2026: compteur pour log "alive" périodique
    _hb_mtime_prev = 0

    try:
        while True:
            # Attente découpée en slices courtes jusqu'au deadline
            while True:
                _remaining = _next_check - time.monotonic()
                if _remaining <= 0:
                    break
                time.sleep(min(5.0, _remaining))

            if auto_restart_disabled:
                # Allowed to keep monitoring but never resurrect on our own.
                log("Auto-restart disabled (shutdown/halt flag) — monitoring only, no restart", "WARN")
                if not get_process_status(target_pid):
                    log("Target process no longer alive and auto-restart disabled — exiting", "WARN")
                    sys.exit(0)

            # First check: is target process still alive?
            if not get_process_status(target_pid):
                log(f"CRITICAL: Target process {target_pid} is DEAD (crashed) — resurrecting")
                attempt_restart(heartbeat_path)
                # attempt_restart exits or spawns; loop continues only if --unreachable path
                continue

            # 🔧 FIX 17 Août 2026: log périodique "alive" — preuve que la boucle
            # tourne. Si le watchdog se gèle (10h sans log observé), ce heartbeat
            # interne révélera le gel dans les 2.5 min au lieu de découvrir la
            # panne après coup. Loggue toutes les 5 itérations (~2.5 min).
            _loop_count += 1
            if _loop_count % 5 == 0:
                _hb_age = time.time() - heartbeat_path.stat().st_mtime if heartbeat_path.exists() else -1
                log(f"ALIVE: monitoring PID={target_pid} (hb_age={_hb_age:.0f}s, loop={_loop_count})")
            _hb_mtime_prev = time.time()

            # Second check: is heartbeat recent? (only if process is alive)
            try:
                if heartbeat_path.exists():
                    # 🔧 FIX 11 Août 2026: détection de staleness via le MTIME du
                    # fichier (fiable au réveil de veille) au lieu du parsing ISO.
                    # Le contenu est écrit en UTC par le robot (écriture atomique
                    # tmp+rename depuis le 05/08) ; l'mtime est POSIX, comparable
                    # directement à time.time() sans parsing ni fuseau. Après une
                    # veille S3, on détecte ainsi correctement un heartbeat vieux
                    # de 4h alors que le parsing ISO + datetime.utcnow() aurait pu
                    # être ambigu au moment du réveil.
                    hb_mtime = heartbeat_path.stat().st_mtime
                    elapsed = time.time() - hb_mtime

                    # 🐛 FIX 05 Août 2026: le robot écrit le heartbeat de façon
                    # ATOMIQUE (tmp+rename) depuis le 05/08, mais un fichier vide
                    # peut subsister si une ancienne version du robot tourne encore.
                    # (garde: on relit le contenu UNIQUEMENT pour valider qu'il est
                    # non vide — la staleness, elle, est calculée sur l'mtime.)
                    hb_content = ""
                    for _retry in range(3):
                        try:
                            hb_content = heartbeat_path.read_text(errors="replace").strip()
                            if hb_content:
                                break
                        except Exception:
                            pass
                        time.sleep(0.2)
                    if hb_content:
                        if elapsed > timeout:
                            consecutive_stalls += 1
                            log(f"Heartbeat stale: {elapsed:.0f}s old (stall #{consecutive_stalls}/{max_stalls})")

                            if consecutive_stalls >= max_stalls:
                                log(
                                    f"CRITICAL: {consecutive_stalls} consecutive stalls — killing main process (GIL freeze)"
                                )
                                log("Resurrecting after GIL-freeze kill (hand-off to new watchdog generation).")
                                kill_process(target_pid)
                                time.sleep(2)
                                # Hand off: attempt_restart applies cooldown + budget + stop-flag guards,
                                # spawns a fresh main.py (which spawns its OWN watchdog) and exits.
                                attempt_restart(heartbeat_path)
                                # attempt_restart does not return (exits process) unless it loops the
                                # cooldown. If it returns, continue the next loop cleanly.
                                consecutive_stalls = 0
                        else:
                            # Heartbeat is fresh — reset stall counter
                            if consecutive_stalls > 0:
                                log(f"Heartbeat recovered after {elapsed:.0f}s — reset stalls")
                                consecutive_stalls = 0
                    else:
                        log(f"Heartbeat file empty", "WARN")
                else:
                    log(f"Heartbeat file not found", "WARN")
            except Exception as e:
                log(f"Error reading heartbeat: {e}", "WARN")

    except KeyboardInterrupt:
        log("Received interrupt — exiting")
        sys.exit(0)


if __name__ == "__main__":
    main()
