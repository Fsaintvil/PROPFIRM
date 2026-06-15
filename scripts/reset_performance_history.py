"""reset_performance_history.py — Nettoie performance_history.json après correction du bug import_history.

Le fichier a été contaminé par des doublons d'import (les mêmes trades historiques
réimportés à chaque redémarrage). Ce script :
1. Sauvegarde le fichier actuel
2. Réinitialise en ne gardant que :
   - La section challenge (correcte, lue depuis MT5)
   - Les daily vérifiés propres (2026-06-07)
   - Trades, rolling, alerts, symbols repartent à zéro

Usage:
    python scripts/reset_performance_history.py
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path("runtime") / "performance_history.json"
BACKUP_SUFFIX = ".pre_bilan"


def main():
    if not HISTORY_FILE.exists():
        print(f"❌ Fichier introuvable : {HISTORY_FILE}")
        return 1

    # 1. Backup
    backup_path = HISTORY_FILE.with_suffix(HISTORY_FILE.suffix + BACKUP_SUFFIX)
    shutil.copy2(HISTORY_FILE, backup_path)
    print(f"✅ Backup → {backup_path}")

    # 2. Charger le fichier actuel
    with open(HISTORY_FILE) as f:
        data = json.load(f)

    # 3. Vérifier que le backup n'écrase pas un existant
    challenge = data.get("challenge", {})

    # 4. Construire la structure propre
    clean_daily = {}

    # Garder 2026-06-07 (vérifié : 4 trades, 4 wins, $400 PnL — trades propres)
    if "daily" in data and "2026-06-07" in data["daily"]:
        clean_daily["2026-06-07"] = data["daily"]["2026-06-07"]
        print(f"✅ Conservé daily 2026-06-07 : "
              f"{clean_daily['2026-06-07']['trades']} trades, "
              f"${clean_daily['2026-06-07']['pnl']:.0f} PnL")

    # 5. Structure de réinitialisation
    clean = {
        "daily": clean_daily,
        "rolling": {},
        "symbols": {},
        "alerts": [],
        "recent_trades": [],
        "challenge": challenge,
    }

    # 6. Écrire
    with open(HISTORY_FILE, "w") as f:
        json.dump(clean, f, indent=2)

    print(f"✅ {HISTORY_FILE} réinitialisé")
    print(f"   Challenge conservé : status={challenge.get('status')}, "
          f"trades={challenge.get('total_trades')}, "
          f"profit={challenge.get('profit_progress')}")
    print(f"   Daily conservé : 2026-06-07")
    print(f"   Rolling windows, symboles, alerts, recent_trades → vidés")
    print()
    print("⚠️  Les métriques rolling repartent à zéro. "
          "Les nouveaux trades seront correctement enregistrés à partir de maintenant.")
    return 0


if __name__ == "__main__":
    exit(main())
