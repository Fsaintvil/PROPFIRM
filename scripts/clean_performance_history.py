#!/usr/bin/env python3
"""Nettoie performance_history.json des 26 trades synthétiques contaminants.

Trades contaminants identifiés:
- EURUSD profit=50.0 regime=RAN
- XAUUSD profit=150.0 regime=RANGING
- 13 paires = 26 trades, tous avec timestamps ISO (format T)

Après nettoyage, rebuild daily/symbols/rolling depuis recent_trades clean.
"""

import json
import sys
from pathlib import Path
from collections import Counter

HISTORY_FILE = Path(__file__).parent.parent / "runtime" / "performance_history.json"
BACKUP_FILE = HISTORY_FILE.with_suffix(".json.clean_bak")


def is_synthetic(trade):
    """Détecte un trade synthétique: EURUSD $50 / XAUUSD $150 avec RAN/RANGING."""
    profit = trade.get("profit", 0)
    symbol = trade.get("symbol", "")
    regime = trade.get("regime", "")
    return (symbol == "EURUSD" and profit == 50.0 and regime == "RAN") or (
        symbol == "XAUUSD" and profit == 150.0 and regime == "RANGING"
    )


def rebuild_daily(recent_trades):
    """Reconstruit daily stats depuis recent_trades propres."""
    daily = {}
    for t in recent_trades:
        ts = t.get("ts", "")
        if "T" in ts:
            day = ts[:10]  # ISO format: 2026-06-22T...
        elif " " in ts:
            day = ts[:10]  # Space format: 2026-06-22 ...
        else:
            continue
        profit = t["profit"]
        symbol = t["symbol"]

        if day not in daily:
            daily[day] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "symbols": {},
            }
        d = daily[day]
        d["trades"] += 1
        d["pnl"] += profit
        if profit > 0:
            d["wins"] += 1
            d["gross_profit"] += profit
        elif profit < 0:
            d["losses"] += 1
            d["gross_loss"] += abs(profit)

        if symbol not in d["symbols"]:
            d["symbols"][symbol] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        sd = d["symbols"][symbol]
        sd["trades"] += 1
        sd["pnl"] += profit
        if profit > 0:
            sd["wins"] += 1
        elif profit < 0:
            sd["losses"] += 1
    return daily


def rebuild_symbols(recent_trades):
    """Reconstruit symbol stats depuis recent_trades propres."""
    symbols = {}
    for t in recent_trades:
        symbol = t["symbol"]
        profit = t["profit"]
        direction = t.get("direction", "BUY")
        regime = t.get("regime", "UNKNOWN")

        if symbol not in symbols:
            symbols[symbol] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "regime_stats": {},
                "direction_stats": {
                    "BUY": {"wins": 0, "losses": 0, "pnl": 0.0},
                    "SELL": {"wins": 0, "losses": 0, "pnl": 0.0},
                },
            }
        s = symbols[symbol]
        s["trades"] += 1
        s["pnl"] += profit
        if profit > 0:
            s["wins"] += 1
            s["gross_profit"] += profit
        elif profit < 0:
            s["losses"] += 1
            s["gross_loss"] += abs(profit)

        # Par regime
        if regime not in s["regime_stats"]:
            s["regime_stats"][regime] = {"trades": 0, "wins": 0, "pnl": 0.0}
        rs = s["regime_stats"][regime]
        rs["trades"] += 1
        rs["pnl"] += profit
        if profit > 0:
            rs["wins"] += 1

        # Par direction
        ds = s["direction_stats"].get(direction, {"wins": 0, "losses": 0, "pnl": 0.0})
        ds["pnl"] += profit
        if profit > 0:
            ds["wins"] += 1
        elif profit < 0:
            ds["losses"] += 1
        s["direction_stats"][direction] = ds

    return symbols


def rebuild_rolling(recent_trades):
    """Reconstruit rolling windows depuis recent_trades propres."""
    windows = [20, 50, 100, 200]
    rolling = {}
    n = len(recent_trades)
    for w in windows:
        if n < w:
            continue
        subset = recent_trades[-w:]
        wins = sum(1 for t in subset if t["profit"] > 0)
        losses = sum(1 for t in subset if t["profit"] <= 0)
        pnl = sum(t["profit"] for t in subset)
        total = wins + losses
        rolling[f"last_{w}"] = {
            "trades": total,
            "wins": wins,
            "losses": losses,
            "pnl": round(pnl, 2),
            "wr": round(wins / total * 100, 1) if total > 0 else 0,
            "avg": round(pnl / total, 2) if total > 0 else 0,
        }
    return rolling


def main():
    if not HISTORY_FILE.exists():
        print(f"❌ {HISTORY_FILE} n'existe pas")
        sys.exit(1)

    # Backup
    data = json.loads(HISTORY_FILE.read_text())
    HISTORY_FILE.rename(BACKUP_FILE)
    print(f"📦 Backup créé: {BACKUP_FILE}")

    # Identifier les contaminants
    recent = data.get("recent_trades", [])
    total = len(recent)
    synthetic = [t for t in recent if is_synthetic(t)]
    clean = [t for t in recent if not is_synthetic(t)]

    print(f"📊 Trades totaux: {total}")
    print(f"🔴 Trades synthétiques retirés: {len(synthetic)}")
    print(f"✅ Trades propres conservés: {len(clean)}")

    if len(synthetic) == 0:
        print("⚠️  Aucun trade synthétique trouvé — rien à nettoyer")
        return

    # Rebuild
    data["recent_trades"] = clean
    data["daily"] = rebuild_daily(clean)
    data["symbols"] = rebuild_symbols(clean)
    data["rolling"] = rebuild_rolling(clean)

    # Nettoyer les alertes dupliquées
    alert_history = data.get("alerts", [])
    # Garder seulement la dernière occurrence de chaque type d'alerte par jour
    seen = set()
    deduped = []
    for a in reversed(alert_history):
        key = (a.get("metric"), a.get("date"))
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    data["alerts"] = list(reversed(deduped))
    print(f"🔕 Alertes: {len(alert_history)} → {len(deduped)} (dédupliquées)")

    # Sauvegarder
    HISTORY_FILE.write_text(json.dumps(data, indent=2, default=str))
    print(f"✅ {HISTORY_FILE} nettoyé et sauvegardé")

    # Afficher le résumé
    print(f"\n📈 RÉSUMÉ APRÈS NETTOYAGE:")
    print(f"  Trades: {len(clean)}")
    print(f"  Symboles: {len(data['symbols'])}")
    for sym, sdata in sorted(data["symbols"].items()):
        wr = sdata["wins"] / sdata["trades"] * 100 if sdata["trades"] > 0 else 0
        pf = "∞"
        if sdata["gross_loss"] > 0:
            pf = round(sdata["gross_profit"] / sdata["gross_loss"], 2)
        print(f"    {sym:10s}: {sdata['trades']:3d} trades | WR {wr:5.1f}% | PnL ${sdata['pnl']:>+8.2f} | PF {pf}")

    for key, r in sorted(data["rolling"].items()):
        print(f"    {key}: {r['wr']}% WR | ${r['pnl']:+.0f} PnL | {r['trades']} trades")


if __name__ == "__main__":
    main()
