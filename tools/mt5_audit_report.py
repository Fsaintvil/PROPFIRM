#!/usr/bin/env python3
"""Generate a short MT5 audit report from recent_live_orders_summary_*.json
Writes: logs/mt5_audit_report_<ts>.json and prints a short summary to stdout.
"""
import json
from pathlib import Path
from datetime import datetime
import glob

LOGS = Path('logs')
matches = sorted(LOGS.glob('recent_live_orders_summary_*.json'))
if not matches:
    print('NO_SUMMARY_FOUND')
    raise SystemExit(2)

p = matches[-1]
js = json.load(open(p,'r',encoding='utf-8'))
orders = js.get('orders', [])

stats = {}
stats['summary_file'] = str(p)
stats['generated_at'] = datetime.utcnow().isoformat() + 'Z'
stats['total_orders_found'] = len(orders)

retcode_count = 0
executed_count = 0
orderid_count = 0
sources = {}
problem_samples = []

for o in orders:
    if o.get('retcode') is not None:
        retcode_count += 1
        problem_samples.append({'type':'retcode','obj':o})
    if o.get('executed'):
        executed_count += 1
        problem_samples.append({'type':'executed','obj':o})
    if o.get('order_id') and int(o.get('order_id'))>0:
        orderid_count += 1
        problem_samples.append({'type':'order_id','obj':o})
    src = o.get('_source_file','unknown')
    sources[src] = sources.get(src,0) + 1

stats['retcode_count'] = retcode_count
stats['executed_count'] = executed_count
stats['order_id_positive_count'] = orderid_count

# top sources
stats['top_sources'] = sorted(list(sources.items()), key=lambda x:-x[1])[:12]
# include up to 10 sample problem entries (trim object fields)
trimmed = []
for s in problem_samples[:10]:
    o = s['obj']
    trimmed.append({'type': s['type'], 'symbol': o.get('symbol'), 'retcode': o.get('retcode'), 'order_id': o.get('order_id'), 'executed': o.get('executed'), '_source_file': o.get('_source_file')})
stats['problem_samples'] = trimmed

OUT = LOGS / f'mt5_audit_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json'
OUT.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding='utf-8')
print('WROTE', OUT)
print('TOTAL_ORDERS=', stats['total_orders_found'])
print('RETCODE_COUNT=', stats['retcode_count'])
print('EXECUTED_COUNT=', stats['executed_count'])
print('ORDER_ID_POS_COUNT=', stats['order_id_positive_count'])
print('TOP_SOURCES_COUNT=', len(stats['top_sources']))
if stats['problem_samples']:
    print('SAMPLES:')
    for s in stats['problem_samples']:
        print(' ', s)
else:
    print('NO problem samples found')

