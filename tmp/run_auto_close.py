from scripts.live_trading_engine import LiveTradingEngine
import time

print('--- RUN AUTO-CLOSE (explicit user authorization) ---')
engine = LiveTradingEngine()
print('Attempting MT5 connect...')
ok = engine.connect_mt5()
print('MT5 connected:', ok)
if not ok:
    print('MT5 not connected - aborting enforce_auto_close')
else:
    try:
        # call enforce_auto_close which will perform real closes if MT5 available
        engine.enforce_auto_close()
        print('enforce_auto_close() executed')
    except Exception as e:
        import traceback
        traceback.print_exc()
        print('enforce_auto_close() failed:', e)

print('--- END RUN ---')
