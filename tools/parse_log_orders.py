import re
import os
import csv
import json
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOG = os.path.join(ROOT, 'artifacts', 'live_trading', 'production_run_latest.log')
OUT = os.path.join(ROOT, 'artifacts', 'live_trading')

# We'll fallback to simple patterns to handle encoding artifacts in the log
regex_executed = re.compile(r"Ordre exécut", re.IGNORECASE)
regex_orderid = re.compile(r"order=(\d+)")
regex_action_symbol = re.compile(r"\b(buy|sell)\b\s+([A-Z0-9\._$]+)", re.IGNORECASE)
regex_lot = re.compile(r"lot=?s?\s*=?\s*([0-9\.]+)", re.IGNORECASE)
regex_price = re.compile(r"price=?\s*([0-9\.,\-]+)", re.IGNORECASE)

entries = []
if not os.path.exists(LOG):
    print(json.dumps({'error': 'log missing', 'path': LOG}))
    raise SystemExit(1)

with open(LOG, 'r', encoding='utf-8', errors='ignore') as f:
    for i, line in enumerate(f, 1):
        raw = line.strip()
        # if the line contains an order id, parse ticket, symbol, lot and price
        m_id = regex_orderid.search(line)
        if m_id:
            ticket = int(m_id.group(1))
            m_as = regex_action_symbol.search(line)
            action = None
            symbol = None
            if m_as:
                action = m_as.group(1)
                symbol = m_as.group(2)
            m_lot = regex_lot.search(line)
            lots = float(m_lot.group(1)) if m_lot else None
            m_price = regex_price.search(line)
            price = None
            if m_price:
                price = float(m_price.group(1).replace(',', ''))
            entries.append({
                'line': i,
                'type': 'sent',
                'ticket': ticket,
                'action': action,
                'symbol': symbol,
                'lots': lots,
                'price': price,
                'raw': raw
            })
            continue
        # fallback: executed orders without ticket
        if regex_executed.search(line):
            m_as = regex_action_symbol.search(line)
            action = m_as.group(1) if m_as else None
            symbol = m_as.group(2) if m_as else None
            m_lot = regex_lot.search(line)
            lots = float(m_lot.group(1)) if m_lot else None
            # price in executed lines often after 'à '
            m_price = re.search(r'\bà\s*([0-9\.,\-]+)', line)
            price = float(m_price.group(1).replace(',', '')) if m_price else None
            entries.append({
                'line': i,
                'type': 'executed',
                'ticket': None,
                'action': action,
                'symbol': symbol,
                'lots': lots,
                'price': price,
                'raw': raw
            })
            continue

# Summarize
now = datetime.utcnow()
ts = now.strftime('%Y%m%dT%H%M%SZ')
json_path = os.path.join(OUT, f'orders_audit_{ts}.json')
csv_path = os.path.join(OUT, f'orders_audit_{ts}.csv')

out = {'timestamp': now.isoformat(), 'log_path': LOG, 'count': len(entries), 'entries': entries}
with open(json_path, 'w', encoding='utf-8') as jf:
    json.dump(out, jf, indent=2, default=str)

if entries:
    # write CSV with normalized columns
    keys = ['line','type','ticket','action','symbol','lots','price','raw']
    with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
        writer = csv.DictWriter(cf, fieldnames=keys)
        writer.writeheader()
        for e in entries:
            writer.writerow({k: e.get(k) for k in keys})

print(json.dumps({'status':'ok','json': json_path, 'csv': csv_path, 'count': len(entries)}))
