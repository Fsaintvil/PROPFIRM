"""Analyse la contamination de trades_log.csv"""
import csv
from collections import Counter

with open('runtime/trades_log.csv', 'r') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Total rows (excl header): {len(rows)}")

# Check for backtest contamination
backtest = [r for r in rows if r.get("direction","") == "" or r.get("volume","0") == "0" or r.get("entry_price","") in ("1.1","1.25","150.0")]
print(f"Backtest lines (empty direction/volume=0/generic price): {len(backtest)}")

# Check for trades without SL
no_sl = [r for r in rows if r.get("sl_price","") in ("","0","0.0") and r.get("pnl","0") != "0"]
print(f"Trades without SL: {len(no_sl)}")

# Check duplicates
keys = Counter((r["timestamp"], r["symbol"], r["direction"], r["pnl"]) for r in rows)
dups = {k:v for k,v in keys.items() if v > 1}
print(f"Duplicate (ts+symbol+dir+pnl) groups: {len(dups)} groups, total dup lines: {sum(v for v in dups.values()) - len(dups)}")

# Show backtest samples
print()
print("=== SAMPLE BACKTEST LINES ===")
for r in backtest[:5]:
    print(dict(r))

# Show no-SL samples
print()
print("=== SAMPLE NO-SL LINES ===")
for r in no_sl[:5]:
    print(dict(r))

# Trades today
print()
print("=== TRADES TODAY (10 June) ===")
today = [r for r in rows if r["timestamp"].startswith("2026-06-10")]
print(f"Trades on June 10: {len(today)}")
for r in today:
    sl = r["sl_price"]
    print(f"  {r['timestamp']} {r['symbol']:8s} {r['direction']:5s} {r['volume']:>5s} SL={sl:>8s} PnL={r['pnl']:>8s} [{r['reason']}]")

# Summary
print()
print("=== SUMMARY ===")
print(f"Clean trades: {len(rows) - len(backtest)}")
print(f"Backtest garbage to remove: {len(backtest)}")
print(f"Historical no-SL trades (keep): {len(no_sl)}")
