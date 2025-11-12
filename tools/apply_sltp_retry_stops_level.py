#!/usr/bin/env python3
"""Retry SL/TP updates for tickets that previously failed with 'Invalid stops'.

Reads the last adjusted apply results JSON (mt5_apply_breakeven_adjusted_*.json),
filters failures (retcode 10016 or 10013), computes a compliant SL using
symbol_info.trade_stops_level and symbol_info.point, then re-sends the
TRADE_ACTION_SLTP request for each failed position.

Writes results to artifacts/live_trading/mt5_apply_retry_stops_level_<TS>.json
and prints a short summary.
"""
import json
import glob
import os
from datetime import datetime
import math

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


def find_latest_adjusted_file():
    pattern = os.path.join('artifacts', 'live_trading', 'mt5_apply_breakeven_adjusted_*.json')
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def load_results(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_results(obj, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def main():
    if os.environ.get('ALLOW_MT5_SEND') not in ('1', 'true', 'True'):
        print('ALLOW_MT5_SEND not set to 1/true — aborting to avoid live sends')
        return 2

    if mt5 is None:
        print('MetaTrader5 package not available in this environment')
        return 3

    latest = find_latest_adjusted_file()
    if not latest:
        print('No adjusted apply results file found')
        return 4

    data = load_results(latest)
    entries = data.get('results', [])
    failed = [e for e in entries if e.get('result', {}).get('retcode') in (10016, 10013)]
    if not failed:
        print('No failed tickets (10016/10013) to retry in', latest)
        return 0

    print(f'Found {len(failed)} failed tickets to retry (from {os.path.basename(latest)})')

    if not mt5.initialize():
        print('mt5.initialize() failed:', mt5.last_error())
        return 5

    results = []
    for e in failed:
        ticket = e.get('ticket')
        symbol = e.get('symbol')
        side = e.get('side')
        price_open = e.get('price_open')

        pos = None
        try:
            pos_list = mt5.positions_get(ticket=ticket)
            if pos_list and len(pos_list) > 0:
                pos = pos_list[0]
        except Exception:
            pos = None

        # If position not found by ticket, try all positions and match by ticket
        if pos is None:
            all_pos = mt5.positions_get()
            if all_pos:
                for p in all_pos:
                    if int(p.ticket) == int(ticket):
                        pos = p
                        break

        if pos is None:
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'position_not_found'})
            continue

        si = mt5.symbol_info(symbol)
        if si is None:
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'symbol_info_missing'})
            continue

        point = si.point if si.point and si.point > 0 else 0.0
        stops_level = si.trade_stops_level if getattr(si, 'trade_stops_level', None) is not None else 0

        # compute minimal distance in price units
        min_by_level = stops_level * point
        fallback = max(point * 5, point or 0.0)
        safety = point * 2 if point else 0.0
        delta = max(min_by_level + safety, fallback)

        # Ensure delta is not NaN
        if not delta or math.isnan(delta):
            delta = fallback

        # Determine SL based on side: follow previous convention where side==1 -> sell/short (SL above)
        if side == 1:
            sl = float(price_open) + float(delta)
        else:
            sl = float(price_open) - float(delta)

        # Keep existing TP if present
        tp = float(pos.tp) if getattr(pos, 'tp', 0) else 0.0

        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': int(ticket),
            'sl': sl,
            'tp': tp,
        }

        try:
            r = mt5.order_send(req)
            # Each result is a CStructure — convert to dict
            res = {
                'ticket': ticket,
                'symbol': symbol,
                'request': req,
                'result': {
                    'retcode': int(r.retcode) if hasattr(r, 'retcode') else None,
                    'deal': int(getattr(r, 'deal', 0) or 0),
                    'order': int(getattr(r, 'order', 0) or 0),
                    'volume': float(getattr(r, 'volume', 0) or 0.0),
                    'price': float(getattr(r, 'price', 0) or 0.0),
                    'comment': getattr(r, 'comment', '') if hasattr(r, 'comment') else str(r),
                }
            }
        except Exception as exc:
            res = {'ticket': ticket, 'symbol': symbol, 'error': f'exception:{exc}'}

        results.append(res)

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = {
        'timestamp': ts,
        'source_file': os.path.basename(latest),
        'processed': len(results),
        'results': results,
    }

    out_path = os.path.join('artifacts', 'live_trading', f'mt5_apply_retry_stops_level_{ts}.json')
    save_results(out, out_path)
    print('Wrote', out_path)

    # quick summary
    counts = {}
    for r in results:
        if 'result' in r and r['result'].get('retcode') is not None:
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
