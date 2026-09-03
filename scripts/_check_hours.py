import os, sys, yaml
os.chdir(r"C:\Users\saint\Documents\MT5_FTMO_IA.7"); sys.path.insert(0, ".")
import importlib
import engine_simple.strategy as s
importlib.reload(s)
with open("config/default.yaml") as f:
    raw = yaml.safe_load(f)
yl = raw.get("symbol_limits", {})
active = ["BTCUSD","SOLUSD","GBPUSD","US30.cash","JP225.cash","XAUUSD","NZDUSD","EURUSD","USDJPY","USDCAD","AUDUSD","USDCHF"]
for sym in active:
    s_hrs = s.SYMBOL_CONFIG.get(sym, {}).get("preferred_hours", [])
    y_hrs = yl.get(sym, {}).get("preferred_hours", [])
    s_ok = "24/7" if len(s_hrs) >= 20 else str(len(s_hrs)) + "h"
    y_ok = "24/7" if len(y_hrs) >= 20 else str(len(y_hrs)) + "h"
    match = "OK" if s_ok == y_ok else "DIFF"
    print(f"{sym:14s} strat={s_ok:>4s}  yaml={y_ok:>4s}  {match}")
