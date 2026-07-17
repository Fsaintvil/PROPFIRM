#!/usr/bin/env python3
"""Re-seed l'OnlineLearner avec les VRAIS trades de trades_log.csv.

Au lieu de données synthétiques (WR fixe, R aléatoire), ce script utilise
les trades réellement exécutés et filtrés : r >= 0.1, symbole actif.

Usage:
    python scripts/reseal_ol_reel.py              # Appliquer
    python scripts/reseal_ol_reel.py --dry-run    # Simulation
"""

import csv
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADES_CSV = ROOT / "runtime" / "trades_log.csv"
OL_STATE = ROOT / "runtime" / "ol_state.json"
LOCK_FILE = ROOT / "runtime" / "online_learner_seed.lock"

# Taille de fenêtre OL
WINDOW = 200

# Régimes simulés à partir de la direction (on n'a pas le vrai régime dans le CSV)
REGIME_MAP = {"BUY": "RANGING", "SELL": "RANGING"}


def load_trades():
    """Charge et filtre les trades du CSV."""
    if not TRADES_CSV.exists():
        print(f"ERREUR: {TRADES_CSV} introuvable")
        return []

    trades = []
    with open(TRADES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                pnl = float(row.get("pnl", 0))
                entry = float(row.get("entry_price", 0))
                sl = float(row.get("sl_price", 0))
                tp = float(row.get("tp_price", 0))
                volume = float(row.get("volume", 0))
                symbol = row.get("symbol", "").strip()
                direction = row.get("direction", "").strip().upper()

                if not symbol or not direction or entry == 0 or sl == 0:
                    continue

                # Calculer le r_multiple = PnL / risque_initial
                # risque_initial = |entry - sl| * lot * contract_multiplier
                risk_per_unit = abs(entry - sl)
                if risk_per_unit < 0.00001:
                    continue

                # Le contract multiplier dépend du symbole
                if "JPY" in symbol or "XAG" in symbol or "XAU" in symbol:
                    contract = 1000  # métaux, JPY crosses
                elif "BTC" in symbol or "ETH" in symbol or "SOL" in symbol or "BNB" in symbol or "LNK" in symbol:
                    contract = 1  # crypto en CFD
                elif "JP225" in symbol or "US500" in symbol or "US100" in symbol or "US30" in symbol:
                    contract = 1  # indices en CFD
                elif "GER40" in symbol or "UK100" in symbol:
                    contract = 1  # indices EU
                elif "USOIL" in symbol or "UKOIL" in symbol or "NATGAS" in symbol:
                    contract = 100  # commodités
                else:
                    contract = 100000  # forex standard

                risk_amount = risk_per_unit * volume * contract
                if risk_amount == 0:
                    continue

                r = pnl / risk_amount

                # 🔧 FIX: ignorer le bruit (r < 0.1)
                if abs(r) < 0.1:
                    continue

                trades.append(
                    {
                        "symbol": symbol,
                        "r": round(r, 4),
                        "regime": REGIME_MAP.get(direction, "RANGING"),
                        "volume": volume,
                        "pnl": pnl,
                        "entry": entry,
                        "sl": sl,
                    }
                )
            except (ValueError, TypeError, ZeroDivisionError):
                continue

    return trades


def reseed(trades, dry_run=False):
    """Re-seed l'OL avec les trades filtrés."""
    # Grouper par symbole
    by_symbol = defaultdict(list)
    for t in trades:
        by_symbol[t["symbol"]].append(t)

    # Charger l'état OL existant
    if OL_STATE.exists():
        with open(OL_STATE) as f:
            state = json.load(f)
    else:
        state = {"trades": [], "adapted_params": {}}

    # Compter les trades par symbole dans l'OL actuel
    existing_by_symbol = defaultdict(list)
    for t in state.get("trades", []):
        existing_by_symbol[t.get("symbol", "?")].append(t)

    # Ajouter les nouveaux trades (garder les existants)
    all_trades = list(state.get("trades", []))
    added = 0
    for sym, sym_trades in sorted(by_symbol.items()):
        # Prendre les WINDOW plus récents (trier par... timestamp pas dispo, on prend la fin du CSV)
        # Limiter à WINDOW trades max par symbole
        existing_count = len(existing_by_symbol.get(sym, []))
        remaining = max(0, WINDOW - existing_count)

        if remaining <= 0:
            print(f"  {sym:<12} déjà {existing_count} trades (≥{WINDOW}) — skip")
            continue

        # Prendre les 'remaining' derniers trades du CSV pour ce symbole
        take = min(remaining, len(sym_trades))
        new_trades = sym_trades[-take:]

        for nt in new_trades:
            all_trades.append(
                {
                    "symbol": nt["symbol"],
                    "r": nt["r"],
                    "regime": nt["regime"],
                }
            )

        added += len(new_trades)
        print(f"  {sym:<12} {existing_count} existants + {len(new_trades)} ajoutés (max {WINDOW})")

    print(f"\nTotal: {len(state.get('trades', []))} existants + {added} ajoutés = {len(all_trades)} trades")

    if dry_run:
        print("\n🔷 DRY RUN — rien n'a été écrit")
        return

    # Sauvegarder
    state["trades"] = all_trades
    # Reset adapted_params — l'OL les recalculera au prochain démarrage
    if "adapted_params" in state:
        del state["adapted_params"]

    # Backup de l'ancien état
    if OL_STATE.exists():
        backup = OL_STATE.with_suffix(".json.bak2")
        os.replace(OL_STATE, backup)
        print(f"Ancien état sauvegardé: {backup}")

    with open(OL_STATE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"✓ OL State écrit: {OL_STATE} ({len(all_trades)} trades)")

    # Supprimer le lock pour que le seed soit ré-appliqué au démarrage
    if LOCK_FILE.exists():
        os.remove(LOCK_FILE)
        print(f"✓ Lock supprimé: {LOCK_FILE}")

    # Résumé
    print("\n=== RÉSUMÉ PAR SYMBOLE ===")
    final_by_symbol = defaultdict(list)
    for t in all_trades:
        final_by_symbol[t.get("symbol", "?")].append(t)
    for sym, st in sorted(final_by_symbol.items()):
        n = len(st)
        rr = [s["r"] for s in st]
        wr = sum(1 for r in rr if r > 0) / n if n else 0
        avg_r = sum(rr) / n if n else 0
        print(f"  {sym:<12} {n:>4} trades | WR={wr:.1%} | avg_r={avg_r:+.3f}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print("=== Re-seed OL avec vrais trades ===")
    print(f"Source: {TRADES_CSV}")
    print()

    trades = load_trades()
    print(f"Trades chargés: {len(trades)} (filtrés r>=0.1)")

    if not trades:
        print("Aucun trade valide — abandon")
        sys.exit(1)

    # Stats
    symbols = set(t["symbol"] for t in trades)
    print(f"Symboles: {len(symbols)} — {', '.join(sorted(symbols))}")
    print()

    reseed(trades, dry_run=dry)
