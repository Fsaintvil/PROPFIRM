"""Diagnostic MT5: initialise, sélectionne XAUUSD, récupère jusqu'à 5000 bougies M15 et écrit un CSV de test.
Usage: python tools/tmp_mt5_diag.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

import pandas as pd

out_dir = Path("data")
out_dir.mkdir(parents=True, exist_ok=True)

symbol = "XAUUSD"
count = 5000

try:
    ok = mt5.initialize()
    print("mt5.initialize ->", ok)
except Exception as e:
    print("mt5.initialize error:", e)
    ok = False

if not ok:
    print("MT5 initialize failed, aborting")
    sys.exit(2)

try:
    sel = mt5.symbol_select(symbol, True)
    print("symbol_select ->", sel)
except Exception as e:
    print("symbol_select error:", e)

arr = None
try:
    arr = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, count)
    print("copy_rates_from_pos returned", 0 if arr is None else len(arr))
except Exception as e:
    print("copy_rates_from_pos exception:", e)

if arr is None or len(arr) == 0:
    print("No bars returned for", symbol)
else:
    df = pd.DataFrame(arr)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.rename(columns={"time": "datetime"})
    outp = out_dir / f"{symbol}_15min_test.csv"
    df.to_csv(outp, index=False)
    print("Wrote", len(df), "rows to", outp)

try:
    mt5.shutdown()
except Exception:
    pass

print("diagnostic complete")
