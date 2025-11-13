import json
from datetime import datetime
from pathlib import Path
p=Path('artifacts')/ 'live_trading' / 'mt5_watchlist.jsonl'
if not p.exists():
    print('watchlist not found')
    raise SystemExit(0)
now=datetime.utcnow()
count=0
for i,line in enumerate(p.open('r',encoding='utf-8')):
    try:
        o=json.loads(line)
    except Exception:
        continue
    if o.get('closed'):
        continue
    ac=o.get('auto_close_at')
    if ac:
        try:
            ac_dt=datetime.fromisoformat(ac.replace('Z',''))
        except Exception:
            continue
        if ac_dt<=now:
            count+=1
            print(i+1, o.get('ticket'), o.get('symbol'), ac)
print('TOTAL_DUE:',count)
