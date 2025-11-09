# Merged preview for prefix: mt5
# Generated from 2 files

################################################################################
# FROM: scripts\ops\mt5_audit_and_archive.py
################################################################################
#!/usr/bin/env python3
"""Collect a per-symbol MT5 audit and archive it to artifacts/reports.

Usage:
  python scripts/ops/mt5_audit_and_archive.py [--label pre|post]

Produces: artifacts/reports/mt5_audit_<ts>_<label>.csv
"""
from pathlib import Path
import json
import sys
from datetime import datetime

try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
SYMBOLS_FILE = ROOT / 'config' / 'symbols_live.json'
OUT_DIR = ROOT / 'artifacts' / 'reports'

def load_symbols():
    try:
        return json.loads(SYMBOLS_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        print('ERROR reading symbols:', e)
        return []

def audit(symbols):
    if not mt5.initialize():
        print('ERROR: mt5.initialize() failed')
        return None

    rows = []
    total_pos = 0
    total_pending = 0
    for s in symbols:
        pos = mt5.positions_get(symbol=s)
        pending = mt5.orders_get(symbol=s)
        npos = len(pos) if pos is not None else 0
        npend = len(pending) if pending is not None else 0
        rows.append((s, npos, npend))
        total_pos += npos
        total_pending += npend

    mt5.shutdown()
    return rows, (total_pos, total_pending)

def write_csv(rows, totals, label):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    label = label or 'audit'
    out = OUT_DIR / f'mt5_audit_{ts}_{label}.csv'
    with out.open('w', encoding='utf-8') as f:
        f.write('symbol,positions,pending_orders\n')
        for s, p, o in rows:
            f.write(f'{s},{p},{o}\n')
        f.write(f'TOTALS,{totals[0]},{totals[1]}\n')
    return out

def main():
    label = None
    if len(sys.argv) > 1:
        if sys.argv[1].startswith('--label'):
            parts = sys.argv[1].split('=')
            if len(parts) == 2:
                label = parts[1]
        else:
            label = sys.argv[1]

    symbols = load_symbols()
    if not symbols:
        print('No symbols found, aborting')
        return 3

    result = audit(symbols)
    if result is None:
        print('Audit failed')
        return 4

    rows, totals = result
    out = write_csv(rows, totals, label)
    print('WROTE', out)
    return 0

if __name__ == '__main__':
    rc = main()
    sys.exit(rc)


################################################################################
# FROM: tools\mt5_audit_report.py
################################################################################
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



# End of merged preview
