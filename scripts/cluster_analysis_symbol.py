"""Analyse de clusters de pertes/gains pour un symbole donné à partir de runtime/trades_log.csv
Usage: python scripts/cluster_analysis_symbol.py XAUUSD
Sortie: runtime/cluster_{SYMBOL}_20260813.json et .md
"""
import sys
from pathlib import Path
import csv
from datetime import datetime
from collections import defaultdict, Counter
import json

if len(sys.argv) < 2:
    print("Usage: cluster_analysis_symbol.py SYMBOL")
    sys.exit(1)

symbol = sys.argv[1]
ROOT = Path(__file__).resolve().parents[1]
trades_path = ROOT / 'runtime' / 'trades_log.csv'
if not trades_path.exists():
    print('trades_log.csv missing')
    sys.exit(1)

rows = []
with open(trades_path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        if r.get('symbol') != symbol:
            continue
        try:
            ts = datetime.fromisoformat(r['timestamp'])
        except Exception:
            ts = None
        pnl = float(r.get('pnl') or 0)
        direction = r.get('direction')
        rows.append({'ts': ts, 'pnl': pnl, 'direction': direction, 'entry': float(r.get('entry_price') or 0)})

if not rows:
    print('No trades for', symbol)
    sys.exit(1)

# Basic stats
n = len(rows)
wins = sum(1 for r in rows if r['pnl'] > 0)
losses = sum(1 for r in rows if r['pnl'] <= 0)
win_rate = wins / n * 100
total_pnl = sum(r['pnl'] for r in rows)
avg_win = sum(r['pnl'] for r in rows if r['pnl']>0)/(wins or 1)
avg_loss = sum(r['pnl'] for r in rows if r['pnl']<=0)/(losses or 1)

# Hourly breakdown
by_hour = defaultdict(list)
for r in rows:
    if r['ts']:
        by_hour[r['ts'].hour].append(r)
hour_stats = {h: {'n': len(v), 'wr': sum(1 for x in v if x['pnl']>0)/len(v) if v else None, 'pnl': sum(x['pnl'] for x in v)} for h,v in by_hour.items()}

# Direction stats
by_dir = defaultdict(list)
for r in rows:
    by_dir[r['direction']].append(r)
dir_stats = {d:{'n':len(v),'wr':sum(1 for x in v if x['pnl']>0)/len(v) if v else None, 'pnl':sum(x['pnl'] for x in v)} for d,v in by_dir.items()}

# Consecutive loss streaks
streaks = []
cur = 0
max_streak = 0
for r in rows:
    if r['pnl'] <= 0:
        cur += 1
    else:
        if cur>0:
            streaks.append(cur)
        max_streak = max(max_streak, cur)
        cur = 0
if cur>0:
    streaks.append(cur)
    max_streak = max(max_streak, cur)

streak_counts = Counter(streaks)
current_streak = 0
for r in reversed(rows):
    if r['pnl']<=0:
        current_streak +=1
    else:
        break

# Consecutive losses clusters by time gap (<= 6 hours)
clusters = []
cluster = []
for r in rows:
    if not cluster:
        cluster=[r]
    else:
        prev = cluster[-1]
        if r['pnl']<=0 and prev['pnl']<=0 and r['ts'] and prev['ts'] and (r['ts']-prev['ts']).total_seconds()<=6*3600:
            cluster.append(r)
        else:
            clusters.append(cluster)
            cluster=[r]
if cluster:
    clusters.append(cluster)

cluster_summary = []
for c in clusters:
    cnt = len(c)
    pnl = sum(x['pnl'] for x in c)
    start = c[0]['ts'].isoformat() if c[0]['ts'] else None
    end = c[-1]['ts'].isoformat() if c[-1]['ts'] else None
    cluster_summary.append({'n':cnt,'pnl':pnl,'start':start,'end':end})

out = ROOT / 'runtime' / f'cluster_{symbol}_20260813.json'
json.dump({'symbol':symbol,'n':n,'wins':wins,'losses':losses,'win_rate':win_rate,'total_pnl':total_pnl,'avg_win':avg_win,'avg_loss':avg_loss,'hour_stats':hour_stats,'dir_stats':dir_stats,'streak_counts':dict(streak_counts),'max_streak':max_streak,'current_streak':current_streak,'clusters':cluster_summary}, open(out,'w'), indent=2, default=str)

# also write a short markdown
md = ROOT / 'runtime' / f'cluster_{symbol}_20260813.md'
with open(md,'w',encoding='utf-8') as f:
    f.write(f"# Cluster analysis {symbol} — 2026-08-13\n\n")
    f.write(f"Total trades: {n}\n\n")
    f.write(f"Win rate: {win_rate:.1f}%  (wins={wins}, losses={losses})\n\n")
    f.write(f"Total PnL: {total_pnl:+.2f}$\n\n")
    f.write(f"Max streak: {max_streak}, current streak: {current_streak}\n\n")
    f.write("## Hourly breakdown\n")
    for h in sorted(hour_stats.keys()):
        v = hour_stats[h]
        f.write(f"- {h:02d}:00 UTC — trades={v['n']}, wr={v['wr']:.2%}, pnl={v['pnl']:+.2f}\n")
    f.write('\n## Direction breakdown\n')
    for d,v in dir_stats.items():
        f.write(f"- {d}: n={v['n']}, wr={v['wr']:.2%}, pnl={v['pnl']:+.2f}\n")
    f.write('\n## Loss clusters (<=6h gap)\n')
    for c in cluster_summary[:20]:
        f.write(f"- {c['start']} → {c['end']}: n={c['n']}, pnl={c['pnl']:+.2f}\n")

print('Wrote', out, md)
