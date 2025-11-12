#!/usr/bin/env python3
import glob, json, os
files = sorted(glob.glob('artifacts/live_trading/mt5_apply_breakeven_*.json') + glob.glob('artifacts/live_trading/mt5_apply_breakeven_adjusted_*.json'))
if not files:
    print('No apply result files found')
    raise SystemExit(0)
latest = files[-1]
with open(latest,'r',encoding='utf-8') as f:
    d = json.load(f)
results = d.get('results', [])
counts = {}
for r in results:
    res = r.get('result')
    if isinstance(res, dict):
        code = res.get('retcode')
    else:
        code = 'error'
    counts[code] = counts.get(code,0)+1
print('file:', latest)
print('total:', len(results))
for k in sorted(counts.keys(), key=lambda x: (str(x))):
    print('retcode', k, 'count', counts[k])
# print small sample
print('\nSample failures (first 10):')
failures = [r for r in results if not (isinstance(r.get('result'), dict) and r.get('result').get('retcode') in (10009,))]
for s in failures[:10]:
    print(s.get('ticket'), s.get('symbol'), s.get('result'))
