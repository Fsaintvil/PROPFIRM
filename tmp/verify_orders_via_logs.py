"""verify_orders_via_logs.py

Lire le CSV de conformité et rechercher chaque order_id/ticket dans les logs localement
(utilisé comme fallback si MT5 API n'est pas accessible). Produit un JSON de résultats.
"""
import csv
import json
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CSV_PATH = os.path.join(ROOT, 'artifacts', 'live_trading', 'compliance_orders_20251112T130009Z.csv')
LOG_DIR = os.path.join(ROOT, 'logs')
OUT_DIR = os.path.join(ROOT, 'artifacts', 'live_trading')

LOGFILES = [
    os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR)
    if os.path.isfile(os.path.join(LOG_DIR, f)) and ('live_trading' in f or 'launcher' in f or 'trading_engine' in f)
]


def read_orders(csv_path):
    orders = []
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            orders.append({
                'ticket': r.get('ticket'),
                'order_id': r.get('order_id'),
                'time': r.get('time'),
                'retcode': r.get('retcode'),
            })
    return orders


def search_logs_for_term(term):
    matches = []
    for path in LOGFILES:
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f, start=1):
                    if term in line:
                        matches.append({'file': os.path.basename(path), 'line_no': i, 'line': line.strip()})
        except Exception:
            continue
    return matches


def main():
    orders = read_orders(CSV_PATH)
    results = []

    for o in orders:
        term_ticket = str(o.get('ticket') or '')
        term_order = str(o.get('order_id') or '')
        entry = {'ticket': term_ticket, 'order_id': term_order, 'found_in_logs': False, 'matches': []}
        # search by ticket and order_id
        for term in [term_ticket, term_order]:
            if not term:
                continue
            m = search_logs_for_term(term)
            if m:
                entry['found_in_logs'] = True
                entry['matches'].extend(m)
        results.append(entry)

    out_path = os.path.join(OUT_DIR, f'orders_history_verification_logs_{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'generated': datetime.utcnow().isoformat() + 'Z', 'csv_path': CSV_PATH, 'log_files': [os.path.basename(p) for p in LOGFILES], 'results': results}, f, indent=2, ensure_ascii=False)
    print('Wrote', out_path)


if __name__ == '__main__':
    main()
