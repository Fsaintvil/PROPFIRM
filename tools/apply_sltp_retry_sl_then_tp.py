#!/usr/bin/env python3
"""Retry by setting SL first (only), then TP if SL accepted.

Reads the latest `mt5_apply_retry_targeted_10016_*.json` (or a provided file),
for each entry with retcode 10016 that is still open:
 - compute a proposed SL/TP (prefer diagnostic proposal_2_4, else delta-based)
 - send TRADE_ACTION_SLTP with the proposed SL and the current TP (so TP isn't changed)
 - if SL update is accepted (retcode 10009), then send a second TRADE_ACTION_SLTP to set the desired TP

Supports --dry-run and requires ALLOW_MT5_SEND=1 for live sends.
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
parser = argparse.ArgumentParser(description='Apply SL first then TP for 10016 tickets')
parser.add_argument('--source', help='targeted 10016 result JSON (mt5_apply_retry_targeted_10016_*.json)')
parser.add_argument('--diagnostic', help='diagnostic_symbols JSON file (optional)')
parser.add_argument('--dry-run', action='store_true', help='Do not send orders, only propose')
parser.add_argument('--delay', type=float, default=0.5, help='delay seconds between operations')
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


def compute_proposal_from_diag(diagnostic, symbol, ticket, entry_price):
    if not diagnostic or symbol not in diagnostic:
        return None
    positions = diagnostic[symbol].get('positions', [])
    for p in positions:
        if int(p.get('ticket', 0)) == int(ticket):
            prop = p.get('proposal_2_4')
            if prop:
                return float(prop.get('sl')), float(prop.get('tp'))
    return None


def compute_delta_from_symbol(si):
    point = si.point if getattr(si, 'point', None) else 0.0
    # use delta_2_4 style fallback: point*5 or trade_stops_level*point + safety
    stops_level = getattr(si, 'trade_stops_level', 0) or 0
    try:
        stops_level = int(stops_level)
    except Exception:
        stops_level = 0
    min_by_level = stops_level * point if point else 0.0
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

    diagnostic = None
    if args.diagnostic and os.path.exists(args.diagnostic):
        diagnostic = load_json(args.diagnostic).get('symbols', {})
    else:
        dd = find_latest(os.path.join(OUT_DIR, 'diagnostic_symbols_*.json'))
        if dd:
            diagnostic = load_json(dd).get('symbols', {})

    data = load_json(source)
    entries = data.get('results', [])
    targets = [e for e in entries if isinstance(e.get('result'), dict) and e['result'].get('retcode') == 10016]
    if not targets:
        print('No 10016 entries found in', source)
        return 0

    if not mt5.initialize():
        print('mt5.initialize failed:', mt5.last_error())
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
    for e in targets:
        ticket = int(e.get('ticket'))
        symbol = e.get('symbol')

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

        entry_price = float(getattr(pos, 'price_open', 0.0) or 0.0)
        current_tp = float(getattr(pos, 'tp', 0.0) or 0.0)
        pos_type = int(getattr(pos, 'type', 0))

        # compute proposal
        proposal = compute_proposal_from_diag(diagnostic, symbol, ticket, entry_price)
        if proposal:
            proposed_sl, proposed_tp = proposal
        else:
            si = mt5.symbol_info(symbol)
            if si is None:
                results.append({'ticket': ticket, 'symbol': symbol, 'error': 'symbol_info_missing'})
                continue
            delta = compute_delta_from_symbol(si)
            if pos_type == mt5.ORDER_TYPE_BUY:
                proposed_sl = entry_price - delta
                proposed_tp = entry_price + (2.0 * delta)
            else:
                proposed_sl = entry_price + delta
                proposed_tp = entry_price - (2.0 * delta)

        # round according to digits
        si = mt5.symbol_info(symbol)
        if si is not None and hasattr(si, 'digits') and si.digits is not None:
            try:
                digits = int(si.digits)
                proposed_sl = round(proposed_sl, digits)
                proposed_tp = round(proposed_tp, digits)
            except Exception:
                pass

        # First attempt: set SL only (TP left as current_tp to avoid changing TP)
        req_sl_only = {'action': mt5.TRADE_ACTION_SLTP, 'position': ticket, 'sl': float(proposed_sl), 'tp': float(current_tp)}
        print(f"Ticket {ticket} {symbol}: attempting SL-only SL={req_sl_only['sl']} (TP unchanged={current_tp})")

        ticket_result = {'ticket': ticket, 'symbol': symbol, 'sl_attempt': None, 'tp_attempt': None}

        if DRY_RUN:
            ticket_result['sl_attempt'] = {'request': req_sl_only, 'result': 'dry-run'}
            # also record proposed TP for manual review
            ticket_result['tp_attempt'] = {'request': {'sl': req_sl_only['sl'], 'tp': float(proposed_tp)}, 'result': 'skipped-dry-run'}
            results.append(ticket_result)
            continue

        # send SL-only update
        try:
            r_sl = mt5.order_send(req_sl_only)
            res_sl = r_sl._asdict() if hasattr(r_sl, '_asdict') else {'retcode': getattr(r_sl, 'retcode', None), 'comment': str(r_sl)}
        except Exception as exc:
            res_sl = {'error': str(exc)}

        ticket_result['sl_attempt'] = {'request': req_sl_only, 'result': res_sl}

        rc_sl = None
        if isinstance(res_sl, dict):
            rc_sl = res_sl.get('retcode')

        # if SL accepted (10009), attempt TP update
        if rc_sl == 10009:
            # small wait to allow position to reflect new SL
            time.sleep(float(args.delay))
            # optionally re-fetch position
            try:
                new_pos = mt5.positions_get(ticket=ticket)[0]
                # use current SL from position to keep consistent
                cur_sl = float(getattr(new_pos, 'sl', 0.0) or proposed_sl)
            except Exception:
                cur_sl = proposed_sl

            req_tp = {'action': mt5.TRADE_ACTION_SLTP, 'position': ticket, 'sl': float(cur_sl), 'tp': float(proposed_tp)}
            try:
                r_tp = mt5.order_send(req_tp)
                res_tp = r_tp._asdict() if hasattr(r_tp, '_asdict') else {'retcode': getattr(r_tp, 'retcode', None), 'comment': str(r_tp)}
            except Exception as exc:
                res_tp = {'error': str(exc)}

            ticket_result['tp_attempt'] = {'request': req_tp, 'result': res_tp}
        else:
            ticket_result['tp_attempt'] = {'request': {'sl': req_sl_only['sl'], 'tp': float(proposed_tp)}, 'result': 'skipped-sl-not-accepted'}

        results.append(ticket_result)
        # small pause between tickets
        time.sleep(float(args.delay))

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = {'timestamp': ts, 'source': os.path.basename(source), 'processed': len(results), 'results': results}
    out_path = os.path.join(OUT_DIR, f'mt5_apply_retry_sl_then_tp_{ts}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print('Wrote', out_path)

    # quick summary
    counts = {'sl_accepted': 0, 'sl_rejected': 0, 'tp_accepted': 0, 'tp_rejected': 0, 'errors': 0, 'not_found': 0}
    for r in results:
        if 'error' in r:
            counts['not_found'] += 1
            continue
        slr = r.get('sl_attempt', {}).get('result')
        if isinstance(slr, dict) and slr.get('retcode') == 10009:
            counts['sl_accepted'] += 1
        else:
            counts['sl_rejected'] += 1

        tpr = r.get('tp_attempt', {}).get('result')
        if isinstance(tpr, dict) and tpr.get('retcode') == 10009:
            counts['tp_accepted'] += 1
        elif isinstance(tpr, dict) and tpr.get('retcode') is not None:
            counts['tp_rejected'] += 1

    print('Summary counts:', counts)
    mt5.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
