#!/usr/bin/env python3
"""
Check GR Stalled — alerte "0 trade depuis 24h" sur les 5 symboles RÈGLE D'OR.
Créé 17 Août 2026 (Robot Manager) — Phase 1, surveillance de production.

But : détecter quand un symbole GR ne produit AUCUN trade fermé depuis 24h.
Les trades sont lus depuis runtime/trades_log.csv (journal des trades fermés,
source utilisée par golden_rule.py). Un symbole GR à l'arrêt 24h est un
signal d'investigation (marché baissier OU paramètre bloquant).

Usage :
    python scripts/check_gr_stalled.py               # rapport + alerte si stalled
    python scripts/check_gr_stalled.py --threshold 48  # seuil personnalisé (heures)
    python scripts/check_gr_stalled.py --watch 3600    # boucle (défaut 3600s)

Sortie : alertes via engine_simple.notifier (Telegram si configuré, sinon log).
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

GR_SYMBOLS = ["US100.cash", "US30.cash", "JP225.cash", "SOLUSD", "BTCUSD"]
CSV_PATH = ROOT / "runtime" / "trades_log.csv"
# Borne de démarrage de la RÈGLE D'OR (heure journal UTC+3) — référence du framework
GR_START = datetime(2026, 8, 13, 21, 20)


def last_trade_per_symbol(threshold_h: float) -> dict:
    """Dernier trade fermé par symbole (depuis la borne GR, pas le début du CSV)."""
    now = datetime.now()
    last = {}
    if not CSV_PATH.exists():
        return last
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 12:
                continue
            try:
                ts = datetime.fromisoformat(row[0].replace(" ", "T"))
            except Exception:
                continue
            if ts < GR_START:
                continue  # ignore l'historique pré-GR
            symbol = row[1]
            if symbol in GR_SYMBOLS:
                # Prend le plus récent
                if symbol not in last or ts > last[symbol]["ts"]:
                    try:
                        pnl = float(row[10])
                    except Exception:
                        pnl = 0.0
                    last[symbol] = {"ts": ts, "pnl": pnl}
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=24.0, help="Seuil d'arrêt (heures)")
    ap.add_argument("--watch", type=int, default=0, help="Boucle continue (secondes)")
    ap.add_argument("--quiet", action="store_true", help="Pas d'alerte notifier, rapport seul")
    args = ap.parse_args()

    # Charge le notifier (déprécié silencieusement si Telegram absent)
    from engine_simple.notifier import Notifier
    notifier = Notifier()

    while True:
        now = datetime.now()
        last = last_trade_per_symbol(args.threshold)
        stalled = []
        for sym in GR_SYMBOLS:
            if sym not in last:
                stalled.append((sym, None, None))
                continue
            age_h = (now - last[sym]["ts"]).total_seconds() / 3600
            if age_h > args.threshold:
                stalled.append((sym, age_h, last[sym]["pnl"]))

        print("=" * 60)
        print(f"GR STALL CHECK — {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        for sym in GR_SYMBOLS:
            if sym in last:
                age_h = (now - last[sym]["ts"]).total_seconds() / 3600
                flag = "⚠️ STALLED" if age_h > args.threshold else "OK"
                print(f"  {sym:12s} dernier trade: {last[sym]['ts'].strftime('%m/%d %H:%M')} "
                      f"({age_h:.1f}h) pnl={last[sym]['pnl']:+.2f} {flag}")
            else:
                print(f"  {sym:12s} AUCUN trade depuis la borne GR ⚠️")

        if stalled and not args.quiet:
            msg = "⚠️ GR STALL (0 trade 24h+):\n" + "\n".join(
                f"  {s}: {f'{a:.0f}h' if a else 'jamais'}" + (f" (pnl {p:+.2f})" if p is not None else "")
                for s, a, p in stalled
            )
            notifier.send(msg)
            print(f"\n  → Alerte envoyée (notifier={'Telegram' if notifier.is_enabled() else 'log'})")
        print()

        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()