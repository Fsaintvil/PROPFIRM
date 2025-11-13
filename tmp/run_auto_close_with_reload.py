import sys, os
sys.path.insert(0, os.getcwd())
from scripts.live_trading_engine import LiveTradingEngine
from pathlib import Path
import json

print('--- RUN AUTO-CLOSE WITH WATCHLIST RELOAD (explicit user authorization) ---')
engine = LiveTradingEngine()
print('Attempting MT5 connect...')
ok = engine.connect_mt5()
print('MT5 connected:', ok)
if not ok:
    print('MT5 not connected - aborting enforce_auto_close')
else:
    # reload watchlist from disk like start_live_trading does
    watch_fn = Path('artifacts') / 'live_trading' / 'mt5_watchlist.jsonl'
    added = 0
    if watch_fn.exists():
        with watch_fn.open('r', encoding='utf-8') as wf:
            for line in wf:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get('closed'):
                    continue
                # dedupe by order_id or ticket
                duplicate = False
                for p in getattr(engine, 'position_watchlist', []):
                    try:
                        if (p.get('order_id') is not None and p.get('order_id') == obj.get('order_id')):
                            duplicate = True
                            break
                        if (p.get('ticket') is not None and obj.get('ticket') is not None and p.get('ticket') == obj.get('ticket')):
                            duplicate = True
                            break
                    except Exception:
                        continue
                if duplicate:
                    continue
                engine.position_watchlist.append(obj)
                added += 1
    print(f'Reloaded {added} watchlist entries')

    try:
        engine.enforce_auto_close()
        print('enforce_auto_close() executed')
    except Exception as e:
        import traceback
        traceback.print_exc()
        print('enforce_auto_close() failed:', e)

print('--- END RUN ---')
