#!/usr/bin/env python3
"""
Apply SL=price_open for all positions listed in artifacts/live_trading/current_positions.json
This performs live modifications via MetaTrader5. Requires ALLOW_MT5_SEND=1 in environment.
Writes results to artifacts/live_trading/mt5_apply_breakeven_<TS>.json
"""
import os
import sys
import json
from datetime import datetime

if os.environ.get('ALLOW_MT5_SEND') != '1':
    print('ERROR: ALLOW_MT5_SEND is not set to 1. Aborting.')
    sys.exit(2)

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('ERROR: MetaTrader5 module not available:', e)
    sys.exit(3)

IN_PATH = os.path.join('artifacts', 'live_trading', 'current_positions.json')
TS = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
OUT_PATH = os.path.join('artifacts', 'live_trading', f'mt5_apply_breakeven_{TS}.json')

try:
    with open(IN_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
except Exception as e:
    print('ERROR: cannot read current positions file:', e)
    sys.exit(4)

positions = data.get('positions', [])
if not positions:
    print('No positions found in', IN_PATH)
    sys.exit(0)

if not mt5.initialize():
    print('ERROR: mt5.initialize failed:', mt5.last_error())
    sys.exit(5)

results = []
for p in positions:
    try:
        ticket = int(p.get('ticket'))
        price_open = float(p.get('price_open') or 0)
        current_tp = float(p.get('tp') or 0)
        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': ticket,
            'sl': price_open,
            'tp': current_tp,
        }
        print(f'Applying SL={price_open} for ticket {ticket} ({p.get("symbol")})')
        try:
            res = mt5.order_send(req)
        except Exception as send_e:
            res = {'error': str(send_e)}
        rec = {
            'ticket': ticket,
            'symbol': p.get('symbol'),
            'request': req,
            'result': res._asdict() if hasattr(res, '_asdict') else str(res)
        }
        results.append(rec)
    except Exception as e:
        results.append({'error': str(e), 'position': p})

# write results
with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump({'timestamp': TS, 'applied': len(results), 'results': results}, f, indent=2)

print('Wrote apply results to', OUT_PATH)
mt5.shutdown()
sys.exit(0)
