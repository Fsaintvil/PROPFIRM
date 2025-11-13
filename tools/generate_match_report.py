#!/usr/bin/env python3
"""Génère un rapport CSV/JSON rapprochant les tickets locaux et le broker.
Ecrit `match_report_YYYYmmddTHHMMSSZ.csv` et `.json` dans artifacts/live_trading/.
"""
from pathlib import Path
import json
import csv
from datetime import datetime
import sys

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / 'artifacts' / 'live_trading'
ART.mkdir(parents=True, exist_ok=True)


def find_latest_broker_json():
    files = sorted(ART.glob('broker_history_direct_*.json'))
    if not files:
        files = sorted(ART.glob('broker_history_5d_*.json'))
    return files[-1] if files else None


def load_broker_tickets(broker_path: Path):
    j = json.loads(broker_path.read_text(encoding='utf-8'))
    tickets = set()
    orders = j.get('orders', []) or []
    deals = j.get('deals', []) or []
    for o in orders:
        t = o.get('ticket') or o.get('order')
        if t:
            try:
                tickets.add(int(t))
            except Exception:
                pass
    for d in deals:
        t = d.get('order') or d.get('ticket') or d.get('deal')
        if t:
            try:
                tickets.add(int(t))
            except Exception:
                pass
    return tickets, j


def collect_local_tickets():
    patterns = ['orders_audit_*.json', 'orders_audit_enriched_*.json']
    local = {}
    for pat in patterns:
        for p in ART.glob(pat):
            try:
                j = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            # try multiple shapes
            tickets = set()
            if 'tickets' in j and isinstance(j['tickets'], list):
                for t in j['tickets']:
                    try:
                        tickets.add(int(t))
                    except Exception:
                        pass
            if 'entries' in j and isinstance(j['entries'], list):
                for e in j['entries']:
                    if isinstance(e, dict):
                        for k in ('ticket','order'):
                            if k in e and e[k]:
                                try:
                                    tickets.add(int(e[k]))
                                except Exception:
                                    pass
            # if 'orders' array with {ticket,found}
            if 'orders' in j and isinstance(j['orders'], list):
                for o in j['orders']:
                    if isinstance(o, dict) and o.get('ticket'):
                        try:
                            tickets.add(int(o.get('ticket')))
                        except Exception:
                            pass
            # fallback: found_ids
            if 'found_ids' in j and isinstance(j['found_ids'], list):
                for t in j['found_ids']:
                    try:
                        tickets.add(int(t))
                    except Exception:
                        pass

            for t in tickets:
                rec = local.get(t, {'files': set(), 'count': 0})
                rec['files'].add(str(p.name))
                rec['count'] += 1
                local[t] = rec
    return local


def main():
    broker_path = find_latest_broker_json()
    if not broker_path:
        print('No broker history JSON found in', ART)
        sys.exit(1)

    broker_tickets, broker_json = load_broker_tickets(broker_path)

    local = collect_local_tickets()

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    csv_path = ART / f'match_report_{ts}.csv'
    json_path = ART / f'match_report_{ts}.json'

    # Merge keys: all local tickets plus broker tickets
    all_tickets = set(local.keys()) | set(broker_tickets)

    summary = {'total_local_tickets': len(local), 'total_broker_tickets': len(broker_tickets), 'checked': len(all_tickets), 'matched': 0, 'missing_local':0, 'missing_broker':0}

    with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
        writer = csv.writer(cf)
        writer.writerow(['ticket','in_local','local_files','local_count','in_broker','sample_broker_entry'])
        for t in sorted(all_tickets):
            in_local = t in local
            in_broker = t in broker_tickets
            if in_local and in_broker:
                summary['matched'] += 1
            if in_local and not in_broker:
                summary['missing_broker'] += 1
            if in_broker and not in_local:
                summary['missing_local'] += 1

            local_files = ';'.join(sorted(local[t]['files'])) if in_local else ''
            local_count = local[t]['count'] if in_local else 0

            # sample broker entry
            sample = ''
            if in_broker:
                # find an order object with that ticket
                for o in broker_json.get('orders', []) or []:
                    try:
                        if int(o.get('ticket') or o.get('order')) == t:
                            sample = f"{o.get('symbol')}@{o.get('price_open') or o.get('price_current')}"
                            break
                    except Exception:
                        continue

            writer.writerow([t, 'Y' if in_local else 'N', local_files, local_count, 'Y' if in_broker else 'N', sample])

    # write JSON summary
    out = {'csv': str(csv_path), 'broker_json': str(broker_path), 'summary': summary}
    json_path.write_text(json.dumps(out, indent=2), encoding='utf-8')

    print(json.dumps({'status':'done','csv': str(csv_path), 'json': str(json_path), 'summary': summary}))


if __name__ == '__main__':
    main()
