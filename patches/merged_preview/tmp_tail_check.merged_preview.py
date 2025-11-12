# Merged preview for prefix: tmp
# Generated from 4 files

################################################################################
# FROM: tools\tmp_mt5_diag.py
################################################################################
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


################################################################################
# FROM: tools\archive\tmp_scripts\tmp_check_mt5.py
################################################################################
import os

from src.utils import mt5_connector

print("trading_disabled=", mt5_connector.trading_disabled())
print("mt5_available=", mt5_connector.is_mt5_available())
print("env_MT5_LOGIN=", bool(os.getenv("MT5_LOGIN")))
print("env_MT5_SERVER=", bool(os.getenv("MT5_SERVER")))


################################################################################
# FROM: tools\archive\tmp_scripts\tmp_diag.py
################################################################################
import os
from pathlib import Path

root = Path("MT5_FTMO_IA")
ctrl = root / "control"
ks = ctrl / "kill_switch"
auto = ctrl / "auto_approve"
sec = ctrl / "auto_approve_secondary"
print("KILL_SWITCH exists:", ks.exists(), str(ks))
print("AUTO_APPROVE exists:", auto.exists(), str(auto))
print("AUTO_APPROVE_SECOND exists:", sec.exists(), str(sec))
print("ALLOW_LIVE_SEND env:", os.getenv("ALLOW_LIVE_SEND"))

# Run package-level diagnostics
try:
    from MT5_FTMO_IA.scripts import mt5_connector, safety

    print("safety.can_send_live():", safety.can_send_live())
    info = mt5_connector.check_mt5_compatibility()
    print(
        "mt5 check: ok=",
        info.get("ok"),
        "mt5_imported=",
        info.get("mt5_imported"),
    )
    print("mt5 path:", info.get("mt5_path"))
    print("numpy:", info.get("numpy"))
except Exception as e:
    import traceback

    traceback.print_exc()
    print("DIAG ERROR:", e)


################################################################################
# FROM: tools\archive\tmp_scripts\tmp_tail_check.py
################################################################################
from pathlib import Path

# Path to file
p = Path("scripts/restart_optimized.py")
b = p.read_bytes()
print("FILE LEN", len(b))
TAIL = b[-64:]
print("TAIL HEX", TAIL.hex())
print("TAIL BYTES", list(TAIL))
# count trailing newline like bytes
i = len(b) - 1
count = 0
while i >= 0 and b[i] in (10, 13):
    count += 1
    i -= 1
print("TRAILING_NEWLINE_BYTES", count)
print("LAST_NON_NL_INDEX", i)
print("LAST_NON_NL_BYTE", b[i] if i >= 0 else None)


# End of merged preview
