import csv
import json
import os
import sys
from datetime import datetime, timedelta
import MetaTrader5 as mt5

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Allow optional input filename via CLI: python enrich_orders_with_mt5.py <in_json>
if len(sys.argv) > 1:
    IN_JSON = sys.argv[1]
else:
    IN_JSON = os.path.join(
        ROOT, 'artifacts', 'live_trading', 'orders_audit_20251110T114735Z.json'
    )

OUT_JSON = os.path.join(
    ROOT,
    'artifacts',
    'live_trading',
    'orders_audit_enriched_{}.json'.format(datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')),
)
OUT_CSV = os.path.join(
    ROOT,
    'artifacts',
    'live_trading',
    'orders_audit_enriched_{}.csv'.format(datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')),
)

with open(IN_JSON, 'r', encoding='utf-8') as f:
    audit = json.load(f)

tickets = [e['ticket'] for e in audit.get('entries', []) if e.get('ticket')]
result = {
    'timestamp': datetime.utcnow().isoformat(),
    'source': IN_JSON,
    'tickets': tickets,
    'orders': [],
    'errors': [],
}

if not tickets:
    result['errors'].append('no tickets found')
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps({'status': 'no_tickets', 'out': OUT_JSON}))
    raise SystemExit(0)

if not mt5.initialize():
    result['errors'].append('mt5.initialize failed')
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps({'status': 'mt5_failed', 'out': OUT_JSON}))
    raise SystemExit(1)

# query a window that includes recent orders
now = datetime.utcnow()
frm = now - timedelta(days=3)
to = now + timedelta(minutes=10)
orders = mt5.history_orders_get(frm, to)
# index history orders by ticket (if any)
hist_idx = {}
if orders is None:
    result['errors'].append('history_orders_get returned None')
else:
    for o in orders:
        try:
            od = o._asdict()
        except Exception:
            od = {k: getattr(o, k) for k in dir(o) if not k.startswith('_')}
        ticket = od.get('ticket') or od.get('order')
        if ticket:
            try:
                hist_idx[int(ticket)] = od
            except Exception:
                # keep as-is if cannot int-cast
                hist_idx[ticket] = od

# also index current open positions by ticket so we can detect recent fills
positions = mt5.positions_get()
pos_idx = {}
if positions is not None:
    for p in positions:
        try:
            pd = p._asdict()
        except Exception:
            pd = {k: getattr(p, k) for k in dir(p) if not k.startswith('_')}
        # positions typically have 'ticket' attribute
        ticket = pd.get('ticket') or pd.get('position') or pd.get('order')
        if ticket:
            try:
                pos_idx[int(ticket)] = pd
            except Exception:
                pos_idx[ticket] = pd

for t in tickets:
    tt = None
    try:
        tt = int(t)
    except Exception:
        tt = t

    entry = {'ticket': tt, 'found_in_history': False, 'found_in_positions': False}

    # check history
    od = hist_idx.get(tt)
    if od:
        entry.update(
            {
                'found_in_history': True,
                'symbol': od.get('symbol'),
                'type': od.get('type'),
                'volume': od.get('volume_initial') or od.get('volume'),
                'price_open': od.get('price_open') or od.get('price'),
                'time_setup': od.get('time_setup') or od.get('time'),
                'state': od.get('state'),
                'retcode': None,
                'comment': od.get('comment') if 'comment' in od else None,
            }
        )
        if 'result' in od and od['result']:
            try:
                entry['retcode'] = od['result'].get('retcode')
            except Exception:
                entry['retcode'] = None
    else:
        # check open positions
        pd = pos_idx.get(tt)
        if pd:
            entry.update(
                {
                    'found_in_positions': True,
                    'symbol': pd.get('symbol'),
                    'type': pd.get('type'),
                    'volume': pd.get('volume'),
                    'price_open': pd.get('price_open') or pd.get('price'),
                    'time_setup': pd.get('time'),
                    'profit': pd.get('profit'),
                }
            )
    # if neither found, keep flags false and record ticket
    result['orders'].append(entry)

mt5.shutdown()

# post-process: attach ai_send retcodes and controller log excerpts when available
try:
    # build ai_send index: ticket -> {retcode, request, file, ts}
    ai_idx = {}
    ai_dir = os.path.join(ROOT, 'artifacts', 'live_trading')
    if os.path.isdir(ai_dir):
        for fname in os.listdir(ai_dir):
            if fname.startswith('ai_send_') and fname.endswith('.json'):
                p = os.path.join(ai_dir, fname)
                try:
                    with open(p, 'r', encoding='utf-8') as af:
                        payload = json.load(af)
                    meta_ts = payload.get('meta', {}).get('timestamp')
                    for r in payload.get('results', []):
                        try:
                            ticket = int(r.get('order'))
                        except Exception:
                            ticket = r.get('order')
                        ai_idx[ticket] = {
                            'retcode': r.get('retcode'),
                            'request': r.get('request'),
                            'file': p,
                            'meta_ts': meta_ts,
                        }
                except Exception:
                    # skip unreadable files
                    continue

    # read controller log once for excerpts
    ctrl_log = os.path.join(ROOT, 'artifacts', 'live_trading', 'live_run_controller.log')
    ctrl_text = None
    if os.path.exists(ctrl_log):
        try:
            ctrl_text = open(ctrl_log, 'r', encoding='utf-8', errors='replace').read()
        except Exception:
            ctrl_text = None

    # attach to result entries
    for o in result.get('orders', []):
        t = o.get('ticket')
        if t is None:
            continue
            # attach ai_send info (include file/meta_ts and request fields)
        ai = ai_idx.get(t)
        if ai:
            o['ai_send'] = ai
            # expose convenient top-level CSV fields
            o['ai_send_file'] = ai.get('file')
            o['ai_send_meta_ts'] = ai.get('meta_ts')
            req = ai.get('request') or {}
            o['request_symbol'] = req.get('symbol')
            o['request_side'] = req.get('side')
            o['request_price'] = req.get('price')
            o['request_sl'] = req.get('sl')
            o['request_tp'] = req.get('tp')
            # attach controller log excerpt (first matching line containing order=<ticket>)
            if ctrl_text:
                try:
                    import re as _re
                    pat = _re.compile(r"^.*\b" + str(t) + r"\b.*$", _re.MULTILINE)
                    m = pat.search(ctrl_text)
                    if m:
                        o['controller_log_line'] = m.group(0)
                        # try to parse OrderSendResult fields from the controller line
                        try:
                            mm = _re.search(r"OrderSendResult\(retcode=(?P<retcode>\d+),\s*deal=(?P<deal>\d+).*?request_id=(?P<reqid>\d+)", m.group(0))
                            if mm:
                                try:
                                    o['controller_retcode'] = int(mm.group('retcode'))
                                except Exception:
                                    o['controller_retcode'] = mm.group('retcode')
                                try:
                                    o['controller_deal'] = int(mm.group('deal'))
                                except Exception:
                                    o['controller_deal'] = mm.group('deal')
                                try:
                                    o['controller_request_id'] = int(mm.group('reqid'))
                                except Exception:
                                    o['controller_request_id'] = mm.group('reqid')
                        except Exception:
                            pass
                except Exception:
                    pass
except Exception:
    # non-fatal
    pass

with open(OUT_JSON, 'w', encoding='utf-8') as f:
    json.dump(result, f, indent=2, default=str)
# Also write a CSV consolidation for quick review / spreadsheets
try:
    fieldnames = [
        'ticket', 'symbol', 'type', 'volume', 'price_open', 'time_setup',
        'state', 'retcode', 'comment', 'found_in_history',
        'found_in_positions', 'ai_send_retcode', 'ai_send_file', 'ai_send_meta_ts',
        'request_symbol', 'request_side', 'request_price', 'request_sl', 'request_tp',
        'controller_retcode', 'controller_deal', 'controller_request_id', 'controller_log_line'
    ]
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as cf:
        writer = csv.DictWriter(cf, fieldnames=fieldnames)
        writer.writeheader()
        for o in result.get('orders', []):
            row = {
                'ticket': o.get('ticket'),
                'symbol': o.get('symbol'),
                'type': o.get('type'),
                'volume': o.get('volume'),
                'price_open': o.get('price_open'),
                'time_setup': str(o.get('time_setup')) if o.get('time_setup') is not None else None,
                'state': o.get('state'),
                'retcode': o.get('retcode'),
                'comment': o.get('comment'),
                'found_in_history': o.get('found_in_history', False),
                'found_in_positions': o.get('found_in_positions', False),
                'ai_send_retcode': (o.get('ai_send', {}) or {}).get('retcode'),
                'controller_log_line': o.get('controller_log_line'),
            }
            writer.writerow(row)
except Exception:
    # don't fail the whole run for CSV issues; keep JSON
    pass

print(
    json.dumps(
        {
            'status': 'done',
            'out_json': OUT_JSON,
            'out_csv': OUT_CSV,
            'count': len(result['orders']),
        }
    )
)
