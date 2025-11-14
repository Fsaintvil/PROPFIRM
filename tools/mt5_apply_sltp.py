#!/usr/bin/env python3
"""
Apply SL/TP proposals saved by mt5_sl_tp_propose.py.
REQUIREMENTS: This script will perform live modifications on your MT5 account.
You MUST set environment variable ALLOW_MT5_SEND=1 to enable execution.

Usage:
    python tools/mt5_apply_sltp.py [path_to_proposals.json]
If no path provided, the script picks the most recent mt5_proposed_sltp_*.json in artifacts/mt5_backups.
"""
import glob
import json
import os
import sys
from datetime import datetime

if os.environ.get('ALLOW_MT5_SEND') != '1':
    print('ALERT: ALLOW_MT5_SEND is not set to 1. Set ALLOW_MT5_SEND=1 to perform live modifications.')
    sys.exit(2)

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('MetaTrader5 module not available:', e)
    sys.exit(3)

OUT_DIR = os.path.join('artifacts', 'mt5_backups')
if len(sys.argv) > 1:
    proposals_path = sys.argv[1]
else:
    files = sorted(glob.glob(os.path.join(OUT_DIR, 'mt5_proposed_sltp_*.json')))
    if not files:
        print('No proposals file found in', OUT_DIR)
        sys.exit(4)
    proposals_path = files[-1]

print('Using proposals file:', proposals_path)
with open(proposals_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
proposals = data.get('proposals', [])
if not proposals:
    print('No proposals to apply in file')
    sys.exit(0)

def load_symbol_constraints(path=os.path.join('artifacts', 'live_trading', 'symbol_constraints.json')):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


if not mt5.initialize():
    print('MT5 initialize failed:', mt5.last_error())
    sys.exit(5)

SYMBOL_CONSTRAINTS = load_symbol_constraints()

# Automation-safe settings (non-invasive):
# - AUTO_MODE: 'manual' (default), 'canary', 'staged', 'full'
# - APPROVAL_TOKEN: required for 'staged' and 'full' modes (must be provided in env)
# - CANARY_COUNT: number of proposals to apply in canary mode (default 1)
# - STAGED_BATCH: batch size for staged mode (default 10)
# - VERIFY_AFTER_BATCH: if '1', attempt to run tools/mt5_verify_apply.py after each batch (best-effort)

AUTO_MODE = os.environ.get('AUTO_MODE', 'manual').lower()
APPROVAL_TOKEN = os.environ.get('APPROVAL_TOKEN')
CANARY_COUNT = int(os.environ.get('CANARY_COUNT', '1') or '1')
STAGED_BATCH = int(os.environ.get('STAGED_BATCH', '10') or '10')
VERIFY_AFTER_BATCH = os.environ.get('VERIFY_AFTER_BATCH', '0') == '1'
MAX_TO_APPLY = os.environ.get('MAX_TO_APPLY')
if MAX_TO_APPLY:
    try:
        MAX_TO_APPLY = int(MAX_TO_APPLY)
    except Exception:
        MAX_TO_APPLY = None

results = []
TS = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
RESULT_FILE = os.path.join(OUT_DIR, f'mt5_apply_result_{TS}.json')


def _apply_one(pr):
    ticket = int(pr['ticket'])
    symbol = pr['symbol']
    sl = float(pr.get('proposed_sl') or pr.get('sl') or 0)
    tp = float(pr.get('proposed_tp') or pr.get('tp') or 0)
    # best-effort: fetch live position to determine side (buy/sell) and current prices
    pos = None
    try:
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            pos = positions[0]
    except Exception:
        pos = None

    # get tick for price reference
    tick = mt5.symbol_info_tick(symbol)
    syminfo = mt5.symbol_info(symbol)

    # helper rounding by symbol point/digits
    def round_by_point(val):
        try:
            p = float(syminfo.point) if syminfo and syminfo.point else 0.00001
            digits = getattr(syminfo, 'digits', None)
            if digits is not None:
                return round(val, int(digits))
            # fallback to point quantization
            return float(round(val / p) * p)
        except Exception:
            return val

    # determine market-side price
    if pos is not None:
        side = getattr(pos, 'type', None)  # 0 = buy, 1 = sell
    else:
        # fallback: try to infer from proposal
        side = None

    price_ref = None
    if tick:
        price_ref = tick.ask if side == 0 else tick.bid if side == 1 else (tick.bid + tick.ask) / 2.0
    else:
        # fallback to position open price or symbol info
        if pos is not None:
            price_ref = float(getattr(pos, 'price_open', 0.0))
        elif syminfo:
            price_ref = float(syminfo.ask if getattr(syminfo, 'ask', None) else (getattr(syminfo, 'bid', 0.0)))
        else:
            price_ref = 0.0

    # fetch min_stop_distance from symbol constraints or fallback
    min_stop_distance = None
    sc = SYMBOL_CONSTRAINTS.get(symbol)
    try:
        if sc and isinstance(sc, dict):
            if 'min_stop_distance' in sc and sc['min_stop_distance']:
                min_stop_distance = float(sc['min_stop_distance'])
            else:
                # compute from trade_stops_level * point if available in constraints
                tsl_c = sc.get('trade_stops_level')
                pt_c = sc.get('point')
                if tsl_c and pt_c:
                    min_stop_distance = float(tsl_c) * float(pt_c)
    except Exception:
        min_stop_distance = None
    if min_stop_distance is None:
        try:
            tsl = getattr(syminfo, 'trade_stops_level', None)
            point = getattr(syminfo, 'point', None) or 0.00001
            if tsl is not None:
                min_stop_distance = float(tsl * point)
        except Exception:
            min_stop_distance = None
    if not min_stop_distance or min_stop_distance <= 0:
        # dynamic fallback: use spread + 2*point if available
        try:
            spr = 0.0
            if tick and getattr(tick, 'ask', None) and getattr(tick, 'bid', None):
                spr = (tick.ask - tick.bid)
            pt = getattr(syminfo, 'point', None) or 0.00001
            min_stop_distance = max(spr + (2 * pt), 1e-5)
        except Exception:
            min_stop_distance = 1e-5

    adjusted = {'adjusted_sl': sl, 'adjusted_tp': tp, 'constraint_used': min_stop_distance}

    # enforcement helper
    try:
        # SL enforcement
        if sl and price_ref:
            if side == 0:
                # buy: SL must be sufficiently below price_ref
                dist = price_ref - sl
                if dist < min_stop_distance:
                    new_sl = price_ref - (min_stop_distance + (syminfo.point if syminfo else 0.0))
                    new_sl = round_by_point(new_sl)
                    adjusted['adjusted_sl'] = float(new_sl)
            elif side == 1:
                # sell: SL must be sufficiently above price_ref
                dist = sl - price_ref
                if dist < min_stop_distance:
                    new_sl = price_ref + (min_stop_distance + (syminfo.point if syminfo else 0.0))
                    new_sl = round_by_point(new_sl)
                    adjusted['adjusted_sl'] = float(new_sl)
            else:
                # unknown side: use absolute distance
                dist = abs(price_ref - sl)
                if dist < min_stop_distance:
                    # push SL away from price_ref
                    if sl < price_ref:
                        buffer = (syminfo.point if syminfo else 0.0)
                        new_sl = price_ref - (min_stop_distance + buffer)
                    else:
                        buffer = (syminfo.point if syminfo else 0.0)
                        new_sl = price_ref + (min_stop_distance + buffer)
                    adjusted['adjusted_sl'] = float(round_by_point(new_sl))

        # TP enforcement
        if tp and price_ref:
            if side == 0:
                # buy: TP must be sufficiently above price_ref
                dist = tp - price_ref
                if dist < min_stop_distance:
                    new_tp = price_ref + (min_stop_distance + (syminfo.point if syminfo else 0.0))
                    adjusted['adjusted_tp'] = float(round_by_point(new_tp))
            elif side == 1:
                # sell: TP must be sufficiently below price_ref
                dist = price_ref - tp
                if dist < min_stop_distance:
                    new_tp = price_ref - (min_stop_distance + (syminfo.point if syminfo else 0.0))
                    adjusted['adjusted_tp'] = float(round_by_point(new_tp))
            else:
                # unknown side
                dist = abs(price_ref - tp)
                if dist < min_stop_distance:
                    if tp < price_ref:
                        buffer = (syminfo.point if syminfo else 0.0)
                        new_tp = price_ref - (min_stop_distance + buffer)
                    else:
                        buffer = (syminfo.point if syminfo else 0.0)
                        new_tp = price_ref + (min_stop_distance + buffer)
                    adjusted['adjusted_tp'] = float(round_by_point(new_tp))
    except Exception:
        # on any error, keep original values
        adjusted = {'adjusted_sl': sl, 'adjusted_tp': tp, 'constraint_used': min_stop_distance}

    # Build request with adjusted values
    req = {
        'action': mt5.TRADE_ACTION_SLTP,
        'position': ticket,
        'symbol': symbol,
        'sl': adjusted['adjusted_sl'],
        'tp': adjusted['adjusted_tp'],
    }

    print(
        f"Modifying position {ticket} {symbol} -> SL={adjusted['adjusted_sl']} "
        f"TP={adjusted['adjusted_tp']} (constraint {min_stop_distance})"
    )
    try:
        res = mt5.order_send(req)
        res_obj = res._asdict() if hasattr(res, '_asdict') else str(res)
    except Exception as e:
        res_obj = {'error': str(e)}
    rec = {
        'ticket': ticket,
        'symbol': symbol,
        'request': req,
        'result': res_obj,
        'adjusted': adjusted,
    }
    results.append(rec)
    return rec


if AUTO_MODE not in ('manual', 'canary', 'staged', 'full'):
    print('Unknown AUTO_MODE', AUTO_MODE, 'falling back to manual')
    AUTO_MODE = 'manual'

if AUTO_MODE in ('staged', 'full') and not APPROVAL_TOKEN:
    print(
        'ERROR: AUTO_MODE is', AUTO_MODE,
        'but APPROVAL_TOKEN is not set in environment. Aborting.'
    )
    sys.exit(6)

# Determine how many to apply
to_apply = list(proposals)
if MAX_TO_APPLY is not None:
    to_apply = to_apply[:MAX_TO_APPLY]

if AUTO_MODE == 'manual':
    # previous behavior: apply all
    for pr in to_apply:
        _apply_one(pr)

elif AUTO_MODE == 'canary':
    # apply only first CANARY_COUNT proposals
    print('AUTO_MODE=canary: applying first', CANARY_COUNT, 'proposal(s)')
    for pr in to_apply[:CANARY_COUNT]:
        _apply_one(pr)

elif AUTO_MODE == 'staged':
    print('AUTO_MODE=staged: applying in batches of', STAGED_BATCH)
    applied = 0
    for i in range(0, len(to_apply), STAGED_BATCH):
        batch = to_apply[i:i+STAGED_BATCH]
        print(f'Applying batch {i//STAGED_BATCH + 1}: {len(batch)} items')
        for pr in batch:
            _apply_one(pr)
            applied += 1
        # write intermediate results
        with open(RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'timestamp': TS,
                    'mode': AUTO_MODE,
                    'applied': applied,
                    'results': results,
                },
                f,
                indent=2,
            )
        print('Wrote intermediate results to', RESULT_FILE)
        # optional verification
        if VERIFY_AFTER_BATCH:
            try:
                print('Running verification after batch (best-effort)')
                os.system(f"python tools/mt5_verify_apply.py {RESULT_FILE}")
            except Exception:
                print('Verification step failed (ignored)')

elif AUTO_MODE == 'full':
    print('AUTO_MODE=full: applying all proposals in one pass')
    for pr in to_apply:
        _apply_one(pr)

# final write
with open(RESULT_FILE, 'w', encoding='utf-8') as f:
    json.dump({'timestamp': TS, 'mode': AUTO_MODE, 'results': results}, f, indent=2)

print('Apply results written to', RESULT_FILE)
mt5.shutdown()
