#!/usr/bin/env python3
"""
Enforce SL/TP rule (SL:TP = 1.5 : 3 → R/R 1:2) for all open positions.

This script proposes and applies SL/TP changes for currently open positions using
LiveTradingEngine.calculate_dynamic_stop_loss to compute a sensible SL and then
sets TP = entry +/- (SL_distance * 2.0) to reach the target R/R.

REQUIREMENTS: set environment variable ALLOW_MT5_SEND=1 to allow live modifications.
Use with caution: this will modify live positions.
"""

import os
import sys
import json
import argparse
from datetime import datetime

OUT_DIR = os.path.join('artifacts', 'live_trading')
os.makedirs(OUT_DIR, exist_ok=True)

# Accept --dry-run to only propose changes without sending them to MT5
parser = argparse.ArgumentParser(description='Enforce SL/TP RR on open positions')
parser.add_argument('--dry-run', action='store_true', help='Do not send orders, only propose and print')
args = parser.parse_args()

DRY_RUN = args.dry_run

if not DRY_RUN and os.environ.get('ALLOW_MT5_SEND') != '1':
    print('ERROR: ALLOW_MT5_SEND != 1. Set ALLOW_MT5_SEND=1 to apply changes (or run with --dry-run).')
    sys.exit(2)

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('MetaTrader5 import failed:', e)
    sys.exit(3)

# import engine to compute dynamic SL
try:
    # ensure the scripts directory is on sys.path for direct imports
    scripts_dir = os.path.join(os.getcwd(), 'scripts')
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from live_trading_engine import LiveTradingEngine
except Exception as e:
    print('Failed to import LiveTradingEngine:', e)
    sys.exit(5)

# initialize mt5
if not mt5.initialize():
    print('MT5 initialize failed:', mt5.last_error())
    # allow dry-run to continue if initialization failed? no — positions can't be read
    sys.exit(4)

engine = LiveTradingEngine()

positions = mt5.positions_get()
if not positions:
    print('No open positions found.')
    mt5.shutdown()
    sys.exit(0)

results = []
TS = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
OUT_PATH = os.path.join(OUT_DIR, f'mt5_enforce_sltp_rr_{TS}.json')

for p in positions:
    try:
        ticket = int(getattr(p, 'ticket', 0))
        symbol = getattr(p, 'symbol', None)
        entry_price = float(getattr(p, 'price_open', 0.0))
        pos_type = int(getattr(p, 'type', 0))
        action = 'buy' if pos_type == mt5.ORDER_TYPE_BUY else 'sell'
        cur_sl = float(getattr(p, 'sl', 0.0))

        # propose a dynamic stop using engine (best-effort)
        proposed_sl = engine.calculate_dynamic_stop_loss(symbol, action, entry_price)
        if proposed_sl is None:
            # fallback: keep current SL if present
            if cur_sl and cur_sl != 0:
                proposed_sl = cur_sl
            else:
                print(f"Skipping position {ticket} {symbol}: cannot determine SL")
                results.append({'ticket': ticket, 'symbol': symbol, 'error': 'no_sl_proposal'})
                continue

        sl_distance = abs(entry_price - proposed_sl)
        if sl_distance <= 0:
            print(f"Skipping position {ticket} {symbol}: computed zero sl_distance")
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'zero_sl_distance'})
            continue

        # desired TP = entry +/- 2 * sl_distance (R/R 1:2)
        if action == 'buy':
            proposed_tp = entry_price + (sl_distance * 2.0)
        else:
            proposed_tp = entry_price - (sl_distance * 2.0)

        # round according to symbol digits if available
        si = mt5.symbol_info(symbol)
        if si is not None and hasattr(si, 'digits'):
            digits = int(si.digits)
            proposed_sl = round(proposed_sl, digits)
            proposed_tp = round(proposed_tp, digits)

        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': ticket,
            'symbol': symbol,
            'sl': float(proposed_sl),
            'tp': float(proposed_tp),
        }

        print(f"Proposed SL/TP for {ticket} {symbol}: SL={proposed_sl} TP={proposed_tp}")
        if DRY_RUN:
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'result': 'dry-run'})
        else:
            try:
                res = mt5.order_send(req)
                # mt5 returns structure; attempt to convert
                res_obj = res._asdict() if hasattr(res, '_asdict') else str(res)
                results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'result': res_obj})
            except Exception as e:
                results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'error': str(e)})

    except Exception as e:
        results.append({'error': str(e)})

# write results
with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump({'timestamp': TS, 'results': results}, f, indent=2)

print('Results written to', OUT_PATH)
mt5.shutdown()
