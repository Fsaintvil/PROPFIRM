#!/usr/bin/env python3
"""Process-level watchdog for MT5 FTMO Robot.

PROBLEM: The thread-based watchdog (external watchdog thread in main.py) cannot
run when MT5 blocks the Python GIL (Global Interpreter Lock) during C-extension
API calls (copy_rates_from_pos, positions_get, order_send, etc.). This causes
the robot to freeze for hours (observed: 4h30, 12h25 blocks).

SOLUTION: A completely separate PROCESS that:
1. Is spawned at startup by main.py with the main PID
2. Monitors the heartbeat file every 30 seconds
3. If heartbeat is stale (> 300s / 5 min), kills the main process via taskkill /F
4. Windows auto-releases the named mutex when the process dies
5. Spawns a new main.py instance to replace the killed one

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
from pathlib import Path


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


def spawn_new_instance():
    """Spawn a new main.py process to replace the killed one."""
    main_py = Path(__file__).parent.parent / "main.py"
    if not main_py.exists():
        log(f"main.py not found at {main_py}", "CRITICAL")
        return False
    try:
        subprocess.Popen([sys.executable, str(main_py)], cwd=str(main_py.parent))
        log(f"Spawned new main.py instance")
        return True
    except Exception as e:
        log(f"Failed to spawn new instance: {e}", "ERROR")
        return False


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

    check_interval = 30  # seconds between checks
    consecutive_stalls = 0
    max_stalls = 3  # Kill after 3 consecutive stale reads (~90s + 300s = ~6.5min)

    try:
        while True:
            time.sleep(check_interval)

            # First check: is target process still alive?
            if not get_process_status(target_pid):
                log(f"Target process {target_pid} is no longer alive — exiting")
                sys.exit(0)

            # Second check: is heartbeat recent?
            try:
                if heartbeat_path.exists():
                    hb_content = heartbeat_path.read_text().strip()
                    if hb_content:
                        # Parse ISO timestamp
                        from datetime import datetime

                        hb_time = datetime.fromisoformat(hb_content)
                        elapsed = (datetime.utcnow() - hb_time).total_seconds()

                        if elapsed > timeout:
                            consecutive_stalls += 1
                            log(f"Heartbeat stale: {elapsed:.0f}s old (stall #{consecutive_stalls}/{max_stalls})")

                            if consecutive_stalls >= max_stalls:
                                log(f"CRITICAL: {consecutive_stalls} consecutive stalls — killing main process")

                                # Kill the stuck main process
                                kill_process(target_pid)

                                # Give it a moment to die
                                time.sleep(2)

                                # Spawn replacement
                                spawn_new_instance()

                                # Exit — our job is done
                                sys.exit(0)
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
