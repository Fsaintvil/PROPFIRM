import json, os
import MetaTrader5 as mt5

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
tmp = None
for fname in os.listdir(os.path.join(ROOT, 'artifacts', 'live_trading')):
    if fname.startswith('tmp_orders_for_enrich_') and fname.endswith('.json'):
        tmp = os.path.join(ROOT, 'artifacts', 'live_trading', fname)
        break
if not tmp:
    print(json.dumps({'status':'no_tmp'}))
    raise SystemExit(1)
with open(tmp,'r',encoding='utf-8') as f:
    data = json.load(f)
tickets = set(int(e['ticket']) for e in data.get('entries',[]) if e.get('ticket'))

if not mt5.initialize():
    print(json.dumps({'status':'mt5_init_failed'}))
    raise SystemExit(1)

pos = mt5.positions_get()
found = []
if pos:
    for p in pos:
        try:
            d = p._asdict()
        except Exception:
            d = {k:getattr(p,k) for k in dir(p) if not k.startswith('_')}
        t = d.get('ticket') or d.get('identifier') or d.get('position')
        if t and int(t) in tickets:
            found.append({'ticket':int(t),'symbol':d.get('symbol'),'price_open':d.get('price_open'),'volume':d.get('volume'),'profit':d.get('profit')})

mt5.shutdown()
print(json.dumps({'status':'done','matched':found,'matched_count':len(found)}))
