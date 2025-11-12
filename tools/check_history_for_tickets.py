import json
import os
from datetime import datetime, timedelta
import MetaTrader5 as mt5

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
tmp = None
for fname in os.listdir(os.path.join(ROOT, 'artifacts', 'live_trading')):
    if fname.startswith('tmp_orders_for_enrich_') and fname.endswith('.json'):
        tmp = os.path.join(ROOT, 'artifacts', 'live_trading', fname)
        break
if not tmp:
    print(json.dumps({'status':'no_tmp_found'}))
    raise SystemExit(1)
with open(tmp,'r',encoding='utf-8') as f:
    data = json.load(f)
tickets = [int(e['ticket']) for e in data.get('entries',[]) if e.get('ticket')]

if not mt5.initialize():
    print(json.dumps({'status':'mt5_init_failed'}))
    raise SystemExit(1)

now = datetime.utcnow()
frm = now - timedelta(days=7)
to = now + timedelta(minutes=10)
orders = mt5.history_orders_get(frm, to)
deals = mt5.history_deals_get(frm, to)

found_orders = []
found_deals = []
if orders:
    for o in orders:
        try:
            od = o._asdict()
        except Exception:
            od = {k: getattr(o,k) for k in dir(o) if not k.startswith('_')}
        t = od.get('ticket') or od.get('order')
        if t and int(t) in tickets:
            found_orders.append({'ticket': int(t), 'order_obj': od})
if deals:
    for d in deals:
        try:
            dd = d._asdict()
        except Exception:
            dd = {k: getattr(d,k) for k in dir(d) if not k.startswith('_')}
        order_ref = dd.get('order')
        deal_id = dd.get('deal') or dd.get('ticket')
        if order_ref and int(order_ref) in tickets:
            found_deals.append({'order': int(order_ref), 'deal_obj': dd})

mt5.shutdown()
print(json.dumps({'status':'done','tickets_checked':len(tickets),'found_orders':len(found_orders),'found_deals':len(found_deals)}))
