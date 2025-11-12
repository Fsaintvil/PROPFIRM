import time
import subprocess
import glob
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ARTIFACTS = os.path.join(ROOT, 'artifacts', 'live_trading')
PARSER = os.path.join(ROOT, 'tools', 'parse_log_orders.py')
ENRICH = os.path.join(ROOT, 'tools', 'enrich_orders_with_mt5.py')
LOCK = os.path.join(ROOT, 'control', 'ai_sending.lock')

SLEEP_SECONDS = 930


def run_once():
    # if lock file present, skip
    if os.path.exists(LOCK):
        print(f"{datetime.utcnow().isoformat()} - lock present, skipping cycle")
        return

    # run parser
    try:
        print(f"{datetime.utcnow().isoformat()} - running parser: {PARSER}")
        subprocess.run(['python', PARSER], check=False)
    except Exception as e:
        print("parser error:", e)

    # find latest orders_audit_*.json
    pattern = os.path.join(ARTIFACTS, 'orders_audit_*.json')
    files = glob.glob(pattern)
    if not files:
        print(f"{datetime.utcnow().isoformat()} - no audit files found, skipping enrichment")
        return
    latest = max(files, key=os.path.getmtime)
    print(f"{datetime.utcnow().isoformat()} - latest audit: {latest}")

    # call enrich with that file
    try:
        print(f"{datetime.utcnow().isoformat()} - running enrich on {latest}")
        subprocess.run(['python', ENRICH, latest], check=False)
    except Exception as e:
        print("enrich error:", e)


if __name__ == '__main__':
    print(f"Monitor started at {datetime.utcnow().isoformat()}, cadence {SLEEP_SECONDS}s")
    while True:
        run_once()
        print(f"{datetime.utcnow().isoformat()} - sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)
