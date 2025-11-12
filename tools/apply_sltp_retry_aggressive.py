#!/usr/bin/env python3
"""Aggressive retry of SL/TP updates for tickets failed with Invalid stops.

Strategy:
- delta = max(trade_stops_level * point + margin, point * X) with X=10, margin=2*point
- Use market-relative SL: for SHORT (side==1) sl = max(price_open + delta, ask + delta)
  for LONG (side==0) sl = min(price_open - delta, bid - delta)

Reads latest retry/adjusted results file and retries only failed tickets.
Writes output to artifacts/live_trading/mt5_apply_retry_aggressive_<TS>.json
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


def find_latest_source():
    patterns = [
        os.path.join('artifacts', 'live_trading', 'mt5_apply_retry_stops_level_*.json'),
        os.path.join('artifacts', 'live_trading', 'mt5_apply_breakeven_adjusted_*.json'),
        os.path.join('artifacts', 'live_trading', 'mt5_apply_breakeven_*.json'),
    ]
    candidates = []
    for p in patterns:
        candidates.extend(glob.glob(p))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


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
        print('MetaTrader5 package not available')
        return 3

    src = find_latest_source()
    if not src:
        print('No source apply-results file found')
        return 4

    data = load_results(src)
    entries = data.get('results', [])
    # choose failures from available results
    failed = [e for e in entries if (e.get('result', {}).get('retcode') in (10016, 10013)) or e.get('error')]
    if not failed:
        print('No failed tickets to retry in', src)
        return 0

    print(f'Found {len(failed)} failed tickets to retry (from {os.path.basename(src)})')

    if not mt5.initialize():
        print('mt5.initialize() failed:', mt5.last_error())
        return 5

    results = []
    # read parameters from environment to support multiple strategies
    try:
        X = int(os.environ.get('RETRY_X', '10'))
    except Exception:
        X = 10
    try:
        stops_mult = float(os.environ.get('STOPS_MULT', '1'))
    except Exception:
        stops_mult = 1.0
    mode = os.environ.get('RETRY_MODE', 'price_and_market')
    print(f'Parameters: RETRY_X={X}, STOPS_MULT={stops_mult}, RETRY_MODE={mode}')
    for e in failed:
        ticket = e.get('ticket')
        symbol = e.get('symbol')
        side = e.get('side')
        price_open = e.get('price_open')

        # find live position
        pos = None
        try:
            pos_list = mt5.positions_get(ticket=ticket)
            if pos_list and len(pos_list) > 0:
                pos = pos_list[0]
        except Exception:
            pos = None

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

        # fallback: if price_open missing in the result entry, take from live position
        if price_open is None:
            try:
                price_open = float(getattr(pos, 'price_open', None) or getattr(pos, 'price', None) or 0.0)
            except Exception:
                price_open = 0.0

        tick = mt5.symbol_info_tick(symbol)
        bid = float(tick.bid) if tick and getattr(tick, 'bid', None) is not None else 0.0
        ask = float(tick.ask) if tick and getattr(tick, 'ask', None) is not None else 0.0

        point = si.point if si.point and si.point > 0 else 0.0
        stops_level = si.trade_stops_level if getattr(si, 'trade_stops_level', None) is not None else 0

        # apply multiplier to stops_level if provided
        stops_level = stops_level * stops_mult

        min_by_level = stops_level * point
        margin = 2 * point
        fallback = max(point * X, point or 0.0)
        delta = max(min_by_level + margin, fallback)
        if not delta or math.isnan(delta):
            delta = fallback
        # compute SL according to selected mode
        if mode == 'market-only':
            if side == 1:
                sl = ask + delta if ask else float(price_open) + delta
            else:
                sl = bid - delta if bid else float(price_open) - delta
        else:
            # price_and_market (default): prefer market-relative but respect price_open
            if side == 1:
                sl_market = ask + delta if ask else float(price_open) + delta
                sl_price = float(price_open) + delta
                sl = max(sl_market, sl_price)
            else:
                sl_market = bid - delta if bid else float(price_open) - delta
                sl_price = float(price_open) - delta
                sl = min(sl_market, sl_price)

        # round to symbol digits
        digits = int(si.digits) if getattr(si, 'digits', None) is not None else None
        if digits is not None:
            sl = round(sl, digits)

        tp = float(pos.tp) if getattr(pos, 'tp', 0) else 0.0

        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': int(ticket),
            'sl': sl,
            'tp': tp,
        }

        try:
            r = mt5.order_send(req)
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
        'source_file': os.path.basename(src),
        'processed': len(results),
        'results': results,
    }

    out_path = os.path.join('artifacts', 'live_trading', f'mt5_apply_retry_aggressive_{ts}.json')
    save_results(out, out_path)
    print('Wrote', out_path)

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
