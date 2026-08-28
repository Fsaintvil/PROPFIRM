"""List all active symbols and clean trade counts."""
import json, csv
from pathlib import Path
from collections import defaultdict

# 1. Golden Rule state
gr_path = Path("runtime/golden_rule/state.json")
if gr_path.exists():
    with open(gr_path, "r") as f:
        gr = json.load(f)
    print("=== REGLE D'OR ===")
    print(f"Total trades: {gr['total_trades']}/{gr['target_trades']}")
    print(f"WR: {gr['win_rate']:.1%}  PF: {gr['profit_factor']:.2f}")
    print(f"Status: {gr['status']}")
    print()

# 2. Trades log - count per symbol
log_path = Path("runtime/trades_log.csv")
if not log_path.exists():
    print("trades_log.csv not found")
    exit()

trades = []
with open(log_path, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        trades.append(row)

# Filter GR challenge trades
challenge = [t for t in trades if t.get("phase", "").strip() == "challenge"]
print(f"=== TRADES PROPRES (challenge) ===")
print(f"Total: {len(challenge)}")
print()

# Per symbol
sym = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0})
for t in challenge:
    s = t.get("symbol", "?")
    pnl = float(t.get("pnl", 0) or 0)
    sym[s]["count"] += 1
    sym[s]["pnl"] += pnl
    if pnl > 0:
        sym[s]["wins"] += 1
    elif pnl < 0:
        sym[s]["losses"] += 1

print(f"{'Symbole':<14} {'Trades':>7} {'WR':>7} {'PnL':>10}")
print("-" * 42)
total = 0
for s in sorted(sym.keys()):
    d = sym[s]
    wr = d["wins"] / d["count"] * 100 if d["count"] else 0
    total += d["pnl"]
    print(f"{s:<14} {d['count']:>7} {wr:>6.1f}% {d['pnl']:>+10.2f}")
print("-" * 42)
print(f"{'TOTAL':<14} {len(challenge):>7} {'':>7} {total:>+10.2f}")

# 3. Active symbols from config
print()
print("=== SYMBOLES ACTIFS (config) ===")
import yaml
with open("config/default.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
for name, lim in sorted(cfg.get("symbol_limits", {}).items()):
    lot = lim.get("max_lot", "?")
    ms = lim.get("min_score", "?")
    risk = lim.get("risk_mult", "?")
    print(f"  {name:<14} max_lot={lot}  min_score={ms}  risk_mult={risk}")
