"""Debug: check get_rates functionality"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import config_simple as cfg
from engine_simple.mt5_connector import MT5Connector
import MetaTrader5 as mt5

print(f"mt5.TIMEFRAME_M1 = {mt5.TIMEFRAME_M1} (type={type(mt5.TIMEFRAME_M1).__name__})")

conn = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
ok = conn.connect()
print(f"Connected: {ok}")

r1 = conn.get_rates("USDCAD", mt5.TIMEFRAME_M1, 100)
print(f"get_rates int: {len(r1) if r1 is not None else None}")

r2 = conn.get_rates("USDCAD", "M1", 100)
print(f"get_rates str M1: {len(r2) if r2 is not None else None}")

r3 = conn.get_rates("USDCAD", "H1", 100)
print(f"get_rates str H1: {len(r3) if r3 is not None else None}")

r4 = mt5.copy_rates_from_pos("USDCAD", mt5.TIMEFRAME_M1, 0, 100)
print(f"Direct MT5: {len(r4) if r4 is not None else None}")

if r1 is not None and len(r1) > 0:
    print(f"Sample: close[0]={r1[0]['close']:.5f} close[-1]={r1[-1]['close']:.5f}")

conn.disconnect()
