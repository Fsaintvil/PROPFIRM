import json
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOG_FILE = BASE / "logs" / "monitor.log"
HEARTBEAT_FILE = BASE / "runtime" / "heartbeat.txt"
STATE_FILE = BASE / "runtime" / "robot_state.json"
REPORT_FILE = BASE / "runtime" / "ftmo_report.json"
MAIN_SCRIPT = BASE / "main.py"

# Load .env for Telegram
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except Exception:
    pass

CHECK_INTERVAL = 60
HEARTBEAT_TIMEOUT = 150
RESTART_DELAY = 5

handler = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=3, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger("monitor")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_alert(msg):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]},
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"Telegram alert failed: {e}")


def robot_process():
    pid_entry = _find_robot_pid()
    if pid_entry:
        return pid_entry
    return None


def _find_robot_pid():
    """Return dict {pid, name} if robot process running, else None."""
    # 1) Try PID lock file
    pid_file = BASE / "runtime" / "robot.pid"
    try:
        pid = int(pid_file.read_text().strip())
        if _is_python_process(pid):
            return {"pid": pid, "name": "pythonw.exe"}
    except Exception:
        pass

    # 2) Scan with psutil
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                cmd = " ".join(p.info.get("cmdline") or [])
                if "main.py" in cmd and "python" in name:
                    return {"pid": p.info["pid"], "name": p.info.get("name", "python.exe")}
            except Exception:
                pass
    except ImportError:
        pass

    # 3) Fallback: PowerShell Get-CimInstance
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if '"ProcessId"' in line or not line.strip():
                continue
            if "main.py" in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    pid_str = parts[1].strip('" ')
                    if pid_str.isdigit() and _is_python_process(int(pid_str)):
                        return {"pid": int(pid_str), "name": "python.exe"}
    except Exception:
        pass

    return None


def _is_python_process(pid):
    """Check if a given PID is a running python process (no psutil needed)."""
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and "python" in (p.name() or "").lower()
    except ImportError:
        pass
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x1000, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def heartbeat_age():
    try:
        age = time.time() - HEARTBEAT_FILE.stat().st_mtime
        return age
    except Exception:
        return float("inf")


def last_log_errors(lines=100):
    log_path = BASE / "logs" / "simple_robot.log"
    if not log_path.exists():
        return [], []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        entries = text.strip().split("\n")[-lines:]
        errors = [line for line in entries if "ERROR" in line or "CRITICAL" in line]
        warnings = [line for line in entries if "WARNING" in line]
        return errors[-5:], warnings[-5:]
    except Exception:
        return [], []


def read_report():
    try:
        return json.loads(REPORT_FILE.read_text())
    except Exception:
        return {}


def start_robot():
    logger.info("Starting robot...")
    try:
        pythonw = "pythonw.exe" if sys.platform == "win32" else sys.executable
        subprocess.Popen(
            [pythonw, str(MAIN_SCRIPT)],
            cwd=str(BASE),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        logger.error(f"Start failed: {e}")
        send_alert(f"MONITOR: echec demarrage robot: {e}")
        return False
    return True


def stop_robot():
    entry = robot_process()
    if not entry:
        return
    pid = entry["pid"]
    try:
        # Try graceful terminate via psutil first
        import psutil
        p = psutil.Process(pid)
        p.terminate()
        time.sleep(3)
        if p.is_running():
            p.kill()
    except ImportError:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
    except Exception:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
    logger.info("Robot stopped")


def status_summary(report):
    rep = report or {}
    pnl = rep.get("pnl", 0)
    def _parse_pct(v):
        s = str(v).replace("%","").strip()
        try:
            return float(s)
        except Exception:
            return 0.0
    dd_pct = max(_parse_pct(rep.get("dd_from_initial", "0%")), _parse_pct(rep.get("dd_from_peak", "0%")))
    dd = f"{dd_pct:.1f}%"
    trades = rep.get("total_trades", 0)
    wr = rep.get("win_rate", "0%")
    progress = rep.get("profit_progress", "0%")
    balance = rep.get("balance", 0)
    equity = rep.get("equity", 0)
    return {
        "pnl": f"${pnl:.0f}" if isinstance(pnl, (int, float)) else str(pnl),
        "dd": dd,
        "trades": trades,
        "win_rate": wr,
        "progress": progress,
        "balance": balance,
        "equity": equity,
    }


def main():
    logger.info("=" * 50)
    logger.info("MONITOR DEMARRE - surveillance robot FTMO")
    logger.info("=" * 50)
    send_alert("MONITOR: surveillance automatique lancee")

    last_summary_time = 0
    last_report_time = 0
    restart_count = 0

    first_run = True

    while True:
        try:
            if first_run:
                logger.info("Waiting 60s for robot initialization...")
                time.sleep(60)
                first_run = False

            proc = robot_process()
            hb = heartbeat_age()
            now = time.time()

            if proc is None or hb > HEARTBEAT_TIMEOUT:
                reason = "process not found" if proc is None else f"heartbeat stale ({hb:.0f}s)"
                logger.warning(f"Robot down: {reason}")
                send_alert(f"MONITOR: Robot arrete ({reason}). Redemarrage...")
                stop_robot()
                time.sleep(RESTART_DELAY)
                start_robot()
                restart_count += 1
                time.sleep(10)
                if restart_count > 5:
                    send_alert(f"MONITOR: {restart_count} redemarrages en 24h - verifier le robot")
                    restart_count = 0

            errors, warnings = last_log_errors()
            if errors:
                for e in errors:
                    logger.warning(f"Log ERROR: {e[:200]}")
                send_alert(f"MONITOR: {len(errors)} erreurs dans les logs\n{errors[-1][:300]}")

            if now - last_report_time > 3600:
                report = read_report()
                summary = status_summary(report)
                logger.info(f"Status: PnL={summary['pnl']} DD={summary['dd']} "
                    f"Trades={summary['trades']} WR={summary['win_rate']} "
                    f"Progress={summary['progress']} Balance=${summary['balance']}")
                last_report_time = now

            if now - last_summary_time > 21600:
                report = read_report()
                summary = status_summary(report)
                msg = (
                    f"ROBOT FTMO - Rapport 6h\n"
                    f"Balance: ${summary['balance']:.0f}\n"
                    f"Equity: ${summary['equity']:.0f}\n"
                    f"PnL: {summary['pnl']}\n"
                    f"Drawdown: {summary['dd']}\n"
                    f"Trades: {summary['trades']} | WR: {summary['win_rate']}\n"
                    f"Progres: {summary['progress']}\n"
                    f"Redemarrages: {restart_count}"
                )
                send_alert(msg)
                last_summary_time = now

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
            break
        except Exception as e:
            logger.error(f"Monitor error: {e}", exc_info=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
