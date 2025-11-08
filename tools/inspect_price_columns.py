#!/usr/bin/env python3
"""Inspect price-like columns and report statistics to help pick a safe price column."""
import sys, os, json
import pandas as pd

IN = "data/features_sample.csv"
try:
    df = pd.read_csv(IN)
except Exception as e:
    print("Failed to open:", IN, e)
    raise

cols = df.columns.tolist()
info = []
for c in cols:
    try:
        s = pd.to_numeric(df[c], errors='coerce')
        n = len(s.dropna())
        if n == 0:
            continue
        pos = (s > 0).sum() / n
        med = float(s.median()) if n>0 else None
        mins = float(s.min()) if n>0 else None
        maxs = float(s.max()) if n>0 else None
        info.append({'col': c, 'n_numeric': int(n), 'positive_rate': float(pos), 'median': med, 'min': mins, 'max': maxs})
    except Exception:
        continue

out = 'artifacts/diagnostics/price_column_candidates.json'
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, 'w') as f:
    json.dump(info, f, indent=2)

print('Wrote', out)
print('Top candidates (positive_rate desc):')
for x in sorted(info, key=lambda r: r['positive_rate'], reverse=True)[:20]:
    print(x)
