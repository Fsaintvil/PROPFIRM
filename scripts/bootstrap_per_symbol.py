"""Bootstrap Monte-Carlo per-symbol on last N trades from runtime/trades_log.csv
Usage: python scripts/bootstrap_per_symbol.py XAUUSD 200
Outputs: runtime/bootstrap_{SYMBOL}_200_20260813.json
"""
import sys
from pathlib import Path
import csv
from datetime import datetime
import json
import random
import statistics

if len(sys.argv) < 3:
    print('Usage: bootstrap_per_symbol.py SYMBOL N')
    sys.exit(1)

symbol = sys.argv[1]
N = int(sys.argv[2])
ROOT = Path(__file__).resolve().parents[1]
trades_path = ROOT / 'runtime' / 'trades_log.csv'
if not trades_path.exists():
    print('trades_log.csv missing')
    sys.exit(1)

pnl = []
with open(trades_path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        if r.get('symbol')!=symbol:
            continue
        try:
            val = float(r.get('pnl') or 0)
        except Exception:
            val = 0.0
        pnl.append(val)

if not pnl:
    print('No trades for', symbol)
    sys.exit(1)

pnl = pnl[-N:]
# bootstrap
iters = 5000
results = []
for i in range(iters):
    sample = [random.choice(pnl) for _ in range(len(pnl))]
    results.append(sum(sample))

prob_pos = sum(1 for x in results if x>0)/len(results)
mean = statistics.mean(results)
median = statistics.median(results)
stdev = statistics.pstdev(results)

out = ROOT / 'runtime' / f'bootstrap_{symbol}_{N}_20260813.json'
json.dump({'symbol':symbol,'n':len(pnl),'iters':iters,'prob_pos':prob_pos,'mean':mean,'median':median,'stdev':stdev}, open(out,'w'), indent=2)

print('Wrote', out)
