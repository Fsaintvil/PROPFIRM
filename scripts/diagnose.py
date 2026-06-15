#!/usr/bin/env python
"""Diagnostic rapide du robot FTMO avant redémarrage."""
import json
from pathlib import Path

print("=" * 60)
print("DIAGNOSTIC ROBOT FTMO — 10 Juin 2026")
print("=" * 60)

# État du robot
state_file = Path("runtime/robot_state.json")
if state_file.exists():
    with open(state_file) as f:
        state = json.load(f)
    print("\n📊 ÉTAT DU COMPTE:")
    print(f"  Challenge balance initial: ${state.get('challenge_initial_balance', '?')}")
    print(f"  Peak equity: ${state.get('peak_equity', '?')}")
    print(f"  Trades enregistrés: {len(state.get('trade_history', []))}")
    print(f"  Pertes consécutives: {state.get('consecutive_losses', 0)}")
    
    hist = state.get("trade_history", [])
    if hist:
        recent = hist[-20:]
        wr = sum(1 for t in recent if t.get("profit", 0) > 0) / len(recent)
        pf = sum(t.get("profit", 0) for t in recent if t.get("profit", 0) > 0) / max(1, abs(sum(t.get("profit", 0) for t in recent if t.get("profit", 0) < 0)))
        print(f"  WR (derniers 20): {wr:.1%}")
        print(f"  PF (derniers 20): {pf:.2f}")
        
        # Pertes récentes
        losses = [t for t in recent if t.get("profit", 0) < 0]
        if losses:
            recent_loss = sum(t.get("profit", 0) for t in losses)
            print(f"  Perte récente: ${recent_loss:.2f}")
else:
    print("❌ Pas de state trouvé")

# Rapport quotidien
daily_file = Path("runtime/daily_report.json")
if daily_file.exists():
    with open(daily_file) as f:
        daily = json.load(f)
    print("\n📈 RAPPORT QUOTIDIEN:")
    print(f"  Trades aujourd'hui: {daily.get('trades_today', 0)}")
    print(f"  PnL: ${daily.get('pnl_today', 0):.2f}")
    print(f"  Status: {daily.get('status', '?')}")

# Vérifier les positions ouvertes
print("\n🔍 POSITIONS OUVERTES:")
if "positions_open" in state:
    positions = state.get("positions_open", {})
    if positions:
        print(f"  {len(positions)} position(s):")
        for symbol, qty in positions.items():
            print(f"    {symbol}: {qty}")
    else:
        print("  Aucune position ouverte")

# PID lock
pid_file = Path("runtime/robot.pid")
if pid_file.exists():
    with open(pid_file) as f:
        pid = f.read().strip()
    print(f"\n🔒 PID LOCK: {pid}")
    print("  ⚠️  Le robot est verrouillé — arrêtez le processus avant de redémarrer")

print("\n" + "=" * 60)
