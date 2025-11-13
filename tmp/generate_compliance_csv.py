"""
Génère un CSV de conformité à partir du dernier close_positions_run_*.json
Écrit: artifacts/live_trading/compliance_orders_YYYYMMDD_HHMMSS.csv
"""
import glob, json, csv
from pathlib import Path
from datetime import datetime

p = sorted(glob.glob('artifacts/live_trading/close_positions_run_*.json'), key=lambda x: Path(x).stat().st_mtime, reverse=True)
if not p:
    print('No close_positions_run JSON found')
    raise SystemExit(1)
js = p[0]
with open(js,'r',encoding='utf-8') as f:
    j = json.load(f)

rows = []
for r in j.get('results',[]):
    ticket = r.get('ticket')
    status = r.get('status')
    attempts = r.get('attempts', [])
    if attempts:
        last = attempts[-1]
        order = last.get('order')
        retcode = last.get('retcode')
        time = last.get('time')
        comment = last.get('comment')
    else:
        order = None
        retcode = None
        time = None
        comment = None
    rows.append({'ticket':ticket,'status':status,'order_id':order,'retcode':retcode,'time':time,'comment':comment})

ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
out = Path(f'artifacts/live_trading/compliance_orders_{ts}.csv')
out.parent.mkdir(parents=True,exist_ok=True)
with open(out,'w',newline='',encoding='utf-8') as cf:
    w = csv.DictWriter(cf, fieldnames=['ticket','status','order_id','retcode','time','comment'])
    w.writeheader()
    for row in rows:
        w.writerow(row)

print(str(out))
