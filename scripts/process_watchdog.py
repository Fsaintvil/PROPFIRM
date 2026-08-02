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


def log(msg: str, level: str = "INFO") -> None:
    """Simple logging to stderr (never interferes with main process output)."""
    print(f"[WATCHDOG_PROC] {level} {msg}", file=sys.stderr, flush=True)


def get_process_status(pid: int) -> bool:
    """Check if a process is alive on Windows using tasklist."""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"], capture_output=True, text=True, timeout=10
            )
            return str(pid) in result.stdout
        except (subprocess.TimeoutExpired, Exception) as e:
            log(f"tasklist check failed: {e}", "WARN")
            return True  # Assume alive on error (conservative)
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
            result = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True, timeout=10)
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
    log(f"Started monitoring PID={target_pid} heartbeat={heartbeat_path} timeout={timeout}s")
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

    try:
        while True:
            time.sleep(check_interval)

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

            # Second check: is heartbeat recent? (only if process is alive)
            try:
                if heartbeat_path.exists():
                    hb_content = heartbeat_path.read_text().strip()
                    if hb_content:
                        # Parse ISO timestamp
                        hb_time = datetime.fromisoformat(hb_content)
                        elapsed = (datetime.utcnow() - hb_time).total_seconds()

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
