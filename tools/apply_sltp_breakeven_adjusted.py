#!/usr/bin/env python3
"""
Apply adjusted breakeven SL for all open positions by setting SL slightly beyond price_open
in the correct direction so the broker accepts the request.
Requires ALLOW_MT5_SEND=1 in environment.
Writes results to artifacts/live_trading/mt5_apply_breakeven_adjusted_<TS>.json
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

TS = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
OUT_PATH = os.path.join('artifacts', 'live_trading', f'mt5_apply_breakeven_adjusted_{TS}.json')

if not mt5.initialize():
    print('ERROR: mt5.initialize failed:', mt5.last_error())
    sys.exit(5)

positions = mt5.positions_get()
if not positions:
    print('No open positions returned by MT5')
    mt5.shutdown()
    sys.exit(0)

results = []
for pos in positions:
    try:
        ticket = int(pos.ticket)
        symbol = pos.symbol
        price_open = float(pos.price_open)
        current_tp = float(getattr(pos, 'tp', 0) or 0)
        pinfo = mt5.symbol_info(symbol)
        if pinfo is None:
            point = 0.0001
        else:
            point = pinfo.point or 0.0001
        # choose delta: 5 points
        delta = point * 5
        # determine side: pos.type -> 0=buy,1=sell
        side = int(pos.type)
        if side == mt5.ORDER_TYPE_BUY:
            sl_target = price_open - delta
        else:
            sl_target = price_open + delta
        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': ticket,
            'sl': round(sl_target, 8),
            'tp': current_tp,
        }
        print(f'Applying adjusted SL={req["sl"]} for ticket {ticket} ({symbol}) side={side}')
        try:
            res = mt5.order_send(req)
        except Exception as e:
            res = {'error': str(e)}
        rec = {
            'ticket': ticket,
            'symbol': symbol,
            'side': side,
            'price_open': price_open,
            'sl_target': req['sl'],
            'request': req,
            'result': res._asdict() if hasattr(res, '_asdict') else str(res)
        }
        results.append(rec)
    except Exception as e:
        results.append({'error': str(e), 'position': str(pos)})

with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump({'timestamp': TS, 'applied': len(results), 'results': results}, f, indent=2)

print('Wrote apply results to', OUT_PATH)
mt5.shutdown()
sys.exit(0)
