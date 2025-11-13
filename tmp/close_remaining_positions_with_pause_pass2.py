#!/usr/bin/env python3
"""Pass 2: ferme positions restantes ticket-par-ticket, journalise mt5.last_error() après chaque order_send.
Usage: python tmp/close_remaining_positions_with_pause_pass2.py [--pause SECONDS]
Écrit : artifacts/live_trading/close_all_positions_retry_pass2.json
"""
import time
import json
import sys
from pathlib import Path

PAUSE = 30.0
try:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--pause', type=float, default=30.0)
    args = p.parse_args()
    PAUSE = float(args.pause)
except Exception:
    pass

OUTDIR = Path('artifacts') / 'live_trading'
OUTDIR.mkdir(parents=True, exist_ok=True)
OUTFILE = OUTDIR / 'close_all_positions_retry_pass2.json'

report = {
    'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'pause_seconds': PAUSE,
    'connected': False,
    'errors': [],
    'attempts': [],
    'remaining_after': None,
}

try:
    import MetaTrader5 as mt5
except Exception as e:
    report['errors'].append('mt5_import_failed: ' + str(e))
    with open(OUTFILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    sys.exit(1)

# init
try:
    init_ok = mt5.initialize()
    report['connected'] = bool(init_ok)
    if not init_ok:
        report['errors'].append('mt5.initialize_failed')
except Exception as e:
    report['errors'].append('mt5_initialize_exception:' + str(e))

if not report['connected']:
    with open(OUTFILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    sys.exit(1)

# fetch positions
try:
    positions = mt5.positions_get()
    positions = list(positions) if positions is not None else []
except Exception as e:
    report['errors'].append('positions_get_failed:' + str(e))
    positions = []

if not positions:
    report['remaining_after'] = 0
    with open(OUTFILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print('No positions to close.')
    mt5.shutdown()
    sys.exit(0)

# helpers
def get_price_for_closing(symbol, pos_type):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return float(tick.bid) if pos_type == 0 else float(tick.ask)

def adjust_volume(symbol, volume):
    try:
        si = mt5.symbol_info(symbol)
        if si is None:
            return round(volume, 8)
        step = float(getattr(si, 'volume_step', 0.01) or 0.01)
        vmin = float(getattr(si, 'volume_min', 0.01) or 0.01)
        steps = int(volume / step)
        vol_adj = max(vmin, steps * step)
        if vol_adj <= 0:
            vol_adj = vmin
        return round(vol_adj, 8)
    except Exception:
        return round(volume, 8)

# iterate positions and attempt close
for pos in positions:
    try:
        ticket = int(getattr(pos, 'ticket'))
        sym = pos.symbol
        vol = float(pos.volume)
        typ = int(pos.type)
    except Exception:
        try:
            d = pos._asdict() if hasattr(pos, '_asdict') else dict(pos)
            ticket = int(d.get('ticket'))
            sym = d.get('symbol')
            vol = float(d.get('volume'))
            typ = int(d.get('type'))
        except Exception as e:
            report['errors'].append('pos_parse_failed:' + str(e))
            continue

    price = get_price_for_closing(sym, typ)
    if price is None:
        report['errors'].append('no_price_for_symbol:' + str(sym))
        continue

    vol_to_send = adjust_volume(sym, vol)
    order_type = mt5.ORDER_TYPE_SELL if typ == 0 else mt5.ORDER_TYPE_BUY

    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'position': ticket,
        'symbol': sym,
        'volume': vol_to_send,
        'type': order_type,
        'price': price,
        'deviation': 20,
        'magic': 0,
        'comment': 'close_remaining_positions_with_pause_pass2',
    }

    # send
    try:
        res = mt5.order_send(request)
        # capture mt5.last_error immediately
        try:
            last_err = mt5.last_error()
        except Exception as e:
            last_err = ('last_error_exception', str(e))
        try:
            r = res._asdict()
        except Exception:
            r = str(res)
        report['attempts'].append({
            'ticket': ticket,
            'symbol': sym,
            'volume': vol,
            'price': price,
            'result': r,
            'mt5_last_error': last_err,
        })
    except Exception as e:
        # also record last_error
        try:
            last_err = mt5.last_error()
        except Exception as e2:
            last_err = ('last_error_exception', str(e2))
        report['attempts'].append({
            'ticket': ticket,
            'symbol': sym,
            'volume': vol,
            'price': price,
            'error': str(e),
            'mt5_last_error': last_err,
        })

    # pause
    time.sleep(PAUSE)

# final check
try:
    rem = mt5.positions_get()
    report['remaining_after'] = len(rem) if rem is not None else 0
except Exception as e:
    report['errors'].append('positions_get_after_failed:' + str(e))

with open(OUTFILE, 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, default=str)

try:
    mt5.shutdown()
except Exception:
    pass

print(json.dumps(report, indent=2))
