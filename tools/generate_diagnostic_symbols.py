"""
Generate a per-symbol diagnostic JSON from the latest enforcement artifact.
Designed to be run from PowerShell (pwsh) in this repo root.
Writes: artifacts/live_trading/diagnostic_symbols_<TS>.json

Usage (from repo root):
  python tools/generate_diagnostic_symbols.py

This script tries to import MetaTrader5 if available to read live symbol/position info.
If MT5 isn't available it falls back to conservative defaults.
"""

import json, os, glob, sys
from datetime import datetime

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ART_DIR = os.path.join(BASE, 'artifacts', 'live_trading')
if not os.path.isdir(ART_DIR):
    os.makedirs(ART_DIR, exist_ok=True)

# find latest enforcement artifact
enf_list = glob.glob(os.path.join(ART_DIR, 'mt5_enforce_sltp_rr_*.json'))
if not enf_list:
    print('No enforcement artifact found in', ART_DIR)
    sys.exit(2)
fn_enf = max(enf_list, key=os.path.getmtime)
print('Using enforcement file:', fn_enf)
with open(fn_enf, 'r', encoding='utf-8') as f:
    enf = json.load(f)
results = enf.get('results', [])

# map ticket -> entry
ticket_map = {}
symbols = set()
for r in results:
    t = r.get('ticket')
    sym = r.get('symbol')
    if sym:
        symbols.add(sym)
    if t is not None:
        ticket_map[int(t)] = r

# try to import MetaTrader5 (optional)
mt5 = None
try:
    import MetaTrader5 as mt5lib
    mt5 = mt5lib
    try:
        if not mt5.initialize():
            print('MT5 initialize failed:', mt5.last_error())
            mt5 = None
        else:
            print('MT5 initialized')
    except Exception as e:
        print('MT5 initialize exception:', e)
        mt5 = None
except Exception as e:
    print('MetaTrader5 not available, will use fallbacks:', e)
    mt5 = None

live_positions = []
if mt5:
    try:
        pos_list = mt5.positions_get()
        if pos_list:
            for p in pos_list:
                live_positions.append(p)
                symbols.add(p.symbol)
    except Exception as e:
        print('positions_get failed:', e)

report = {'timestamp': datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'), 'source_enforce': os.path.basename(fn_enf), 'symbols': {}}

for sym in sorted(symbols):
    si = None
    if mt5:
        try:
            si = mt5.symbol_info(sym)
        except Exception:
            si = None
    point = getattr(si, 'point', None) if si else None
    digits = getattr(si, 'digits', None) if si else None
    stops_level = getattr(si, 'trade_stops_level', None) if si else None
    if point is None:
        if sym.endswith('.cash') or any(x in sym for x in ['US500', 'JP225', 'US500.cash', 'JP225.cash']):
            point = 0.01
        elif 'JPY' in sym or sym.endswith('JPY'):
            point = 0.01
        elif sym in ('XAUUSD', 'XAGUSD'):
            point = 0.01
        else:
            point = 0.0001
    if digits is None:
        if point >= 1:
            digits = 2
        else:
            s = ('{:.10f}'.format(point)).rstrip('0')
            if '.' in s:
                digits = len(s.split('.', 1)[1])
            else:
                digits = 5
    if stops_level is None:
        stops_level = 5

    min_by_level = stops_level * point
    safety = 2 * point
    fallback = max(point * 10, point)
    # delta to attempt strict 1.5:3 (converted to R/R 1:2 where TP = 2*SL), ensure it's at least min_by_level + safety
    delta1 = max(min_by_level + safety, fallback)
    # relaxed delta preserving ratio (approx) 2:4
    delta2 = round(delta1 * (2.0 / 1.5), 10)

    pos_entries = []
    for p in live_positions:
        if p.symbol != sym:
            continue
        ticket = int(p.ticket)
        price_open = float(getattr(p, 'price_open', getattr(p, 'price', 0.0) or 0.0))
        side = int(getattr(p, 'type', getattr(p, 'position_type', 0)))
        if side == 1:
            sl1 = price_open + delta1
            tp1 = price_open - 2 * delta1
            sl2 = price_open + delta2
            tp2 = price_open - 2 * delta2
        else:
            sl1 = price_open - delta1
            tp1 = price_open + 2 * delta1
            sl2 = price_open - delta2
            tp2 = price_open + 2 * delta2
        sl1 = round(sl1, digits)
        tp1 = round(tp1, digits)
        sl2 = round(sl2, digits)
        tp2 = round(tp2, digits)
        enf_hit = ticket_map.get(ticket)
        accepted = False
        enf_rc = None
        if enf_hit:
            enf_rc = enf_hit.get('result', {}).get('retcode')
            accepted = enf_rc == 10009
        pos_entries.append({'ticket': ticket, 'price_open': price_open, 'side': side, 'accepted_in_enforce': accepted, 'enforce_retcode': enf_rc, 'proposal_1_5_3': {'sl': sl1, 'tp': tp1}, 'proposal_2_4': {'sl': sl2, 'tp': tp2}})

    if not pos_entries:
        samples = []
        for r in results:
            if r.get('symbol') == sym:
                t = r.get('ticket')
                rc = r.get('result', {}).get('retcode')
                req = r.get('request', {})
                samples.append({'ticket': t, 'req_sl': req.get('sl'), 'req_tp': req.get('tp'), 'retcode': rc})
                if len(samples) >= 6:
                    break
        pos_entries = [{'example': True, 'ticket': s['ticket'], 'req_sl': s['req_sl'], 'req_tp': s['req_tp'], 'retcode': s['retcode']} for s in samples]

    report['symbols'][sym] = {'point': point, 'digits': digits, 'trade_stops_level': stops_level, 'min_by_level': min_by_level, 'delta_1_5_3': delta1, 'delta_2_4': delta2, 'positions': pos_entries}

TS = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
out_path = os.path.join(ART_DIR, f'diagnostic_symbols_{TS}.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print('Wrote diagnostic:', out_path)
if mt5:
    try:
        mt5.shutdown()
    except Exception:
        pass

sys.exit(0)
