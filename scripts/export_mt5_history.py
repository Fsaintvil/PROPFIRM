"""
Export MT5 History — Exporte l'historique des deals MT5 vers un CSV propre.

Fix 30 Aout 2026: L'API MT5 ne retient que ~20 jours d'historique.
Ce script exporte les deals avant qu'ils ne soient tronqués.
Le CSV devient la source de vérité pour l'historique long.

Usage:
    python scripts/export_mt5_history.py [--output runtime/mt5_history.csv] [--days 30]
"""

import csv
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def export_mt5_history(output_path: str = "runtime/mt5_history.csv", days: int = 30):
    """Exporte l'historique MT5 vers un CSV propre."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MetaTrader5 non installé. pip install MetaTrader5")
        return False

    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        return False

    info = mt5.account_info()
    if info:
        print(f"Account: {info.login}")
        print(f"Balance: {info.balance:.2f}")
        print(f"Server: {info.server}")
    else:
        print("account_info() failed:", mt5.last_error())
        return False

    # Récupérer les deals
    from_date = datetime.now() - timedelta(days=days)
    to_date = datetime.now()

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        print("history_deals_get failed:", mt5.last_error())
        return False

    print(f"\nDeals récupérés: {len(deals)}")
    print(f"Période: {from_date.strftime('%Y-%m-%d')} → {to_date.strftime('%Y-%m-%d')}")

    # Grouper par position
    positions = defaultdict(list)
    for d in deals:
        positions[d.position_id].append(d)

    # Construire les positions fermées
    closed = []
    for pos_id, deal_list in positions.items():
        entries = [d for d in deal_list if d.entry == mt5.DEAL_ENTRY_IN]
        exits = [d for d in deal_list if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]

        if not entries or not exits:
            continue

        e = entries[0]
        x = exits[0]
        direction = 'BUY' if e.type == mt5.DEAL_TYPE_BUY else 'SELL'
        pnl = sum(d.profit for d in deal_list)
        swaps = sum(d.swap for d in deal_list)
        comm = sum(d.commission for d in deal_list)
        net = pnl + swaps + comm
        duration_h = (datetime.fromtimestamp(x.time) - datetime.fromtimestamp(e.time)).total_seconds() / 3600

        closed.append({
            'position_id': pos_id,
            'symbol': e.symbol,
            'direction': direction,
            'volume': e.volume,
            'entry_price': e.price,
            'entry_time': datetime.fromtimestamp(e.time).strftime('%Y-%m-%d %H:%M:%S'),
            'exit_price': x.price,
            'exit_time': datetime.fromtimestamp(x.time).strftime('%Y-%m-%d %H:%M:%S'),
            'pnl': round(pnl, 2),
            'swaps': round(swaps, 2),
            'commission': round(comm, 2),
            'net': round(net, 2),
            'duration_h': round(duration_h, 2),
            'magic': e.magic,
            'comment': e.comment,
        })

    closed.sort(key=lambda t: t['entry_time'])

    # Écrire le CSV
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    fieldnames = [
        'position_id', 'symbol', 'direction', 'volume',
        'entry_price', 'entry_time', 'exit_price', 'exit_time',
        'pnl', 'swaps', 'commission', 'net', 'duration_h',
        'magic', 'comment',
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(closed)

    print(f"\nExport terminé: {len(closed)} positions → {output_path}")

    # Stats rapides
    if closed:
        total_net = sum(t['net'] for t in closed)
        wins = sum(1 for t in closed if t['net'] > 0)
        wr = wins / len(closed) * 100
        print(f"WR: {wr:.1f}% ({wins}/{len(closed)})")
        print(f"Net PnL: ${total_net:+,.2f}")

        # Par symbole
        by_sym = defaultdict(lambda: {'count': 0, 'net': 0.0, 'wins': 0})
        for t in closed:
            s = t['symbol']
            by_sym[s]['count'] += 1
            by_sym[s]['net'] += t['net']
            if t['net'] > 0:
                by_sym[s]['wins'] += 1
        print("\nPar symbole:")
        for s in sorted(by_sym, key=lambda x: by_sym[x]['net'], reverse=True):
            d = by_sym[s]
            swr = d['wins'] / d['count'] * 100 if d['count'] else 0
            print(f"  {s:12s}: {d['count']:3d}T WR {swr:5.1f}% Net ${d['net']:+8.2f}")

    mt5.shutdown()
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export MT5 history to CSV")
    parser.add_argument("--output", default="runtime/mt5_history.csv", help="Output CSV path")
    parser.add_argument("--days", type=int, default=30, help="Days of history to export")
    args = parser.parse_args()

    success = export_mt5_history(args.output, args.days)
    sys.exit(0 if success else 1)
