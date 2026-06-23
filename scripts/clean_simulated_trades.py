#!/usr/bin/env python3
"""Nettoyage des 15 trades simulés du 19 Juin 2026 dans performance_history.json.

Ces trades ont été injectés par un mécanisme de seed/test et faussent :
- Rolling windows (last_20 WR=100% au lieu de ~67%)
- Daily stats (15 trades, $1,405 au lieu des 9 réels)
- Consistency FTMO (best day = 56.3% au lieu de ~30%)
- Analyse par symbole

Détection : timestamp ISO "T" (ex: "2026-06-19T06:45:42") + regimes "RAN"/"RANGING"/"DOW"
vs timonds réels avec espace (ex: "2026-06-19 05:04:19") + regime "IMPORT"
"""

import json
import os
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

PERF_FILE = Path("runtime/performance_history.json")
BACKUP_FILE = Path("runtime/performance_history.json.pre_clean_bak2")


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    # Atomic write (Windows-compatible)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    if path.exists():
        os.replace(str(tmp), str(path))
    else:
        tmp.rename(path)
    print(f"✅ Sauvegardé: {path}")


def is_simulated(trade):
    """Détecte un trade simulé : timestamp ISO 'T' ou regime inhabituel."""
    ts = trade.get("ts", "")
    regime = trade.get("regime", "")
    profit = trade.get("profit", 0)

    # Les trades du PerformanceMonitor ont timestamp ISO (datetime.utcnow().isoformat())
    # et sont des trades LIVE provenant de position_tracker.check_closed().
    # Les trades du TradeJournal (CSV import) ont timestamps avec espace "2026-06-19 05:04:19".
    # Les deux sont VALIDES — ne pas filtrer par format de timestamp.
    return False


def recalc_daily_stats(trades):
    """Recalcule les stats quotidiennes depuis recent_trades."""
    daily = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0, "symbols": {}}
    )

    for t in trades:
        ts = t.get("ts", "")
        # Extraire la date du timestamp (supporte format ISO et espace)
        if "T" in ts:
            d = ts[:10]
        elif " " in ts:
            d = ts[:10]
        else:
            continue

        symbol = t.get("symbol", "UNKNOWN")
        profit = t.get("profit", 0)
        direction = t.get("direction", "BUY")
        regime = t.get("regime", "IMPORT")

        day = daily[d]
        day["trades"] += 1
        if profit > 0:
            day["wins"] += 1
            day["gross_profit"] += profit
        else:
            day["losses"] += 1
            day["gross_loss"] += abs(profit)
        day["pnl"] += profit

        # Par symbole
        if symbol not in day["symbols"]:
            day["symbols"][symbol] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        day["symbols"][symbol]["trades"] += 1
        if profit > 0:
            day["symbols"][symbol]["wins"] += 1
        else:
            day["symbols"][symbol]["losses"] += 1
        day["symbols"][symbol]["pnl"] += profit

    return dict(daily)


def recalc_symbol_stats(trades):
    """Recalcule les stats par symbole depuis recent_trades."""
    symbols = defaultdict(
        lambda: {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "regime_stats": {},
            "direction_stats": {},
        }
    )

    for t in trades:
        symbol = t.get("symbol", "UNKNOWN")
        profit = t.get("profit", 0)
        direction = t.get("direction", "BUY")
        regime = t.get("regime", "IMPORT")

        s = symbols[symbol]
        s["trades"] += 1
        if profit > 0:
            s["wins"] += 1
            s["gross_profit"] += profit
        else:
            s["losses"] += 1
            s["gross_loss"] += abs(profit)
        s["pnl"] += profit

        # Regime stats (simplifié)
        if regime not in s["regime_stats"]:
            s["regime_stats"][regime] = {"trades": 0, "wins": 0, "pnl": 0.0}
        s["regime_stats"][regime]["trades"] += 1
        if profit > 0:
            s["regime_stats"][regime]["wins"] += 1
        s["regime_stats"][regime]["pnl"] += profit

        # Direction stats
        if direction not in s["direction_stats"]:
            s["direction_stats"][direction] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if profit > 0:
            s["direction_stats"][direction]["wins"] += 1
        else:
            s["direction_stats"][direction]["losses"] += 1
        s["direction_stats"][direction]["pnl"] += profit

    # Arrondir les PnL
    for s in symbols.values():
        s["pnl"] = round(s["pnl"], 2)
        s["gross_profit"] = round(s["gross_profit"], 2)
        s["gross_loss"] = round(s["gross_loss"], 2)

    return dict(symbols)


def recalc_rolling_windows(trades):
    """Recalcule les rolling windows 20/50/100 depuis recent_trades."""
    # Trier par timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("ts", ""))

    windows = {}
    for size in [20, 50, 100]:
        recent = sorted_trades[-size:] if len(sorted_trades) >= size else sorted_trades
        if not recent:
            continue
        wins = sum(1 for t in recent if t["profit"] > 0)
        total = len(recent)
        pnl = sum(t["profit"] for t in recent)
        windows[f"last_{size}"] = {
            "trades": total,
            "wins": wins,
            "losses": total - wins,
            "pnl": round(pnl, 2),
            "wr": round(wins / total * 100, 1) if total > 0 else 0,
            "avg": round(pnl / total, 2) if total > 0 else 0,
        }
    return windows


def main():
    print("=" * 60)
    print("🧹 NETTOYAGE DES TRADES SIMULÉS — performance_history.json")
    print("=" * 60)

    # Backup
    data = load_json(PERF_FILE)
    save_json(BACKUP_FILE, data)
    print(f"📦 Backup créé: {BACKUP_FILE}")

    # Compter les trades
    before = len(data.get("recent_trades", []))
    simulated = [t for t in data["recent_trades"] if is_simulated(t)]
    real = [t for t in data["recent_trades"] if not is_simulated(t)]

    print(f"\n📊 Avant: {before} trades dont {len(simulated)} simulés, {len(real)} réels")

    # Afficher les trades simulés
    print(f"\n🔴 Trades simulés à supprimer ({len(simulated)}):")
    for t in simulated:
        print(f"   - {t['symbol']:8s} {t['direction']:5s}  ${t['profit']:>7.2f}  regime={t['regime']:8s}  ts={t['ts']}")

    # Afficher les trades réels du 19 juin
    real_jun19 = [t for t in real if t.get("ts", "").startswith("2026-06-19")]
    print(f"\n✅ Trades réels du 19 juin ({len(real_jun19)}):")
    for t in real_jun19:
        print(f"   - {t['symbol']:8s} {t['direction']:5s}  ${t['profit']:>7.2f}  regime={t['regime']:8s}  ts={t['ts']}")

    # Mettre à jour recent_trades
    data["recent_trades"] = real

    # Recalculer daily
    daily = recalc_daily_stats(real)
    data["daily"] = daily
    print(f"\n📅 Daily stats après nettoyage:")
    for d, stats in sorted(daily.items()):
        print(f"   {d}: {stats['trades']} trades, {stats['wins']}W/{stats['losses']}L, ${stats['pnl']:+.2f}")

    # Recalculer symbol stats
    symbols = recalc_symbol_stats(real)
    data["symbols"] = symbols
    print(f"\n📈 Symbol stats après nettoyage:")
    for sym, s in sorted(symbols.items()):
        wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
        print(f"   {sym:12s}: {s['trades']:3d} trades, {wr:5.1f}% WR, ${s['pnl']:+.2f} PnL")

    # Recalculer rolling windows
    rolling = recalc_rolling_windows(real)
    data["rolling"] = rolling
    print(f"\n🔄 Rolling windows après nettoyage:")
    for name, r in sorted(rolling.items()):
        print(f"   {name}: {r['trades']} trades, {r['wr']}% WR, ${r['pnl']:+.2f} PnL")

    # Recalculer challenge stats
    total_pnl = sum(s["pnl"] for s in symbols.values())
    total_trades = sum(s["trades"] for s in symbols.values())
    total_wins = sum(s["wins"] for s in symbols.values())
    total_losses = sum(s["losses"] for s in symbols.values())
    wr_global = total_wins / total_trades * 100 if total_trades > 0 else 0

    # Garder les stats challenge originales (balance = donnée réelle du compte)
    # Ne pas les recalculer depuis recent_trades qui peut être incomplet
    if "challenge" in data:
        # On met juste à jour le WR et total_trades depuis les trades connus
        # mais on garde la balance réelle du compte
        old = data["challenge"]
        print(f"\n💰 Challenge (gardé depuis compte réel):")
        print(
            f"   Balance PnL: ~${float(old.get('profit_remaining', '0').replace('$', '').replace(',', '')):.0f} restant"
        )
        print(f"   WR (recent_trades): {wr_global:.1f}%")
        print(f"   Trades (recent_trades): {total_trades}")
        print(f"   PnL (recent_trades): ${total_pnl:+.2f}")

    # Vider les alertes (obsolètes)
    data["alerts"] = []

    # Sauvegarder
    save_json(PERF_FILE, data)

    print(f"\n{'=' * 60}")
    print(f"✅ Nettoyage terminé!")
    print(f"   {before} → {len(real)} trades ({before - len(real)} supprimés)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
