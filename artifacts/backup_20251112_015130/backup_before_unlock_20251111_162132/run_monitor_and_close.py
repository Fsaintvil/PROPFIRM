#!/usr/bin/env python3
# Runner pour lancer la surveillance active et la fermeture progressive
import time
import threading
import json
from datetime import datetime
import sys
from pathlib import Path
# Ensure repository root is on sys.path so we can import scripts.* modules
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from scripts.live_trading_engine import LiveTradingEngine

if __name__ == '__main__':
    symbols = ["EURUSD", "XAUUSD", "BTCUSD"]
    engine = LiveTradingEngine(symbols=symbols)

    print("[runner] Connecting to MT5...")
    ok = engine.connect_mt5()
    if not ok:
        print("[runner] MT5 connection failed - aborting")
        raise SystemExit(1)

    # Start monitor thread
    def monitor_target():
        print("[runner] Monitor thread started at", datetime.utcnow().isoformat() + 'Z')
        summary = engine.monitor_and_apply_retries(interval_s=10, cycles=20)
        print("[runner] Monitor finished - summary written; actions:", len(summary.get('actions', [])))

    monitor_thread = threading.Thread(target=monitor_target, daemon=False)
    monitor_thread.start()

    # Start close-positive thread
    def close_target():
        print("[runner] Close-positive thread started at", datetime.utcnow().isoformat() + 'Z')
        res = engine.close_positive_positions_gradual(duration_minutes=30, min_profit=0.0)
        print("[runner] Close-positive finished:", res)

    close_thread = threading.Thread(target=close_target, daemon=False)
    close_thread.start()

    # Wait for both to finish
    try:
        while monitor_thread.is_alive() or close_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print('[runner] KeyboardInterrupt received, exiting')
    print('[runner] All tasks finished')
