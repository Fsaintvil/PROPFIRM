#!/usr/bin/env python3
"""Retry SL/TP only for tickets whose last error was retcode_10016 and are still open.

Uses diagnostic proposals (proposal_2_4) when available, otherwise uses per-symbol
delta_2_4 to compute SL/TP. Respects --dry-run. Requires ALLOW_MT5_SEND=1 for live sends.
"""
import os
import sys
import json
import argparse
from datetime import datetime
import glob

OUT_DIR = os.path.join('artifacts', 'live_trading')
os.makedirs(OUT_DIR, exist_ok=True)

parser = argparse.ArgumentParser(description='Targeted retry for retcode_10016 tickets')
parser.add_argument('--analysis', help='retry_analysis JSON file', default=None)
parser.add_argument('--diagnostic', help='diagnostic_symbols JSON file', default=None)
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


def main():
    # determine analysis file
    analysis_path = args.analysis
    if not analysis_path:
        analysis_path = find_latest(os.path.join(OUT_DIR, 'retry_analysis_*.json'))
    if not analysis_path or not os.path.exists(analysis_path):
        print('No retry_analysis file found')
        return 4

    diagnostic_path = args.diagnostic
    if not diagnostic_path:
        diagnostic_path = find_latest(os.path.join(OUT_DIR, 'diagnostic_symbols_*.json'))

    analysis = load_json(analysis_path)

    # extract list of tickets with retcode_10016
    rc16 = analysis.get('errors', {}).get('retcode_10016', [])
    tickets_wanted = {int(e['ticket']): e for e in rc16}
    if not tickets_wanted:
        print('No retcode_10016 tickets found in analysis')
        return 0

    # load diagnostic if available
    diagnostic = None
    if diagnostic_path and os.path.exists(diagnostic_path):
        diagnostic = load_json(diagnostic_path).get('symbols', {})

    # initialize mt5
    if not mt5.initialize():
        print('MT5 initialize failed:', mt5.last_error())
        return 5

    # build map of open positions by ticket
    positions = mt5.positions_get()
    pos_map = {}
    if positions:
        for p in positions:
            try:
                pos_map[int(p.ticket)] = p
            except Exception:
                continue

    # filter tickets that are still open
    open_tickets = [t for t in tickets_wanted.keys() if t in pos_map]
    print(f'Found {len(open_tickets)} tickets still open out of {len(tickets_wanted)} retcode_10016 candidates')
    results = []

    for ticket in open_tickets:
        p = pos_map[ticket]
        symbol = getattr(p, 'symbol', None)
        entry_price = float(getattr(p, 'price_open', 0.0) or 0.0)
        pos_type = int(getattr(p, 'type', 0))
        side = 1 if pos_type == mt5.ORDER_TYPE_SELL else 0 if pos_type == mt5.ORDER_TYPE_BUY else None

        # try to get proposal from diagnostic per-position
        sl = None
        tp = None
        if diagnostic and symbol in diagnostic:
            # try to match position entry proposals
            sym = diagnostic[symbol]
            positions_list = sym.get('positions', [])
            for item in positions_list:
                if int(item.get('ticket', 0)) == ticket and item.get('proposal_2_4'):
                    prop = item['proposal_2_4']
                    sl = float(prop.get('sl'))
                    tp = float(prop.get('tp'))
                    break

        # fallback: compute using delta_2_4 from diagnostic symbol or simple heuristic
        if sl is None:
            delta = None
            if diagnostic and symbol in diagnostic:
                delta = diagnostic[symbol].get('delta_2_4')
            # if still None, attempt to use symbol_info
            if not delta:
                si = mt5.symbol_info(symbol)
                if si is not None:
                    point = si.point if si.point and si.point > 0 else 0.0
                    # use a coarse fallback similar to previous scripts
                    delta = max(point * 5, point or 0.0)
                else:
                    delta = 0.0

            # compute based on side: mt5.ORDER_TYPE_BUY=0, SELL=1
            if pos_type == mt5.ORDER_TYPE_BUY:
                sl = entry_price - float(delta)
                tp = entry_price + (2.0 * float(delta))
            else:
                sl = entry_price + float(delta)
                tp = entry_price - (2.0 * float(delta))

        # rounding according to symbol digits
        si = mt5.symbol_info(symbol)
        if si is not None and hasattr(si, 'digits'):
            digits = int(si.digits)
            sl = round(sl, digits)
            tp = round(tp, digits)

        req = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': int(ticket),
            'sl': float(sl),
            'tp': float(tp),
        }

        print(f"Ticket {ticket} {symbol}: proposing SL={req['sl']} TP={req['tp']}")

        if DRY_RUN:
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'result': 'dry-run'})
            continue

        try:
            r = mt5.order_send(req)
            res = r._asdict() if hasattr(r, '_asdict') else {
                'retcode': getattr(r, 'retcode', None),
                'comment': str(r)
            }
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'result': res})
        except Exception as exc:
            results.append({'ticket': ticket, 'symbol': symbol, 'request': req, 'error': str(exc)})

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = {
        'timestamp': ts,
        'source_analysis': os.path.basename(analysis_path),
        'processed': len(results),
        'results': results,
    }
    out_path = os.path.join(OUT_DIR, f'mt5_apply_retry_targeted_10016_{ts}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print('Wrote', out_path)
    # quick summary
    counts = {}
    for r in results:
        if 'result' in r and isinstance(r['result'], dict) and r['result'].get('retcode') is not None:
            rc = r['result'].get('retcode')
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
