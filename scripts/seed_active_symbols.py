#!/usr/bin/env python3
"""Seed les 3 symboles actifs dans l'OnlineLearner avec trades réalistes.

Utilise les WR de backtest 2026 (conservatives) et R-multiples réalistes
(RR~2.5 pour winners, R=-1 pour losers).
19 Juin 2026: 3 symboles (XAUUSD H4, BTCUSD H1, EURUSD H1)

Usage:
    python scripts/seed_active_symbols.py           # Génère et applique le seed
    python scripts/seed_active_symbols.py --dry-run  # Simulation sans écrire
    python scripts/seed_active_symbols.py --csv-only # Met à jour le CSV seulement
"""

import csv
import json
import os
import random
import sys
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
SEED_CSV = ROOT / "runtime" / "online_learner_seed.csv"
LOCK_FILE = ROOT / "runtime" / "online_learner_seed.lock"
STATE_FILE = ROOT / "runtime" / "ol_state.json"

random.seed(42)  # Reproductible

# ============================================================
# Données par symbole — WR backtest 2026 (ajusté conservateur)
# Juin 2026: 3 symboles actifs (XAUUSD H4, BTCUSD H1, EURUSD H1)
# ============================================================
SYMBOL_CONFIG = {
    "XAUUSD": {"wr": 0.62, "trades": 200, "rr_win": 2.5, "rr_loss": -1.0, "volume": 0.10},
    "BTCUSD": {"wr": 0.65, "trades": 200, "rr_win": 2.5, "rr_loss": -1.0, "volume": 0.05},
    "EURUSD": {"wr": 0.63, "trades": 200, "rr_win": 2.3, "rr_loss": -1.0, "volume": 0.10},
    "USDJPY": {"wr": 0.65, "trades": 200, "rr_win": 2.3, "rr_loss": -1.0, "volume": 0.10},
    "GBPUSD": {"wr": 0.63, "trades": 200, "rr_win": 2.3, "rr_loss": -1.0, "volume": 0.10},
    "AUDUSD": {"wr": 0.62, "trades": 200, "rr_win": 2.2, "rr_loss": -1.0, "volume": 0.10},
    "USDCAD": {"wr": 0.63, "trades": 200, "rr_win": 2.3, "rr_loss": -1.0, "volume": 0.10},
}

# Régimes réalistes (Distribution: 40% RANGING, 25% TREND_UP, 25% TREND_DOWN, 5% HIGH_VOL, 5% LOW_VOL)
REGIMES = ["RANGING"] * 40 + ["TREND_UP"] * 25 + ["TREND_DOWN"] * 25 + ["HIGH_VOL"] * 5 + ["LOW_VOL"] * 5

# Directions
DIRECTIONS = ["BUY", "SELL"]


def generate_trades(symbol: str, cfg: dict) -> list[dict]:
    """Génère N trades réalistes pour un symbole."""
    trades = []
    wr = cfg["wr"]
    rr_win = cfg["rr_win"]
    rr_loss = cfg["rr_loss"]
    n = cfg["trades"]
    volume = cfg["volume"]

    for i in range(n):
        is_win = random.random() < wr
        regime = random.choice(REGIMES)
        direction = random.choice(DIRECTIONS)

        if is_win:
            # Les gagnants ont une distribution de R: 2.0-3.0 selon le trailing
            r_mul = round(rr_win * random.uniform(0.85, 1.15), 2)
            profit = round(r_mul * volume * 100 * random.uniform(0.8, 1.2), 2)
        else:
            # Les perdants: -1.0 (full SL) ou -0.5 (trailing partiel avant SL)
            r_mul = round(random.choice([-1.0, -0.8, -0.6, -0.5, -0.3]), 2)
            profit = round(r_mul * volume * 100 * random.uniform(0.8, 1.2), 2)

        # Année 2025-2026, dates étalées
        year = random.choice([2025, 2026])
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        hour = random.randint(0, 23)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        timestamp = f"{year}.{month:02d}.{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

        trades.append(
            {
                "symbol": symbol,
                "direction": direction,
                "volume": round(volume, 2),
                "profit": profit,
                "r_multiple": r_mul,
                "timestamp": timestamp,
            }
        )

    return trades


def update_csv(trades: list[dict], dry_run: bool = False):
    """Ajoute les trades au fichier CSV existant ou le crée."""
    if dry_run:
        print(f"  [DRY-RUN] Total nouveaux trades: {len(trades)}")
        return

    # Charger l'existant
    existing_symbols = set()
    existing_lines = []
    if SEED_CSV.exists():
        with open(SEED_CSV, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_symbols.add(row["symbol"])
                existing_lines.append(row)

    # Compter les nouveaux par symbole
    new_by_symbol = {}
    for t in trades:
        new_by_symbol.setdefault(t["symbol"], []).append(t)

    # Vérifier ce qui est nouveau
    total_new = 0
    for sym, sym_trades in new_by_symbol.items():
        if sym in existing_symbols:
            old_count = sum(1 for r in existing_lines if r["symbol"] == sym)
            print(f"  {sym}: {old_count} existants → ajout de {len(sym_trades)} (total={old_count + len(sym_trades)})")
        else:
            print(f"  {sym}: 0 existants → ajout de {len(sym_trades)}")
        total_new += len(sym_trades)

    if total_new == 0:
        print("  Aucun nouveau trade à ajouter.")
        return

    # Écrire le nouveau CSV
    fieldnames = ["symbol", "direction", "volume", "profit", "r_multiple", "timestamp"]
    with open(SEED_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing_lines:
            writer.writerow(row)
        for t in trades:
            # Ne pas dupliquer si déjà présent
            if t["symbol"] in existing_symbols and any(
                r["symbol"] == t["symbol"] and r["timestamp"] == t["timestamp"] for r in existing_lines
            ):
                continue
            writer.writerow(t)

    print(f"  ✅ CSV mis à jour: {SEED_CSV} ({sum(1 for _ in open(SEED_CSV)) - 1} trades)")


def reset_state(dry_run: bool = False):
    """Supprime le lock et régénère l'état OnlineLearner."""
    if dry_run:
        print("  [DRY-RUN] Supprimerait le lock et régénérerait l'état")
        return

    # Supprimer le lock
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
        print("  ✅ Lock supprimé: online_learner_seed.lock")

    # Supprimer l'état pour forcer un re-seed au prochain démarrage
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        print("  ✅ État supprimé: online_learner_state.json (re-seed au prochain démarrage)")

    print()
    print("  🔄 AU PROCHAIN DÉMARRAGE DU ROBOT:")
    print("     OnlineLearner re-seedera depuis le CSV mis à jour")
    print("     Les 3 symboles actifs auront 200 trades avec WR réaliste")
    print("     Les adapted_params seront générés automatiquement")


def print_stats(csv_path: Path):
    """Affiche les stats du CSV."""
    if not csv_path.exists():
        print("  ⚠️  Fichier CSV introuvable")
        return

    symbols = {}
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = row["symbol"]
            if sym not in symbols:
                symbols[sym] = {"trades": 0, "wins": 0, "total_r": 0.0}
            symbols[sym]["trades"] += 1
            r = float(row["r_multiple"])
            symbols[sym]["total_r"] += r
            if r > 0:
                symbols[sym]["wins"] += 1

    print(f"\n  {'Symbole':<15} {'Trades':<8} {'WR':<8} {'Exp':<8}")
    print(f"  {'─' * 40}")
    for sym in sorted(symbols.keys()):
        s = symbols[sym]
        wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
        exp = s["total_r"] / s["trades"] if s["trades"] else 0
        print(f"  {sym:<15} {s['trades']:<8} {wr:<7.1f}% {exp:<8.2f}")
    print(f"  {'─' * 40}")
    total_trades = sum(s["trades"] for s in symbols.values())
    print(f"  {'TOTAL':<15} {total_trades:<8}")


def main():
    dry_run = "--dry-run" in sys.argv
    csv_only = "--csv-only" in sys.argv

    print("=" * 55)
    print("  SEED ONLINE LEARNER — 3 Symboles Actifs")
    print("=" * 55)

    # Générer les trades
    print("\n📊 Génération des trades...")
    all_trades = []
    for sym, cfg in SYMBOL_CONFIG.items():
        trades = generate_trades(sym, cfg)
        all_trades.extend(trades)
        wr = cfg["wr"]
        print(f"  {sym}: {cfg['trades']} trades, WR={wr * 100:.0f}%, RR_win={cfg['rr_win']}")

    # Mettre à jour le CSV
    print(f"\n📝 Mise à jour du CSV: {SEED_CSV}")
    update_csv(all_trades, dry_run=dry_run)

    # Stats après mise à jour
    if not dry_run:
        print("\n📈 Stats après seed:")
        print_stats(SEED_CSV)

    # Reset state (sauf si csv-only)
    if not csv_only:
        print(f"\n🔄 Reset du lock/state:")
        reset_state(dry_run=dry_run)
    else:
        print(f"\n  [--csv-only] Lock et state conservés")

    print("\n✅ Terminé.")


if __name__ == "__main__":
    main()
