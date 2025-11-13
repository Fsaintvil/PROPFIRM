#!/usr/bin/env python3
"""Retry minimal-compliant SL/TP for tickets that previously returned retcode 10016.

Reads the latest `mt5_apply_retry_targeted_10016_*.json` (or a provided file),
filters entries that returned retcode 10016 and are still open, then computes
the minimal allowed delta using `symbol_info.trade_stops_level` and `point` plus
small safety, and sends TRADE_ACTION_SLTP updates.

Supports --dry-run and requires ALLOW_MT5_SEND=1 for live sends.
"""
import os
import sys
import json
import glob
import math
from datetime import datetime

OUT_DIR = os.path.join('artifacts', 'live_trading')
os.makedirs(OUT_DIR, exist_ok=True)

import argparse
parser = argparse.ArgumentParser(description='Retry minimum-compliant SL/TP for 10016 tickets')
parser.add_argument('--source', help='targeted retry result JSON (mt5_apply_retry_targeted_10016_*.json)')
parser.add_argument('--dry-run', action='store_true', help='Do not send orders, only propose')
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


def find_latest(pattern):
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(obj, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def compute_min_delta(si):
    # compute minimal distance from trade_stops_level and point
    point = si.point if getattr(si, 'point', None) else 0.0
    stops_level = getattr(si, 'trade_stops_level', None)
    try:
        stops_level = int(stops_level) if stops_level is not None else 0
    except Exception:
        stops_level = 0

    min_by_level = (stops_level * point) if point else 0.0
    fallback = max(point * 5, point or 0.0)
    safety = point * 2 if point else 0.0
    delta = max(min_by_level + safety, fallback)
    if not delta or math.isnan(delta):
        delta = fallback
    return float(delta)


def main():
    source = args.source
    if not source:
        source = find_latest(os.path.join(OUT_DIR, 'mt5_apply_retry_targeted_10016_*.json'))
    if not source or not os.path.exists(source):
        print('No targeted 10016 result file found (mt5_apply_retry_targeted_10016_*.json)')
        return 4

    data = load_json(source)
    entries = data.get('results', [])
    # filter those with result.retcode == 10016
    to_retry = []
    for e in entries:
        res = e.get('result')
        if isinstance(res, dict) and res.get('retcode') == 10016:
            to_retry.append(e)

    if not to_retry:
        print('No entries with retcode 10016 found in', source)
        return 0

    if not mt5.initialize():
        print('mt5.initialize() failed:', mt5.last_error())
        return 5

    positions = mt5.positions_get()
    pos_map = {}
    if positions:
        for p in positions:
            try:
                pos_map[int(p.ticket)] = p
            except Exception:
                continue

    results = []
    for e in to_retry:
        ticket = int(e.get('ticket'))
        symbol = e.get('symbol')

        pos = pos_map.get(ticket)
        if pos is None:
            # try to fetch by ticket
            try:
                plist = mt5.positions_get(ticket=ticket)
                if plist and len(plist) > 0:
                    pos = plist[0]
            except Exception:
                pos = None

        if pos is None:
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'position_not_found'})
            continue

        entry_price = float(getattr(pos, 'price_open', 0.0) or 0.0)
        pos_type = int(getattr(pos, 'type', 0))

        si = mt5.symbol_info(symbol)
        if si is None:
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'symbol_info_missing'})
            continue

        delta = compute_min_delta(si)

        # compute SL/TP using delta; for buy SL = entry - delta, TP = entry + 2*delta
        if pos_type == mt5.ORDER_TYPE_BUY:
            sl = entry_price - delta
            tp = entry_price + (2.0 * delta)
        else:
            sl = entry_price + delta
            tp = entry_price - (2.0 * delta)

        # round according to digits
        if hasattr(si, 'digits') and si.digits is not None:
            try:
                digits = int(si.digits)
                sl = round(sl, digits)
                tp = round(tp, digits)
            except Exception:
                pass

        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': ticket,
            'sl': float(sl),
            'tp': float(tp),
        }

        print(f'Retrying ticket {ticket} {symbol} with minimal delta {delta} -> SL={req["sl"]} TP={req["tp"]}')

        if DRY_RUN:
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'result': 'dry-run'})
            continue

        try:
            r = mt5.order_send(req)
            # convert to dict where possible
            res_obj = r._asdict() if hasattr(r, '_asdict') else {
                'retcode': getattr(r, 'retcode', None), 'comment': str(r)
            }
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'result': res_obj})
        except Exception as exc:
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'error': str(exc)})

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = {
        'timestamp': ts,
        'source_targeted': os.path.basename(source),
        'processed': len(results),
        'results': results,
    }
    out_path = os.path.join(OUT_DIR, f'mt5_apply_retry_minimum_{ts}.json')
    save_json(out, out_path)
    print('Wrote', out_path)

    # summary
    counts = {}
    for r in results:
        if 'result' in r and isinstance(r['result'], dict) and r['result'].get('retcode') is not None:
            rc = r['result']['retcode']
        elif 'error' in r:
            rc = 'error'
        else:
            rc = 'unknown'
        counts[rc] = counts.get(rc, 0) + 1

    print('Summary counts:', counts)
    mt5.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
