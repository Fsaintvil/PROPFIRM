"""Lightweight live monitor: tails order audit and live audit logs
and prints simple alerts when error retcodes or unexpected events occur.

Run: python tools/live_monitor.py
"""
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
ORDER_AUDIT = LOGS / "order_audit.csv"
LIVE_AUDIT = LOGS / "live_enable_audit.csv"


def tail(path: Path):
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        # seek end
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.rstrip("\n")


def monitor():
    print("Starting live monitor (press Ctrl-C to stop)")
    tails = []
    if ORDER_AUDIT.exists():
        tails.append(("order", tail(ORDER_AUDIT)))
    if LIVE_AUDIT.exists():
        tails.append(("live", tail(LIVE_AUDIT)))

    try:
        while True:
            for name, gen in list(tails):
                try:
                    line = next(gen)
                except StopIteration:
                    continue
                except Exception:
                    continue
                if name == "order":
                    # naive parse: look for retcode fields or 'retcode' text
                    if "retcode" in line or ",10027" in line:
                        print("[ALERT][order]", line)
                    else:
                        print("[order]", line)
                else:
                    print("[live]", line)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nMonitor stopped")


if __name__ == "__main__":
    monitor()
