#!/usr/bin/env python3
"""Escalate SL/TP distance multiplicatively for tickets that returned 10016.

For each ticket found in the latest `mt5_apply_retry_targeted_10016_*.json` (or provided source),
the script will compute a base delta (from the targeted request if available,
otherwise from symbol_info.trade_stops_level fallback), then try factors in order
until the broker accepts (retcode 10009) or max factor reached.

Usage: set ALLOW_MT5_SEND=1 for live sends. Supports --dry-run, custom factors and delay.
"""
import os
import sys
import json
import glob
import time
import math
from datetime import datetime

OUT_DIR = os.path.join('artifacts', 'live_trading')
os.makedirs(OUT_DIR, exist_ok=True)

import argparse
parser = argparse.ArgumentParser(description='Escalate SL/TP distance multiplicatively for 10016 tickets')
parser.add_argument('--source', help='targeted 10016 result JSON (mt5_apply_retry_targeted_10016_*.json)')
parser.add_argument('--diagnostic', help='diagnostic_symbols JSON file (optional)')
parser.add_argument('--factors', help='comma-separated factors to try (default 1.0,1.5,2.0,3.0)', default='1.0,1.5,2.0,3.0')
parser.add_argument('--delay', type=float, default=0.5, help='delay (s) between attempts per ticket')
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


def base_delta_from_entry_and_request(pos, req):
    """Try to derive base delta from request.sl vs entry price; fallback None."""
    try:
        entry = float(getattr(pos, 'price_open', 0.0) or 0.0)
        req_sl = req.get('sl') if isinstance(req, dict) else None
        if req_sl is None:
            return None
        d = abs(entry - float(req_sl))
        return float(d) if d > 0 else None
    except Exception:
        return None


def compute_min_from_symbol(si):
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
        print('No targeted 10016 result file found')
        return 4

    diag = None
    if args.diagnostic and os.path.exists(args.diagnostic):
        diag = load_json(args.diagnostic).get('symbols', {})
    else:
        dd = find_latest(os.path.join(OUT_DIR, 'diagnostic_symbols_*.json'))
        if dd:
            diag = load_json(dd).get('symbols', {})

    data = load_json(source)
    entries = data.get('results', [])
    failed = [e for e in entries if isinstance(e.get('result'), dict) and e['result'].get('retcode') == 10016]
    if not failed:
        print('No 10016 entries to escalate')
        return 0

    factors = [float(x) for x in args.factors.split(',') if x.strip()]

    if not mt5.initialize():
        print('mt5.initialize failed:', mt5.last_error())
        return 5

    # build position map
    positions = mt5.positions_get()
    pos_map = {}
    if positions:
        for p in positions:
            try:
                pos_map[int(p.ticket)] = p
            except Exception:
                continue

    results = []
    for e in failed:
        ticket = int(e.get('ticket'))
        symbol = e.get('symbol')
        req_prev = e.get('request') if isinstance(e.get('request'), dict) else {}

        pos = pos_map.get(ticket)
        if pos is None:
            try:
                plist = mt5.positions_get(ticket=ticket)
                pos = plist[0] if plist else None
            except Exception:
                pos = None

        if pos is None:
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'position_not_found'})
            continue

        # determine base delta: prefer delta from previous request, else fallback to symbol min
        base_delta = base_delta_from_entry_and_request(pos, req_prev)
        si = mt5.symbol_info(symbol)
        if base_delta is None:
            if si is not None:
                base_delta = compute_min_from_symbol(si)
            else:
                base_delta = 0.0

        entry_price = float(getattr(pos, 'price_open', 0.0) or 0.0)
        pos_type = int(getattr(pos, 'type', 0))

        ticket_result = {'ticket': ticket, 'symbol': symbol, 'attempts': []}

        accepted = False
        for factor in factors:
            delta = float(base_delta) * float(factor)
            if pos_type == mt5.ORDER_TYPE_BUY:
                sl = entry_price - delta
                tp = entry_price + (2.0 * delta)
            else:
                sl = entry_price + delta
                tp = entry_price - (2.0 * delta)

            # round
            if si is not None and hasattr(si, 'digits') and si.digits is not None:
                try:
                    digits = int(si.digits)
                    sl = round(sl, digits)
                    tp = round(tp, digits)
                except Exception:
                    pass

            req = {'action': mt5.TRADE_ACTION_SLTP, 'position': ticket, 'sl': float(sl), 'tp': float(tp), 'factor': factor}
            print(f"Trying ticket {ticket} factor={factor} -> SL={req['sl']} TP={req['tp']}")

            if DRY_RUN:
                ticket_result['attempts'].append({'factor': factor, 'request': req, 'result': 'dry-run'})
                continue

            try:
                r = mt5.order_send(req)
                res_obj = r._asdict() if hasattr(r, '_asdict') else {'retcode': getattr(r, 'retcode', None), 'comment': str(r)}
            except Exception as exc:
                res_obj = {'error': str(exc)}

            ticket_result['attempts'].append({'factor': factor, 'request': req, 'result': res_obj})

            # check success
            rc = None
            if isinstance(res_obj, dict) and res_obj.get('retcode') is not None:
                rc = res_obj.get('retcode')
            if rc == 10009:
                accepted = True
                break

            # small delay before next factor
            time.sleep(float(args.delay))

        results.append(ticket_result)

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = {'timestamp': ts, 'source': os.path.basename(source), 'processed': len(results), 'results': results}
    out_path = os.path.join(OUT_DIR, f'mt5_apply_retry_escalate_{ts}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print('Wrote', out_path)
    # counts
    counts = {'accepted': 0, 'rejected': 0, 'errors': 0, 'not_found': 0}
    for r in results:
        if 'error' in r:
            counts['not_found'] += 1
            continue
        accepted_flag = False
        for a in r.get('attempts', []):
            res = a.get('result')
            if isinstance(res, dict) and res.get('retcode') == 10009:
                accepted_flag = True
                break
        if accepted_flag:
            counts['accepted'] += 1
        else:
            counts['rejected'] += 1

    print('Summary counts:', counts)
    mt5.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
