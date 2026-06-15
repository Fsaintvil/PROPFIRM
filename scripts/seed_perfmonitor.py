"""
Importe les trades existants dans le Performance Monitor.
⚠️  ATTENTION: Ce script importe TOUS les trades du compte, pas seulement
   ceux du robot (magic 999001). Privilégier le seeding via Excel:
   python scripts/seed_from_excel.py

   À exécuter une fois pour initialiser l'historique.
"""
import sys
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from collections import defaultdict
from datetime import datetime

from engine_simple.performance_monitor import get_monitor

base = Path(__file__).parent.parent / "runtime"

# 1. Importer depuis trading_journal.db (trades avec profit)
print("Importation depuis trading_journal.db...")
conn = sqlite3.connect(str(base / "trading_journal.db"))
rows = conn.execute("""
    SELECT symbol, profit, action, entry_time, exit_time, reason, regime
    FROM trades
    WHERE profit IS NOT NULL AND profit != 0
    ORDER BY entry_time
""").fetchall()
conn.close()

pm = get_monitor()
count = 0
for symbol, profit, action, entry_time, exit_time, reason, regime in rows:
    direction = "BUY"
    if reason:
        reason_lower = reason.lower()
        if "buy" in reason_lower:
            direction = "BUY"
        elif "sell" in reason_lower:
            direction = "SELL"
    pm.record_trade(symbol or "UNKNOWN", profit or 0, regime or "UNKNOWN", direction)
    count += 1

print(f"✅ {count} trades importés dans le Performance Monitor")

# 2. Importer depuis trades_log.csv
print("\nImportation depuis trades_log.csv...")
import csv
csv_count = 0
csv_path = base / "trades_log.csv"
if csv_path.exists():
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                pnl = float(r.get("pnl", 0))
                if pnl == 0:
                    continue
                symbol = r.get("symbol", "UNKNOWN")
                direction = r.get("direction", "BUY")
                reason = r.get("reason", "UNKNOWN")
                # Le regime n'est pas dans le CSV trades_log, on utilise un défaut
                pm.record_trade(symbol, pnl, "UNKNOWN", direction)
                csv_count += 1
            except (ValueError, KeyError):
                pass

print(f"✅ {csv_count} trades supplémentaires importés depuis trades_log.csv")

# 3. Mettre à jour le challenge avec les données actuelles
print("\nImportation du statut challenge FTMO...")
state_path = base / "ftmo_report.json"
if state_path.exists():
    import json
    with open(state_path) as f:
        ftmo = json.load(f)
    pm.record_challenge(ftmo)
    print(f"✅ Challenge mis à jour: {ftmo.get('status')} | {ftmo.get('profit_progress')}")

# Rapport
print("\n" + "=" * 60)
print(pm.summary_text(detailed=True))
print(f"\n📁 Historique sauvegardé dans runtime/performance_history.json")
