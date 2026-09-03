import os, sys, yaml
os.chdir(r"C:\Users\saint\Documents\MT5_FTMO_IA.7"); sys.path.insert(0, ".")
import importlib
import engine_simple.strategy as s
importlib.reload(s)

# Direct check of SYMBOL_CONFIG
active = ["GBPUSD","USDCAD","AUDUSD","USDCHF"]
for sym in active:
    cfg = s.SYMBOL_CONFIG.get(sym, {})
    hrs = cfg.get("preferred_hours", "MISSING")
    print(f"  {sym}: preferred_hours = {hrs} (type={type(hrs).__name__}, len={len(hrs) if isinstance(hrs, list) else 'N/A'})")
