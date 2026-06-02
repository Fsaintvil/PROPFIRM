#!/usr/bin/env python3
"""
MONITORING CONTINU - Rapport toutes les 15 minutes
Suivi de l'impact des optimisations en temps réel
"""
import csv
import json
from datetime import datetime


def read_robot_state():
    """Lire l'état du robot"""
    try:
        with open("runtime/robot_state.json") as f:
            return json.load(f)
    except Exception:
        return {}

def read_trades_log():
    """Lire les derniers trades du log CSV"""
    trades = []
    try:
        with open("runtime/trades_log.csv") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)
    except Exception:
        pass
    return trades

def analyze_trades(trades, last_n=50):
    """Analyser les trades récents"""
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0, "wr": 0,
            "avg_win": 0, "avg_loss": 0, "ratio": 0,
            "profit": 0, "shorts_wr": 0, "longs_wr": 0
        }

    recent = trades[-last_n:]
    wins = sum(1 for t in recent if float(t.get("pnl", 0)) > 0)
    losses = sum(1 for t in recent if float(t.get("pnl", 0)) < 0)
    total = len(recent)
    wr = wins / max(total, 1) * 100

    profits = [float(t.get("pnl", 0)) for t in recent]
    wins_list = [p for p in profits if p > 0]
    losses_list = [p for p in profits if p < 0]

    avg_win = sum(wins_list) / max(len(wins_list), 1)
    avg_loss = sum(losses_list) / max(len(losses_list), 1)
    ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 0

    total_profit = sum(profits)

    # Par direction
    shorts = [t for t in recent if t.get("direction", "").lower() == "sell"]
    longs = [t for t in recent if t.get("direction", "").lower() == "buy"]

    shorts_wr = sum(1 for t in shorts if float(t.get("pnl", 0)) > 0) / max(len(shorts), 1) * 100
    longs_wr = sum(1 for t in longs if float(t.get("pnl", 0)) > 0) / max(len(longs), 1) * 100

    return {
        "total": total, "wins": wins, "losses": losses, "wr": wr,
        "avg_win": avg_win, "avg_loss": avg_loss, "ratio": ratio,
        "profit": total_profit, "shorts_wr": shorts_wr, "longs_wr": longs_wr,
        "shorts_count": len(shorts), "longs_count": len(longs)
    }

def print_report(cycle_num):
    """Afficher un rapport complet"""
    state = read_robot_state()
    trades = read_trades_log()
    recent_30 = analyze_trades(trades, 30)
    recent_50 = analyze_trades(trades, 50)
    all_time = analyze_trades(trades)

    print("\n" + "="*120)
    print(f"RAPPORT OPTIMISATION - Cycle {cycle_num} | {datetime.now().strftime('%H:%M:%S')}")
    print("="*120)

    # État du compte
    peak_eq = state.get("peak_equity", 0)
    cons_losses = state.get("consecutive_losses", 0)

    print(f"\n📊 COMPTE (Peak Equity: ${peak_eq:,.0f}):")
    print(f"   Pertes consécutives: {cons_losses}/3 (avant pause)")

    # Récents 30 trades
    print("\n📈 DERNIERS 30 TRADES:")
    print(f"   Total: {recent_30['total']} | Wins: {recent_30['wins']} | Losses: {recent_30['losses']}")
    print(f"   Win Rate: {recent_30['wr']:.1f}%")
    print(f"   Avg Win: ${recent_30['avg_win']:+.2f} | Avg Loss: ${recent_30['avg_loss']:+.2f}")
    print(f"   Ratio (cible 2.0): {recent_30['ratio']:.2f} {'✅' if recent_30['ratio'] >= 1.5 else '❌'}")
    print(f"   Profit 30T: ${recent_30['profit']:+.2f}")
    print(f"   Shorts: {recent_30['shorts_wr']:.0f}% ({recent_30['shorts_count']}) "
          f"| Longs: {recent_30['longs_wr']:.0f}% ({recent_30['longs_count']})")

    # Récents 50 trades
    print("\n📊 DERNIERS 50 TRADES:")
    print(f"   Total: {recent_50['total']} | Wins: {recent_50['wins']} | Losses: {recent_50['losses']}")
    print(f"   Win Rate: {recent_50['wr']:.1f}%")
    print(f"   Avg Win: ${recent_50['avg_win']:+.2f} | Avg Loss: ${recent_50['avg_loss']:+.2f}")
    print(f"   Ratio (cible 2.0): {recent_50['ratio']:.2f} {'✅' if recent_50['ratio'] >= 1.5 else '❌'}")
    print(f"   Profit 50T: ${recent_50['profit']:+.2f}")

    # Tous les trades
    print(f"\n📋 TOUS LES TRADES ({all_time['total']} total):")
    print(f"   Win Rate: {all_time['wr']:.1f}%")
    print(f"   Avg Win: ${all_time['avg_win']:+.2f} | Avg Loss: ${all_time['avg_loss']:+.2f}")
    print(f"   Ratio: {all_time['ratio']:.2f}")
    print(f"   Profit net: ${all_time['profit']:+.2f}")

    print("\n" + "="*120)

if __name__ == "__main__":
    import sys
    cycle = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print_report(cycle)
