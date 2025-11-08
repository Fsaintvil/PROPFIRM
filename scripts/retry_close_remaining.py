"""
Retry closing remaining positions safely:
- Read artifacts/live_trading/close_all_positions_result.json
- For each entry with deal==0, rebuild a close request
- Call mt5.order_check(request) and record response
- If order_check doesn't report 'Invalid request', call _mt5_send_safe(request)
- Write artifacts/live_trading/retry_close_results.json
"""
import os
import json
import time
from pathlib import Path

# migration: try import safe sender (fail-open)
try:
    from src.utils.mt5_safe import send_order as _mt5_send_safe
except Exception:
    _mt5_send_safe = None

OUT_DIR = Path('artifacts') / 'live_trading'
OUT_DIR.mkdir(parents=True, exist_ok=True)

infile = Path('artifacts') / 'live_trading' / 'close_all_positions_result.json'
if not infile.exists():
    print('INPUT_NOT_FOUND:', infile)
    raise SystemExit(1)

j = json.loads(infile.read_text(encoding='utf-8'))
closed = j.get('closed', [])
not_closed = [r for r in closed if not (r.get('order_result', {}).get('deal'))]

# helper

def adjust_volume(symbol, volume):
    try:
        import MetaTrader5 as mt5
        info = mt5.symbol_info(symbol)
        if info is None:
            return volume
        step = float(info.volume_step) if getattr(info, 'volume_step', None) else 0.01
        vmin = float(info.volume_min) if getattr(info, 'volume_min', None) else 0.01
        steps = int(volume / step)
        vol_adj = max(vmin, steps * step)
        if vol_adj <= 0:
            vol_adj = vmin
        return round(vol_adj, 8)
    except Exception:
        return volume

# mt5 init and credentials from config
creds_path = Path(__file__).parent.parent / 'config' / 'mt5_credentials.env'
if creds_path.exists():
    for line in creds_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('MT5_IMPORT_FAILED:', e)
    raise SystemExit(1)

init_kwargs = {}
login = os.getenv('MT5_LOGIN') or os.getenv('MT5_ACCOUNT')
password = os.getenv('MT5_PASSWORD') or os.getenv('MT5_PWD')
server = os.getenv('MT5_SERVER')
if login:
    try:
        init_kwargs['login'] = int(login)
    except Exception:
        init_kwargs['login'] = login
if password:
    init_kwargs['password'] = password
if server:
    init_kwargs['server'] = server

ok = mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()
if not ok:
    print('MT5_INITIALIZE_FAILED')

    results = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'attempts': [],
    }

for entry in not_closed:
    ticket = entry.get('ticket')
    symbol = entry.get('symbol')
    orig_vol = float(entry.get('volume') or 0.0)
    # find current position data from mt5
    positions = mt5.positions_get()
    pos = None
    if positions:
        for p in positions:
            try:
                if int(getattr(p, 'ticket', 0)) == int(ticket):
                    pos = p
                    break
            except Exception:
                continue
    if pos is None:
        note = 'position_not_found'
        results['attempts'].append(
            {'ticket': ticket, 'symbol': symbol, 'note': note}
        )
        continue

    type_pos = int(getattr(pos, 'type', 0))
    order_type = (
        mt5.ORDER_TYPE_SELL
        if type_pos == mt5.ORDER_TYPE_BUY
        else mt5.ORDER_TYPE_BUY
    )

    # get price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        results['attempts'].append(
            {'ticket': ticket, 'symbol': symbol, 'note': 'no_tick'}
        )
        continue
    price = (
        float(tick.bid)
        if order_type == mt5.ORDER_TYPE_SELL
        else float(tick.ask)
    )

    vol = adjust_volume(symbol, orig_vol)

    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'position': int(ticket),
        'symbol': symbol,
        'volume': vol,
        'type': order_type,
        'price': price,
        'deviation': 20,
        'magic': 0,
        'comment': 'retry_close_remaining.py',
    }

    # order_check
    try:
        check = mt5.order_check(request)
    except Exception as e:
        results['attempts'].append(
            {
                'ticket': ticket,
                'symbol': symbol,
                'note': 'order_check_exception',
                'error': str(e),
            }
        )
        continue

    check_dict = None
    try:
        check_dict = check._asdict()
    except Exception:
        try:
            check_dict = dict(check)
        except Exception:
            check_dict = {'raw': str(check)}

    attempt_record = {
        'ticket': ticket,
        'symbol': symbol,
        'request': request,
        'order_check': check_dict,
    }

    # if comment indicates invalid, skip
    comment = (
        check_dict.get('comment')
        if isinstance(check_dict, dict)
        else str(check_dict)
    )
    retcode = (
        check_dict.get('retcode') if isinstance(check_dict, dict) else None
    )
    if comment and 'Invalid' in str(comment):
        attempt_record['note'] = 'order_check_invalid'
        results['attempts'].append(attempt_record)
        continue

    # send
    try:
        send = _mt5_send_safe(request)
    except Exception as e:
        attempt_record['send_exception'] = str(e)
        results['attempts'].append(attempt_record)
        continue

    try:
        send_dict = send._asdict()
    except Exception:
        try:
            send_dict = dict(send)
        except Exception:
            send_dict = {'raw': str(send)}

    attempt_record['order_send'] = send_dict
    results['attempts'].append(attempt_record)
    # small backoff
    time.sleep(0.5)

# final remaining positions count
try:
    rem = mt5.positions_get()
    results['remaining_positions'] = len(rem) if rem is not None else 0
except Exception:
    results['remaining_positions'] = None

outf = OUT_DIR / 'retry_close_results.json'
outf.write_text(json.dumps(results, indent=2), encoding='utf-8')
print('Wrote', outf)
mt5.shutdown()
