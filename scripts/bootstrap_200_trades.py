"""Bootstrap Monte-Carlo on last N trades to estimate probability of positive cumulative PnL."""
import csv
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
trades_path = ROOT / "runtime" / "trades_log.csv"
if not trades_path.exists():
    print("trades_log.csv missing")
    sys.exit(1)

pnl = []
with open(trades_path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            v = float(row.get('pnl') or 0.0)
        except Exception:
            v = 0.0
        pnl.append(v)

if not pnl:
    print('no pnl data')
    sys.exit(1)

N = 200
last = pnl[-N:] if len(pnl) >= N else pnl
n_samples = 10000
rng = np.random.default_rng(12345)

sums = np.empty(n_samples)
for i in range(n_samples):
    sample = rng.choice(last, size=N, replace=True)
    sums[i] = sample.sum()

prob_positive = np.mean(sums > 0)
mean = np.mean(sums)
median = np.median(sums)
ci_low, ci_high = np.percentile(sums, [2.5, 97.5])

out = ROOT / 'runtime' / 'bootstrap_200_trades_20260813.json'
import json
json.dump({
    'n_samples': n_samples,
    'N': N,
    'prob_positive': float(prob_positive),
    'mean': float(mean),
    'median': float(median),
    'ci_2.5': float(ci_low),
    'ci_97.5': float(ci_high)
}, open(out,'w'), indent=2)
print('Results written to', out)
print(f'P(cumulative>0)={prob_positive:.4f}, mean={mean:.2f}, median={median:.2f}, 95%CI=({ci_low:.2f},{ci_high:.2f})')
