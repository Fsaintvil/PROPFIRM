import json
import os
from datetime import datetime

import MetaTrader5 as mt5

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT_DIR = os.path.join(ROOT, 'artifacts', 'live_trading')
if not os.path.exists(OUT_DIR):
    os.makedirs(OUT_DIR, exist_ok=True)

now = datetime.utcnow()
ts = now.strftime('%Y%m%dT%H%M%SZ')
out_path = os.path.join(OUT_DIR, f'live_inventory_{ts}.json')
result = {'timestamp': now.isoformat(), 'orders': None, 'positions': None, 'errors': []}

try:
    ok = mt5.initialize()
    if not ok:
        result['errors'].append('mt5.initialize failed')
    else:
        try:
            # Orders (pending & history orders_get differs by platform; prefer orders_get if available)
            try:
                orders = mt5.orders_get()
            except Exception:
                orders = None
            if orders is None:
                result['orders'] = []
            else:
                out_orders = []
                for o in orders:
                    try:
                        od = o._asdict()
                    except Exception:
                        od = {k: getattr(o, k) for k in dir(o) if not k.startswith('_')}
                    out_orders.append(od)
                result['orders'] = out_orders

            # Positions
            try:
                positions = mt5.positions_get()
            except Exception:
                positions = None
            if positions is None:
                result['positions'] = []
            else:
                out_positions = []
                for p in positions:
                    try:
                        pd = p._asdict()
                    except Exception:
                        pd = {k: getattr(p, k) for k in dir(p) if not k.startswith('_')}
                    out_positions.append(pd)
                result['positions'] = out_positions
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
except Exception as e:
    result['errors'].append(str(e))

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, indent=2, default=str)

print(json.dumps({'status': 'done', 'path': out_path, 'orders': len(result.get('orders') or []), 'positions': len(result.get('positions') or [])}))
