"""Purge calibration_state.json: remove HIST/DOW artefacts, burst entries, and contaminated adapted_params."""
import json
from datetime import datetime

STATE_PATH = "runtime/calibration_state.json"

with open(STATE_PATH, "r") as f:
    data = json.load(f)

oh = data.get("online_history", {})

# 1. XAUUSD: remove HIST and DOW regime entries
if "XAUUSD" in oh:
    before = len(oh["XAUUSD"])
    oh["XAUUSD"] = [e for e in oh["XAUUSD"] if e.get("regime") not in ("HIST", "DOW")]
    print(f"XAUUSD: {before} -> {len(oh['XAUUSD'])} entries (removed HIST/DOW)")

# 2. BTCUSD: remove HIST regime entries
if "BTCUSD" in oh:
    before = len(oh["BTCUSD"])
    oh["BTCUSD"] = [e for e in oh["BTCUSD"] if e.get("regime") != "HIST"]
    print(f"BTCUSD: {before} -> {len(oh['BTCUSD'])} entries (removed HIST)")

# 3. EURUSD: remove burst entries (timestamps within 5s)
if "EURUSD" in oh:
    before = len(oh["EURUSD"])
    cleaned = []
    last_time = None
    for e in oh["EURUSD"]:
        t = e.get("time", "")
        if t and last_time:
            try:
                t1 = datetime.fromisoformat(t.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
                if (t1 - t2).total_seconds() < 5:
                    continue  # skip burst
            except Exception:
                pass
        cleaned.append(e)
        last_time = t
    oh["EURUSD"] = cleaned
    print(f"EURUSD: {before} -> {len(cleaned)} entries (removed burst <5s)")

# 4. Remove TESTX (test symbol)
if "TESTX" in oh:
    del oh["TESTX"]
    print("TESTX: removed (test symbol)")

# 5. Clear ALL adapted_params (contaminated)
data["adapted_params"] = {}
print("adapted_params: cleared (all contaminated)")

with open(STATE_PATH, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\nSaved cleaned calibration_state.json")
