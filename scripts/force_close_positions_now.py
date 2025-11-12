"""Force-close all current MT5 positions one-by-one (direct MT5 order_send).

WARNING: This script bypasses repository safety wrappers (`mt5_safe`) and
operational env checks. Run only after explicit authorization.

Output: writes `artifacts/live_trading/force_close_results.json` with per-ticket
results and mt5.last_error() snapshots.
"""
import json
import time
from pathlib import Path
import os

OUT = Path('artifacts') / 'live_trading' / 'force_close_results.json'
OUT.parent.mkdir(parents=True, exist_ok=True)

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('mt5 import failed', e)
    raise SystemExit(1)

# optional creds file load (same pattern as other scripts)
creds = Path(__file__).parent.parent / 'config' / 'mt5_credentials.env'
if creds.exists():
    for line in creds.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

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

if init_kwargs:
    mt5.initialize(**init_kwargs)
else:
    mt5.initialize()

results = {'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'records': []}

positions = mt5.positions_get()
positions = list(positions) if positions is not None else []

for p in positions:
    rec = {}
    try:
        ticket = int(getattr(p, 'ticket', 0))
        symbol = getattr(p, 'symbol', None)
        volume = float(getattr(p, 'volume', 0.0))
        p_type = int(getattr(p, 'type', 0))

        # closing order_type is opposite
        order_type = mt5.ORDER_TYPE_SELL if p_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            rec.update({'ticket': ticket, 'symbol': symbol, 'note': 'no_tick'})
            results['records'].append(rec)
            continue

        price = float(tick.bid) if order_type == mt5.ORDER_TYPE_SELL else float(tick.ask)

        # adjust volume to symbol step/min
        info = mt5.symbol_info(symbol)
        try:
            step = float(info.volume_step) if getattr(info, 'volume_step', None) else 0.01
            vmin = float(info.volume_min) if getattr(info, 'volume_min', None) else 0.01
            steps = int(round(volume / step))
            vol_adj = max(vmin, steps * step)
            if vol_adj <= 0:
                vol_adj = vmin
        except Exception:
            vol_adj = volume

        req = {
            'action': mt5.TRADE_ACTION_DEAL,
            'position': ticket,
            'symbol': symbol,
            'volume': vol_adj,
            'type': order_type,
            'price': price,
            'deviation': 200,
            'magic': 0,
            'comment': 'force_close_now',
        }

        rec['ticket'] = ticket
        rec['symbol'] = symbol
        rec['volume_requested'] = volume
        rec['volume_sent'] = vol_adj
        rec['request'] = req

        send = mt5.order_send(req)
        rec['order_send'] = {
            'retcode': getattr(send, 'retcode', None),
            'deal': getattr(send, 'deal', None),
            'order': getattr(send, 'order', None),
            'volume': getattr(send, 'volume', None),
            'price': getattr(send, 'price', None),
            'comment': getattr(send, 'comment', None),
        }
        rec['last_error'] = mt5.last_error()

        # brief pause to avoid flooding
        time.sleep(0.5)

        # re-check if ticket still present
        cur = mt5.positions_get()
        cur_tickets = {int(getattr(x, 'ticket', 0)) for x in (cur or [])}
        rec['still_open'] = ticket in cur_tickets

    except Exception as exc:
        rec = {'ticket': getattr(p, 'ticket', None), 'error': str(exc)}
    results['records'].append(rec)

try:
    rem = mt5.positions_get()
    results['remaining_positions'] = len(rem) if rem is not None else 0
except Exception:
    results['remaining_positions'] = None

OUT.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
print('WROTE', OUT)
mt5.shutdown()
