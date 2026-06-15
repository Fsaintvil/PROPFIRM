"""Vérification production : positions, PnL, trailing, gains sécurisés."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Try to import MT5 connector — check engine_simple/ first
mc_mod = None
for mod_name in ("engine_simple.mt5_connector", "mt5_connector"):
    try:
        mc_mod = __import__(mod_name, fromlist=["MT5Connector"])
        break
    except ImportError:
        continue

if mc_mod:
    mc = mc_mod
else:
    print("⚠️  mt5_connector not available — using state only")
    mc = None

print("=" * 60)
print("  PRODUCTION CHECK — Robot FTMO MOM20x3")
print(f"  {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
print("=" * 60)


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ── 1. MT5 Live ────────────────────────────────────────────────
if mc:
    try:
        mc.initialize()
        acc = mc.account_info()
        print(f"\n📊 MT5: Balance=${acc.balance:,.2f}  Equity=${acc.equity:,.2f}  "
              f"Float={acc.profit:+,.2f}")
        dd = (1 - acc.equity / acc.balance) * 100 if acc.balance else 0
        print(f"     DD depuis balance: {dd:.2f}%")

        positions = mc.get_positions()
        if not positions:
            print("\n📭 Aucune position ouverte")
        else:
            print(f"\n📊 {len(positions)} positions:")
            profitable = 0
            secured = 0
            for p in positions:
                label = "🟢" if p.profit > 0 else ("🔴" if p.profit < 0 else "⚪")
                print(f"  {label} #{p.ticket} {p.symbol} {p.type_str} "
                      f"lot={p.volume:.2f} entry={p.price:.5f} "
                      f"SL={p.sl:.5f} TP={p.tp:.5f} "
                      f"PnL={p.profit:+,.2f}")
                if p.profit > 0:
                    profitable += 1
                    # Check if SL is above entry (long) or below entry (short) → secured
                    if p.type_str == "buy" and p.sl and p.sl > p.price:
                        secured += 1
                    elif p.type_str == "sell" and p.sl and p.sl < p.price:
                        secured += 1
            print(f"\n   🟢 Profitable: {profitable}/{len(positions)}")
            print(f"   🔒 Gains sécurisés (SL > entry): {secured}/{profitable}")
        mc.shutdown()
    except Exception as e:
        print(f"\n⚠️  Erreur MT5: {e}")
        import traceback
        traceback.print_exc()
else:
    print("⚠️  MT5 connector not available")

# ── 2. Robot State ─────────────────────────────────────────────
state = load_json("runtime/robot_state.json")
print(f"\n📁 RUNTIME STATE")
print(f"   Challenge initial: ${state.get('challenge_initial_balance',0):,.2f}")
print(f"   Peak equity:       ${state.get('peak_equity',0):,.2f}")
print(f"   Consecutive losses: {state.get('consecutive_losses',0)}")
print(f"   Trading days:       {len(state.get('trading_days_list',[]))}")
print(f"   Daily PnL:          ${sum(state.get('daily_pnl_by_date',{}).values()):+,.2f}")
print(f"   Challenge status:   {state.get('challenge_status','?')}")

peaks = state.get("trailing_peaks", {})
if peaks:
    print(f"\n📍 TRAILING PEAKS ({len(peaks)} actifs)")
    for tick, peak in list(peaks.items())[:8]:
        regime = state.get("position_regime", {}).get(tick, "?")
        print(f"   #{tick}: peak={peak:.5f} regime={regime}")

# ── 3. Performance History ─────────────────────────────────────
perf = load_json("runtime/performance_history.json")
trades = perf.get("trades", [])
if trades:
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    print(f"\n📈 PERFORMANCE HISTORY ({len(trades)} trades)")
    print(f"   WR: {wins}/{len(trades)} ({wins/len(trades)*100:.1f}%)")
    print(f"   Total PnL: ${total_pnl:+,.2f}")
    last = trades[-1]
    print(f"   Dernier trade: {last.get('symbol','?')} {last.get('action','?')} "
          f"PnL={last.get('pnl',0):+,.2f}")
else:
    print(f"\n📈 PERFORMANCE HISTORY: 0 trades (attente 1er trade)")

# ── 4. Trade Log CSV ───────────────────────────────────────────
csv_path = "runtime/trades_log.csv"
try:
    with open(csv_path) as f:
        lines = f.readlines()
    print(f"\n📋 TRADES LOG CSV: {len(lines) - 1} trades")
    if len(lines) > 1:
        print(f"   Header: {lines[0].strip()}")
        print(f"   Dernier: {lines[-1].strip()}")
except FileNotFoundError:
    print(f"\n📋 TRADES LOG CSV: fichier non trouvé (attente 1er trade)")

# ── 5. Trading Journal DB ──────────────────────────────────────
db_path = "runtime/trading_journal.db"
if Path(db_path).exists():
    size_kb = Path(db_path).stat().st_size / 1024
    print(f"\n🗄️  TRADING JOURNAL DB: {size_kb:.1f} KB (existe)")
else:
    print(f"\n🗄️  TRADING JOURNAL DB: non trouvé (attente 1er trade)")

# ── 6. Derniers logs ──────────────────────────────────────────
log_path = "logs/simple_robot.log"
try:
    with open(log_path) as f:
        tail = f.readlines()[-20:]
    errors = [l for l in tail if "ERROR" in l or "CRITICAL" in l]
    print(f"\n📝 DERNIERS LOGS ({len(tail)} lignes)")
    if errors:
        for e in errors:
            print(f"   ❌ {e.strip()}")
    else:
        last_log = tail[-1].strip() if tail else "—"
        print(f"   ✅ Aucune erreur. Dernier: {last_log[:120]}")
except FileNotFoundError:
    print(f"\n📝 LOGS: fichier non trouvé")

print("\n" + "=" * 60)
print("  VÉRIFICATION TERMINÉE")
print("=" * 60)
