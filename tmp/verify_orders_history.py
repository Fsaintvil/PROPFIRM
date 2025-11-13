"""verify_orders_history.py

Lire le CSV de conformité, interroger MT5 history_orders_get pour chaque order_id
et produire un JSON de vérification listant pour chaque order_id si trouvé, et les détails.

Usage: python tmp/verify_orders_history.py
"""
import csv
import json
import os
import time
from datetime import datetime, timedelta
import argparse

import MetaTrader5 as mt5

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CSV_PATH = os.path.join(ROOT, 'artifacts', 'live_trading', 'compliance_orders_20251112T130009Z.csv')
OUT_DIR = os.path.join(ROOT, 'artifacts', 'live_trading')

# Time window padding (seconds) around CSV timestamps when querying history
PADDING_SECONDS = 60 * 5  # 5 minutes (default)


def parse_iso(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def main():
    parser = argparse.ArgumentParser(description='Vérifier order_ids dans l\'historique MT5')
    parser.add_argument('--csv', type=str, default=CSV_PATH, help='Chemin vers le CSV de conformité')
    parser.add_argument('--padding-seconds', type=int, default=PADDING_SECONDS, help='Padding autour du timestamp CSV (secondes)')
    parser.add_argument('--full-history', action='store_true', help='Scanner l\'ensemble de l\'historique (from=0 -> now)')
    parser.add_argument('--days-back', type=int, default=None, help='Scanner les N derniers jours (alternative à --padding-seconds)')
    args = parser.parse_args()

    csv_path = args.csv
    padding_seconds = args.padding_seconds
    full_history = args.full_history
    days_back = args.days_back
    orders = []
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            orders.append({
                'ticket': r['ticket'],
                'order_id': int(r['order_id']) if r['order_id'] else None,
                'retcode': r.get('retcode'),
                'time': r.get('time'),
                'status': r.get('status'),
                'comment': r.get('comment'),
            })

    if not orders:
        print('No orders in CSV')
        return

    # initialize MT5
    if not mt5.initialize():
        raise SystemExit('mt5.initialize() failed')

    results = []
    for o in orders:
        entry = {'ticket': o['ticket'], 'order_id': o['order_id'], 'found': False, 'matches': [], 'error': None}
        if o['order_id'] is None:
            entry['error'] = 'no order_id in CSV'
            results.append(entry)
            continue

        # build time window
        if full_history:
            time_from = 0
            time_to = int(datetime.utcnow().timestamp())
        elif days_back is not None:
            # scan N days before the CSV timestamp
            try:
                t = parse_iso(o['time'])
            except Exception:
                t = datetime.utcnow()
            time_from = int((t - timedelta(days=days_back)).timestamp())
            time_to = int((t + timedelta(days=days_back)).timestamp())
        else:
            try:
                t = parse_iso(o['time'])
            except Exception:
                # fallback to now
                t = datetime.utcnow()
            time_from = int((t - timedelta(seconds=padding_seconds)).timestamp())
            time_to = int((t + timedelta(seconds=padding_seconds)).timestamp())

        # query history orders
        try:
            history = mt5.history_orders_get(time_from, time_to)
        except Exception as e:
            entry['error'] = f'exception during history_orders_get: {e}'
            results.append(entry)
            continue

        if history is None:
            entry['found'] = False
            entry['matches'] = []
            results.append(entry)
            continue

        # history is a list of order objects; convert and filter
        for ho in history:
            try:
                oid = int(ho.ticket) if hasattr(ho, 'ticket') else int(ho.order)
            except Exception:
                # try attribute 'order'
                try:
                    oid = int(ho.order)
                except Exception:
                    oid = None
            if oid == o['order_id']:
                entry['found'] = True
                # capture useful fields
                info = {
                    'order': getattr(ho, 'order', None),
                    'ticket': getattr(ho, 'ticket', None),
                    'symbol': getattr(ho, 'symbol', None),
                    'type': getattr(ho, 'type', None),
                    'price_open': getattr(ho, 'price_open', None),
                    'volume_initial': getattr(ho, 'volume_initial', None),
                    'time_setup': datetime.utcfromtimestamp(getattr(ho, 'time_setup', 0)).isoformat() + 'Z' if getattr(ho, 'time_setup', None) else None,
                    'time_done': datetime.utcfromtimestamp(getattr(ho, 'time_done', 0)).isoformat() + 'Z' if getattr(ho, 'time_done', None) else None,
                    'reason': getattr(ho, 'reason', None),
                    'comment': getattr(ho, 'comment', None),
                }
                entry['matches'].append(info)

        results.append(entry)

    mt5.shutdown()

    out_name = 'orders_history_verification'
    if full_history:
        out_name += '_fullhistory'
    elif days_back is not None:
        out_name += f'_days{days_back}'
    else:
        out_name += f'_pad{padding_seconds}s'

    out_path = os.path.join(OUT_DIR, f'{out_name}_{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'generated': datetime.utcnow().isoformat() + 'Z', 'csv_path': CSV_PATH, 'results': results}, f, indent=2, ensure_ascii=False)

    print('Wrote', out_path)


if __name__ == '__main__':
    main()
