"""Vérification finale du statut robot + config"""
import json
from pathlib import Path
import csv
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

runtime = Path("runtime")

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

# FTMO Report
ftmo = json.loads((runtime / "ftmo_report.json").read_text())
print("=== FTMO REPORT ===")
print(f"  Status: {ftmo.get('status','N/A')}")
print(f"  Balance: ${safe_float(ftmo.get('balance',0)):.2f}")
print(f"  Equity: ${safe_float(ftmo.get('equity',0)):.2f}")
print(f"  DD: {safe_float(ftmo.get('drawdown_pct',0)):.2f}%")
print(f"  Daily PnL: ${safe_float(ftmo.get('daily_pnl',0)):.2f}")
print(f"  Profit Progress: {ftmo.get('profit_progress','N/A')}")
print(f"  Trades today: {ftmo.get('total_trades',0)}")
print(f"  Win Rate: {safe_float(ftmo.get('win_rate',0)):.1f}%")
print(f"  Consecutive losses: {ftmo.get('consecutive_losses',0)}")
print(f"  Daily loss used: {safe_float(ftmo.get('daily_loss_pct',0))*100:.2f}%")

# Verify USDCHF max_lot in config
print("\n=== CONFIG ACTIVE ===")
import config_simple as cfg
ul = cfg.SYMBOL_LIMITS.get("USDCHF", {})
print(f"  USDCHF max_lot: {ul.get('max_lot', '?')}")
print(f"  USDCHF min_lot: {ul.get('min_lot', '?')}")
print(f"  cooldown_minutes: {cfg.COOLDOWN_MINUTES}")
print(f"  max_trades_per_day: {cfg.MAX_TRADES_PER_DAY}")
print(f"  max_orders_per_minute: {cfg.MAX_ORDERS_PER_MINUTE}")

# CSV stats
with open(runtime / "trades_log.csv") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
print(f"\n=== CSV (post-clean) ===")
print(f"  Total trades: {len(rows)}")
today = [r for r in rows if r["timestamp"].startswith("2026-06-10")]
print(f"  Today: {len(today)} trades")
if today:
    wins = sum(1 for r in today if safe_float(r.get("pnl",0)) > 0)
    losses = sum(1 for r in today if safe_float(r.get("pnl",0)) < 0)
    print(f"  Today WR: {wins}/{len(today)} = {wins/len(today)*100:.1f}%")
else:
    print(f"  Today WR: N/A")

# Alertes
print("\n=== ALERTES ===")
alerts = []
dl = safe_float(ftmo.get("daily_loss_pct",0))
if dl > 0.015:
    alerts.append(f"🔴 Daily loss {dl*100:.2f}% proche de 2%!")
dd = safe_float(ftmo.get("drawdown_pct",0))
if dd > 0.07:
    alerts.append(f"🔴 DD {dd*100:.2f}% > 7%!")
if today and len(today) > 15:
    wr = wins/len(today)
    if wr < 0.4:
        alerts.append(f"🟡 WR today {wr*100:.0f}% < 40%")
if alerts:
    for a in alerts:
        print(f"  {a}")
else:
    print("  ✅ Aucune alerte")
