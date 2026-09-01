import csv, collections

rows = []
with open('runtime/trades_log.csv', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        try:
            row['pnl'] = float(row.get('pnl', 0) or 0)
        except:
            row['pnl'] = 0.0
        rows.append(row)

gr = [r for r in rows if r['timestamp'][:10] >= '2026-08-13']
total_pnl = sum(r['pnl'] for r in gr)
print(f"Trades GR: {len(gr)}, PnL total: {total_pnl:.2f}")

bs = collections.defaultdict(list)
for r in gr:
    bs[r['symbol']].append(r)

for s, ts in sorted(bs.items(), key=lambda x: -sum(r['pnl'] for r in x[1])):
    w = sum(1 for r in ts if r['pnl'] > 0)
    pnl = sum(r['pnl'] for r in ts)
    print(f"  {s:<12} {len(ts):>3} trades  WR={w/len(ts)*100:>5.1f}%  PnL={pnl:>+8.2f}")

# Hot-reload check
import os, datetime
if os.path.exists('runtime/robot.pid'):
    pid = open('runtime/robot.pid').read().strip()
    print(f"\nRobot PID: {pid}")
    try:
        import psutil
        p = psutil.Process(int(pid))
        print(f"  RAM: {p.memory_info().rss / 1024 / 1024:.0f}MB, alive=True")
    except:
        print(f"  Process alive check via PID file")

# Last config reload
log_path = 'logs/simple_robot.log'
if os.path.exists(log_path):
    with open(log_path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    for line in reversed(lines[-200:]):
        if 'symbol_limits recharg' in line or 'Configuration reloaded' in line:
            print(f"  Last hot-reload: {line.strip()[:120]}")
            break
