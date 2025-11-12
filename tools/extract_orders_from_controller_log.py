import re
import json
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
log = os.path.join(ROOT, 'artifacts', 'live_trading', 'live_run_controller.log')
out = os.path.join(ROOT, 'artifacts', 'live_trading', f'tmp_orders_for_enrich_{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json')

if not os.path.exists(log):
    print(json.dumps({'status':'no_log', 'log': log}))
    raise SystemExit(1)

text = open(log, 'r', encoding='utf-8', errors='replace').read()
orders = set()
# find patterns like order=345147130 or order=345160735
for m in re.finditer(r'order=(\d+)', text):
    orders.add(int(m.group(1)))

if not orders:
    print(json.dumps({'status':'no_orders_found'}))
    raise SystemExit(0)

entries = [{'ticket': o} for o in sorted(orders)]
payload = {'entries': entries}
with open(out, 'w', encoding='utf-8') as f:
    json.dump(payload, f, indent=2)

print(json.dumps({'status':'done', 'out': out, 'count': len(entries)}))
