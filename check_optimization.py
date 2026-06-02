"""Check optimisation — lit runtime/ftmo_report.json + robot_state.json"""
import json
from datetime import datetime

try:
    report = json.load(open("runtime/ftmo_report.json"))
    state = json.load(open("runtime/robot_state.json"))
except FileNotFoundError as e:
    print(f"Fichier non trouvé: {e.filename} — le robot tourne-t-il ?")
    exit(1)

print("="*100)
print(f"RAPPORT D'OPTIMISATION - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*100)

print("\n>>> OPTIMISATIONS APPLIQUÉES:")
print("  ✅ MIN_SIGNAL_SCORE: 0.60 (sélectivité)")
print("  ✅ MAX_TRADES_PER_DAY: 75 (réduction)")
print("  ✅ RISK_PER_TRADE: 0.4% / RISK_SHORT_MULT: 0.5 (gestion directionnelle)")
print("  ✅ SL: 2×ATR / TP: 5×ATR (trending/HIGH_VOL), 1.5×ATR/4×ATR (RANGING/LOW_VOL)")
print("  ✅ MIN_RR_RATIO: 2.0")
print("  ✅ AUTO_PAUSE_LOSSES: 3")

print("\n>>> ÉTAT DU COMPTE:")
print(f"  Balance: ${report.get('balance', 0):,.0f}")
print(f"  Equity: ${report.get('equity', 0):,.0f}")
print(f"  P&L: ${report.get('pnl', 0):+,.0f}")
print(f"  DD from init: {report.get('dd_from_initial', '?')}")
print(f"  DD from peak: {report.get('dd_from_peak', '?')}")
print(f"  Peak equity: ${report.get('peak_equity', 0):,.0f}")
print(f"  Status: {report.get('status', '?')}")
print(f"  Pertes consécutives: {report.get('consecutive_losses', 0)}")

print("\n>>> STATISTIQUES DE TRADING:")
print(f"  Total trades: {report.get('total_trades', 0)}")
print(f"  Win rate: {report.get('win_rate', '?')}")
print(f"  Profit progress: {report.get('profit_progress', '?')}")
print(f"  Profit restant: {report.get('profit_remaining', '?')}")
print(f"  Daily P&L: {report.get('daily_pnl', '$0')}")
print(f"  Jours tradés: {report.get('trading_days', 0)} / {report.get('days_remaining', 0)} restants")
print(f"  Meilleur jour: {report.get('best_day_pct', '?')} du total")

print("\n>>> CYCLE D'OPTIMISATION:")
start = state.get('restart_timestamps', [])
if start:
    last = datetime.fromtimestamp(start[-1])
    mins = (datetime.now() - last).total_seconds() / 60
    print(f"  Dernier redémarrage: ~{mins:.0f} min")
recent_restarts = [t for t in state.get('restart_timestamps', [])
                   if datetime.now().timestamp() - t < 3600]
print(f"  Redémarrages (1h): {len(recent_restarts)}")

print("\n>>> PROCHAINES ÉTAPES:")
print("  1. Surveiller le WR après 100 trades fermés")
print("  2. Vérifier que la consistance FTMO reste < 30%/jour")
print("  3. Attendre +$20K pour PASS FTMO")
print("  4. Vérifier que daily_profit_reduced ne bloque pas les gains")

print("\n" + "="*100)
