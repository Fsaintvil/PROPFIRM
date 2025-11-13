import json
from pathlib import Path
p = Path('artifacts')/ 'live_trading' / 'mt5_watchlist.jsonl'
if not p.exists():
    print('MISSING', p)
    raise SystemExit(0)
lines = p.read_text(encoding='utf-8').splitlines()
new_lines = []
count = 0
for ln in lines:
    ln = ln.strip()
    if not ln:
        continue
    try:
        obj = json.loads(ln)
    except Exception as e:
        print('SKIP invalid json line:', e)
        new_lines.append(ln)
        continue
    if obj.get('closed') is not True:
        obj['closed'] = True
        count += 1
    new_lines.append(json.dumps(obj, default=str, ensure_ascii=False))
# backup original
bak = p.with_suffix('.jsonl.bak')
p.rename(bak)
# write updated
p.write_text('\n'.join(new_lines)+"\n", encoding='utf-8')
print('UPDATED', p, 'entries_closed:', count)
print('backup at', bak)
