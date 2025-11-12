import MetaTrader5 as mt5
import json

res = {'initialized': False, 'positions': None}
if mt5.initialize():
    res['initialized'] = True
    pos = mt5.positions_get()
    if pos is None:
        res['positions'] = []
    else:
        out = []
        for p in pos:
            try:
                out.append(p._asdict())
            except Exception:
                out.append({k: getattr(p, k) for k in dir(p) if not k.startswith('_')})
        res['positions'] = out
    mt5.shutdown()
print(json.dumps(res, default=str))
