#!/usr/bin/env python
"""Analyse détaillée des 19 trades pour identifier pourquoi WR=31.6% (au lieu de 60%)."""
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

state_file = Path("runtime/robot_state.json")
with open(state_file) as f:
    state = json.load(f)

trades = state.get("trade_history", [])
print("=" * 80)
print("ANALYSE DÉTAILLÉE DES 19 TRADES LIVE (9-10 Juin)")
print("=" * 80)

# Grouper par symbole
by_symbol = defaultdict(list)
for t in trades:
    by_symbol[t.get("symbol", "?")].append(t)

print("\n📊 PERFORMANCE PAR SYMBOLE:")
for symbol in sorted(by_symbol.keys()):
    sym_trades = by_symbol[symbol]
    wr = sum(1 for t in sym_trades if t.get("profit", 0) > 0) / len(sym_trades)
    pf = sum(t.get("profit", 0) for t in sym_trades if t.get("profit", 0) > 0) / max(1, abs(sum(t.get("profit", 0) for t in sym_trades if t.get("profit", 0) < 0)))
    total_pnl = sum(t.get("profit", 0) for t in sym_trades)
    
    print(f"\n  {symbol}: {len(sym_trades)} trades")
    print(f"    WR={wr:.1%} | PF={pf:.2f} | PnL=${total_pnl:.2f}")
    
    # Détails
    for i, t in enumerate(sym_trades, 1):
        action = t.get("action", "?")
        profit = t.get("profit", 0)
        regime = t.get("regime", "?")
        score = t.get("score", "?")
        hour = "?"
        if "ts" in t:
            try:
                dt = datetime.fromisoformat(t["ts"].replace("Z", "+00:00"))
                hour = f"{dt.hour:02d}"
            except:
                pass
        status = "✓" if profit > 0 else "✗"
        print(f"      {i}. {status} {action:4} {regime:8} score={str(score):5} profit=${profit:7.2f} (hour={hour})")

# Analyse par heure
print("\n🕐 PERFORMANCE PAR HEURE UTC:")
by_hour = defaultdict(list)
for t in trades:
    if "ts" in t:
        try:
            dt = datetime.fromisoformat(t["ts"].replace("Z", "+00:00"))
            by_hour[dt.hour].append(t)
        except:
            pass

for hour in sorted(by_hour.keys()):
    hour_trades = by_hour[hour]
    wr = sum(1 for t in hour_trades if t.get("profit", 0) > 0) / len(hour_trades)
    print(f"  {hour:02d}:00 UTC: {len(hour_trades)} trades, WR={wr:.1%}")

# Analyse par régime
print("\n🌊 PERFORMANCE PAR RÉGIME:")
by_regime = defaultdict(list)
for t in trades:
    by_regime[t.get("regime", "?")].append(t)

for regime in sorted(by_regime.keys()):
    reg_trades = by_regime[regime]
    wr = sum(1 for t in reg_trades if t.get("profit", 0) > 0) / len(reg_trades)
    print(f"  {regime}: {len(reg_trades)} trades, WR={wr:.1%}")

# Analyse des pertes consécutives
print("\n⚠️  PERTES CONSÉCUTIVES:")
consecutive_loss_streak = 0
max_streak = 0
for t in trades:
    if t.get("profit", 0) < 0:
        consecutive_loss_streak += 1
        max_streak = max(max_streak, consecutive_loss_streak)
    else:
        consecutive_loss_streak = 0

print(f"  Max streak: {max_streak}")
print(f"  Actuellement: 5 (CIRCUIT BREAKER DÉCLENCHÉ)")

# Identification du problème
print("\n🔍 DIAGNOSTIC:")
print("  ❌ WR=31.6% au lieu de 60%+ attendu")
print("  ❌ Pertes consécutives: 5 (auto-pause after 3)")
print("  ❌ Circuit breaker déclenché (perte > 3% en 30min)")
print("  ⚠️  Possibles causes:")
print("     1. Timeframe M5→H1 change non appliqué en live (bug import?)")
print("     2. Heure 12:00-14:00 UTC pas bloquée efficacement")
print("     3. Mauvais symboles actifs (NZDUSD pas retiré de la config?)")
print("     4. Paramètres MOM20x3 mal calibrés pour H1")

print("\n" + "=" * 80)
