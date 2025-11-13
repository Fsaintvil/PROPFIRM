#!/usr/bin/env python3
"""Escalade pour tous les SL/TP non acceptés trouvés dans les artefacts récents.

Comportement:
- Scanne `artifacts/live_trading` pour les fichiers `mt5_apply_*.json`,
  `mt5_apply_retry_*.json`, `mt5_enforce_sltp_rr_*.json`, `mt5_apply_retry_*` etc.
- Récupère les entrées dont `result.retcode` != 10009 et qui ont une `request`
  contenant SL/TP proposés.
- Pour chaque ticket encore ouvert, tente des facteurs [1.0,1.5,2.0,3.0,4.0,5.0]
  appliqués au delta de base (abs(entry - requested_sl) ou calcul via symbol_info)
  et envoie une requête TRADE_ACTION_SLTP par facteur jusqu'à acceptation (10009)
  ou fin de facteurs.

Usage: définir ALLOW_MT5_SEND=1 pour envois live, sinon utiliser --dry-run.
"""
import os
import sys
import json
import glob
import time
from datetime import datetime
import argparse

OUT_DIR = os.path.join('artifacts', 'live_trading')
os.makedirs(OUT_DIR, exist_ok=True)

parser = argparse.ArgumentParser(
    description='Escalate all failed SL/TP from artifacts'
)
parser.add_argument('--dry-run', action='store_true', help='Do not send orders, only propose')
parser.add_argument(
    '--factors',
    default='1.0,1.5,2.0,3.0,4.0,5.0',
    help='comma-separated factors to try (e.g. 1.0,1.5,2.0)'
)
parser.add_argument('--delay', type=float, default=0.3, help='delay (s) between attempts')
args = parser.parse_args()

DRY_RUN = args.dry_run
if not DRY_RUN and os.environ.get('ALLOW_MT5_SEND') != '1':
    print('ERROR: ALLOW_MT5_SEND != 1. Set ALLOW_MT5_SEND=1 to apply changes (or use --dry-run).')
    sys.exit(2)

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('MetaTrader5 import failed:', e)
    sys.exit(3)


def find_artifact_files():
    patterns = [
        os.path.join(OUT_DIR, 'mt5_apply_*.json'),
        os.path.join(OUT_DIR, 'mt5_apply_retry_*.json'),
        os.path.join(OUT_DIR, 'mt5_apply_retry_targeted_*.json'),
        os.path.join(OUT_DIR, 'mt5_apply_retry_minimum_*.json'),
        os.path.join(OUT_DIR, 'mt5_apply_retry_sl_then_tp_*.json'),
        os.path.join(OUT_DIR, 'mt5_apply_retry_escalate_*.json'),
        os.path.join(OUT_DIR, 'mt5_enforce_sltp_rr_*.json'),
        os.path.join(OUT_DIR, '*.json'),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    # deduplicate and sort by mtime desc
    files = sorted(set(files), key=os.path.getmtime, reverse=True)
    return files


def load_json(p):
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def extract_failed_requests(files):
    failed = {}
    for p in files:
        data = load_json(p)
        if not data:
            continue
        # artifacts may be either an object with key 'results' or a plain list
        if isinstance(data, list):
            results = data
        else:
            results = data.get('results') or data.get('positions') or []
        for e in results:
            req = e.get('request') or e.get('request', None)
            res = e.get('result') or {}
            # convert result retcode if in nested
            rc = None
            if isinstance(res, dict):
                rc = res.get('retcode')
            # treat non-accepted (not 10009) as failed if request sl/tp present
            if rc == 10009:
                continue
            if not req or not isinstance(req, dict):
                continue
            ticket = e.get('ticket') or req.get('position') or req.get('position')
            symbol = e.get('symbol') or req.get('symbol') or ''
            # sl/tp from the old request are not needed here; we recompute below
            if ticket is None:
                continue
            ticket = int(ticket)
            # store the most recent failed request per ticket
            info = {
                'ticket': ticket,
                'symbol': symbol,
                'request': req,
                'source_file': os.path.basename(p),
            }
            failed[ticket] = info
    return failed


def compute_base_delta(pos, req):
    try:
        entry = float(getattr(pos, 'price_open', 0.0) or 0.0)
        req_sl = req.get('sl')
        if req_sl is not None:
            d = abs(entry - float(req_sl))
            if d > 0:
                return float(d)
    except Exception:
        pass
    # fallback: use symbol_info
    try:
        si = mt5.symbol_info(req.get('symbol'))
        if si is not None:
            point = si.point if getattr(si, 'point', None) else 0.0
            stops_level = getattr(si, 'trade_stops_level', 0) or 0
            try:
                stops_level = int(stops_level)
            except Exception:
                stops_level = 0
            min_by_level = stops_level * point if point else 0.0
            fallback = max(point * 5, point or 0.0)
            safety = point * 2 if point else 0.0
            delta = max(min_by_level + safety, fallback)
            return float(delta)
    except Exception:
        pass
    return 0.0


def main():
    files = find_artifact_files()
    failed = extract_failed_requests(files)
    if not failed:
        print('No failed requests with SL/TP found in artifacts')
        return 0

    # initialize MT5 if possible so we can compute proposals even in --dry-run
    mt5_initialized = False
    try:
        mt5_initialized = mt5.initialize()
    except Exception:
        mt5_initialized = False

    if not mt5_initialized and not DRY_RUN:
        if hasattr(mt5, 'last_error'):
            print('mt5.initialize failed:', mt5.last_error())
        else:
            print('mt5.initialize failed: unknown error')
        return 5

    positions = mt5.positions_get() if mt5_initialized else []
    pos_map = {}
    if positions:
        for p in positions:
            try:
                pos_map[int(p.ticket)] = p
            except Exception:
                continue

    factors = [float(x) for x in args.factors.split(',') if x.strip()]

    results = []
    for ticket, info in failed.items():
        symbol = info.get('symbol')
        req = info.get('request')

        # check if position still open
        pos = None
        if ticket in pos_map:
            pos = pos_map[ticket]
        else:
            try:
                plist = mt5.positions_get(ticket=ticket) if mt5_initialized else None
                if plist:
                    pos = plist[0]
            except Exception:
                pos = None

        if pos is None:
            results.append({'ticket': ticket, 'symbol': symbol, 'error': 'position_not_found'})
            continue

        entry = float(getattr(pos, 'price_open', 0.0) or 0.0)
        pos_type = int(getattr(pos, 'type', 0))

        base_delta = compute_base_delta(pos, req)

        ticket_res = {'ticket': ticket, 'symbol': symbol, 'attempts': []}

        for factor in factors:
            delta = float(base_delta) * float(factor)
            if pos_type == mt5.ORDER_TYPE_BUY:
                sl = entry - delta
                tp = entry + (2.0 * delta)
            else:
                sl = entry + delta
                tp = entry - (2.0 * delta)

            # round by digits
            si = mt5.symbol_info(symbol)
            if si is not None and hasattr(si, 'digits') and si.digits is not None:
                try:
                    digits = int(si.digits)
                    sl = round(sl, digits)
                    tp = round(tp, digits)
                except Exception:
                    pass

            req_try = {
                'action': mt5.TRADE_ACTION_SLTP,
                'position': ticket,
                'sl': float(sl),
                'tp': float(tp),
                'factor': factor,
            }
            # concise printing split to satisfy line length limits
            print(f'Escalating ticket {ticket} {symbol} factor={factor}')
            print('SL=', req_try['sl'], 'TP=', req_try['tp'])

            if DRY_RUN:
                ticket_res['attempts'].append(
                    {'factor': factor, 'request': req_try, 'result': 'dry-run'}
                )
                continue

            try:
                r = mt5.order_send(req_try)
                if hasattr(r, '_asdict'):
                    res = r._asdict()
                else:
                    res = {
                        'retcode': getattr(r, 'retcode', None),
                        'comment': str(r),
                    }
            except Exception as exc:
                res = {'error': str(exc)}

            ticket_res['attempts'].append({'factor': factor, 'request': req_try, 'result': res})

            rc = None
            if isinstance(res, dict):
                rc = res.get('retcode')
            if rc == 10009:
                break

            time.sleep(float(args.delay))

        results.append(ticket_res)

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = {'timestamp': ts, 'processed': len(results), 'results': results}
    out_path = os.path.join(OUT_DIR, f'mt5_apply_retry_escalate_all_{ts}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print('Wrote', out_path)
    mt5.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
