#!/usr/bin/env python3
"""Correlate retcode==10016 entries from recent_live_orders_summary_*.json
and produce a CSV/JSON report with source file, timestamp, symbol, retcode, order_id, and nearby launcher/log timestamps.
Writes: logs/retcode_correlation_<ts>.json
"""
import json
from pathlib import Path
from datetime import datetime
import glob

LOGS = Path('logs')
matches = sorted(LOGS.glob('recent_live_orders_summary_*.json'))
if not matches:
    print('NO_SUMMARY')
    raise SystemExit(2)

p = matches[-1]
js = json.load(open(p,'r',encoding='utf-8'))
orders = js.get('orders', [])

matches_rc = [o for o in orders if o.get('retcode') == 10016]

report = {
    'generated_at': datetime.utcnow().isoformat() + 'Z',
    'summary_file': str(p),
    'retcode': 10016,
    'count': len(matches_rc),
    'entries': []
}

# For each entry, try to find nearest launcher log by filename pattern and include mtime
for o in matches_rc:
    entry = {
        'symbol': o.get('symbol'),
        'retcode': o.get('retcode'),
        'order_id': o.get('order_id'),
        'executed': o.get('executed'),
        '_source_file': o.get('_source_file'),
        '_file_mtime': o.get('_file_mtime')
    }
    # try to read the source file content snippet
    src = o.get('_source_file')
    if src:
        sf = Path(src)
        if not sf.exists():
            # try relative to logs/
            sf = LOGS / Path(src).name
        if sf.exists():
            try:
                txt = sf.read_text(encoding='utf-8', errors='ignore')
                # capture first and last 5 lines
                lines = txt.strip().splitlines()
                entry['source_head'] = '\n'.join(lines[:5])
                entry['source_tail'] = '\n'.join(lines[-5:])
            except Exception:
                entry['source_head'] = None
                entry['source_tail'] = None
    report['entries'].append(entry)

OUT = LOGS / f'retcode_correlation_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json'
OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
print('WROTE', OUT)
print('COUNT=', report['count'])
