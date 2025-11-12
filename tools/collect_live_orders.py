import re
import json
import csv
from datetime import datetime, timedelta
import os

import MetaTrader5 as mt5

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOG_PATH = os.path.join(ROOT, 'artifacts', 'live_trading', 'production_run_latest.log')
OUT_DIR = os.path.join(ROOT, 'artifacts', 'live_trading')

regex_order = re.compile(r'Ordre envoy[ée]: .*order=(\d+)', re.IGNORECASE)
regex_executed = re.compile(r'Ordre exécuté: .* (?:order=)?(\d+)?', re.IGNORECASE)

found = set()
if os.path.exists(LOG_PATH):
    with open(LOG_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            for m in regex_order.finditer(line):
                found.add(int(m.group(1)))
            for m in regex_executed.finditer(line):
                g = m.group(1)
                if g and g.isdigit():
                    found.add(int(g))
else:
    print(json.dumps({'error': 'log not found', 'path': LOG_PATH}))
    raise SystemExit(1)

# If none found, expand search to other log files for recent sessions
if not found:
    # scan session files for 'Ordre envoy' pattern
    dirp = os.path.join(ROOT, 'artifacts', 'live_trading')
    for fn in os.listdir(dirp):
        if fn.startswith('session_') and fn.endswith('.json'):
            p = os.path.join(dirp, fn)
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    txt = f.read()
            except:
                continue
            for m in regex_order.finditer(txt):
                found.add(int(m.group(1)))

now = datetime.utcnow()
result = {'timestamp': now.isoformat(), 'found_ids': sorted(list(found)), 'orders': [], 'errors': []}

if not mt5.initialize():
    result['errors'].append('mt5.initialize failed')
    print(json.dumps(result, indent=2))
else:
    # Query history orders in a reasonable window (last 48h)
    frm = now - timedelta(hours=48)
    to = now + timedelta(minutes=5)
    orders = mt5.history_orders_get(frm, to)
    if orders is None:
        result['errors'].append('mt5.history_orders_get returned None')
    else:
        # convert to dicts and filter by ticket
        for o in orders:
            try:
                od = o._asdict()
            except Exception:
                # fallback: convert attributes
                od = {k: getattr(o, k) for k in dir(o) if not k.startswith('_')}
            ticket = od.get('ticket') or od.get('order') or od.get('ticket_id')
            if ticket and ticket in found:
                # map fields
                entry = {
                    'ticket': ticket,
                    'symbol': od.get('symbol'),
                    'type': od.get('type'),
                    'volume': od.get('volume_initial') or od.get('volume'),
                    'price_open': od.get('price_open') or od.get('price'),
                    'time_setup': od.get('time_setup') or od.get('time'),
                    'state': od.get('state'),
                    'comment': od.get('comment'),
                }
                # try to find retcode/result if available
                if 'result' in od and od['result'] is not None:
                    try:
                        entry['retcode'] = od['result']['retcode']
                    except Exception:
                        entry['retcode'] = None
                result['orders'].append(entry)
    mt5.shutdown()

# For any found IDs not in orders, try to query current positions and trades
if found and not result['orders']:
    if mt5.initialize():
        positions = mt5.positions_get()
        if positions:
            for p in positions:
                pd = p._asdict()
                ticket = pd.get('ticket')
                if ticket and ticket in found:
                    entry = {
                        'ticket': ticket,
                        'symbol': pd.get('symbol'),
                        'type': pd.get('type'),
                        'volume': pd.get('volume'),
                        'price_open': pd.get('price_open'),
                        'time': pd.get('time'),
                    }
                    result['orders'].append(entry)
        mt5.shutdown()

# Write output files
ts = now.strftime('%Y%m%dT%H%M%SZ')
json_path = os.path.join(OUT_DIR, f'orders_audit_{ts}.json')
csv_path = os.path.join(OUT_DIR, f'orders_audit_{ts}.csv')
with open(json_path, 'w', encoding='utf-8') as jf:
    json.dump(result, jf, indent=2, default=str)

# write CSV if orders exist
if result['orders']:
    with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
        writer = csv.DictWriter(cf, fieldnames=['ticket','symbol','type','volume','price_open','time_setup','state','retcode','comment'])
        writer.writeheader()
        for o in result['orders']:
            writer.writerow({k: o.get(k) for k in writer.fieldnames})

print(json.dumps({'status':'done','json': json_path, 'csv': csv_path, 'summary': {'found': len(found), 'matched_orders': len(result['orders'])}}))
