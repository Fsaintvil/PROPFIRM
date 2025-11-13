#!/usr/bin/env python3
"""Ferme les positions restantes ticket-par-ticket avec pause entre envois.
Ecrit le rapport dans artifacts/live_trading/close_all_positions_retry.json
Usage: python tmp/close_remaining_positions_with_pause.py [--pause SECONDS]
"""
import time
import json
from pathlib import Path
import sys

PAUSE = 6.0
try:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--pause', type=float, default=6.0)
    args = p.parse_args()
    PAUSE = float(args.pause)
except Exception:
    pass

OUTDIR = Path('artifacts') / 'live_trading'
OUTDIR.mkdir(parents=True, exist_ok=True)
OUTFILE = OUTDIR / 'close_all_positions_retry.json'

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

# initialize
init_ok = False
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

# fetch current positions
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

# helper to get price
def get_price_for_closing(symbol, pos_type):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    # pos_type: 0 buy, 1 sell
    if pos_type == 0:
        return float(tick.bid)
    else:
        return float(tick.ask)

# helper adjust volume (basic)
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

# iterate and try closing
for pos in positions:
    # robust extraction of common fields from different position representations
    ticket = None
    sym = None
    vol = None
    typ = None
    try:
        # object with attributes
        if hasattr(pos, 'ticket'):
            try:
                ticket = int(pos.ticket)
            except Exception:
                ticket = None
        if hasattr(pos, 'symbol'):
            sym = getattr(pos, 'symbol')
        if hasattr(pos, 'volume'):
            try:
                vol = float(getattr(pos, 'volume'))
            except Exception:
                vol = None
        if hasattr(pos, 'type'):
            try:
                typ = int(getattr(pos, 'type'))
            except Exception:
                typ = None
    except Exception:
        pass

    # fallback to mapping-like access
    if sym is None or vol is None or typ is None or ticket is None:
        try:
            d = pos._asdict() if hasattr(pos, '_asdict') else dict(pos)
            if ticket is None:
                try:
                    ticket = int(d.get('ticket'))
                except Exception:
                    ticket = None
            if sym is None:
                sym = d.get('symbol')
            if vol is None:
                try:
                    vol = float(d.get('volume'))
                except Exception:
                    vol = None
            if typ is None:
                try:
                    typ = int(d.get('type'))
                except Exception:
                    typ = None
        except Exception as e:
            report['errors'].append(f'pos_parse_failed:{str(e)}')
            continue
    # if still missing critical fields, skip
    if ticket is None or sym is None or vol is None or typ is None:
        report['errors'].append(
            'pos_missing_fields: ticket=%s sym=%s vol=%s type=%s' % (str(ticket), str(sym), str(vol), str(typ))
        )
        continue

    price = get_price_for_closing(sym, typ)
    if price is None:
        report['errors'].append(f'no_price_for_symbol:{sym}')
        continue

    vol_to_send = adjust_volume(sym, vol)

    # determine order type to close: if pos is buy (0), we send sell
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
        'comment': 'close_remaining_positions_with_pause',
    }

    # send and record
    try:
        res = mt5.order_send(request)
        # normalize result
        try:
            r = res._asdict()
        except Exception:
            r = str(res)
        report['attempts'].append({'ticket': ticket, 'symbol': sym, 'volume': vol, 'price': price, 'result': r})
    except Exception as e:
        report['attempts'].append({'ticket': ticket, 'symbol': sym, 'volume': vol, 'price': price, 'error': str(e)})

    # pause to avoid cadence blocks
    time.sleep(PAUSE)

# final remaining check
try:
    rem = mt5.positions_get()
    report['remaining_after'] = len(rem) if rem is not None else 0
except Exception as e:
    report['errors'].append('positions_get_after_failed:' + str(e))

# write
with open(OUTFILE, 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, default=str)

try:
    mt5.shutdown()
except Exception:
    pass

print(json.dumps(report, indent=2))
