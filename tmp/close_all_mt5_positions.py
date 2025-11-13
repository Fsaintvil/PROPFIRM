import sys
from pathlib import Path
# Ensure repo root is on sys.path
repo_root = str(Path(__file__).resolve().parents[1])
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from scripts.live_trading_engine import LiveTradingEngine


if __name__ == '__main__':
    engine = LiveTradingEngine()
    ok = engine.connect_mt5()
    print('MT5 connected:', ok)
    if not ok:
        print('MT5 not available or connection failed; aborting close_all_positions')
    else:
        # Ensure we run with safety: confirm via environment variable
        import os
        if os.getenv('CONFIRM_CLOSE_ALL', '0') != '1':
            print('CONFIRM_CLOSE_ALL not set to 1 — aborting. Set environment variable CONFIRM_CLOSE_ALL=1 to proceed.')
        else:
            print('Proceeding to close all positions now...')
            res = engine.close_all_positions()
            print('close_all_positions returned:', res)
