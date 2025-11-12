"""
Order-check diagnostics for current positions.
Writes artifacts/live_trading/order_check_diagnostics.json
"""
import os
import json
import time
from pathlib import Path

OUT = Path('artifacts') / 'live_trading' / 'order_check_diagnostics.json'
OUT.parent.mkdir(parents=True, exist_ok=True)

# load creds
creds = Path(__file__).parent.parent / 'config' / 'mt5_credentials.env'
if creds.exists():
    for line in creds.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k,v = line.split('=',1)
        os.environ.setdefault(k.strip(), v.strip())

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('mt5 import failed', e)
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

mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()

positions = mt5.positions_get()
res = {'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'diagnostics': []}
if positions is None:
    positions = []

for p in list(positions):
    try:
        ticket = int(getattr(p, 'ticket', 0))
        symbol = getattr(p, 'symbol', None)
        volume = float(getattr(p, 'volume', 0.0))
        p_type = int(getattr(p, 'type', 0))
        order_type = mt5.ORDER_TYPE_SELL if p_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(symbol)
        price = None
        if tick is not None:
            price = float(tick.bid) if order_type == mt5.ORDER_TYPE_SELL else float(tick.ask)
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'position': ticket,
            'symbol': symbol,
            'volume': volume,
            'type': order_type,
            'price': price,
            'deviation': 20,
            'magic': 0,
            'comment': 'order_check_diagnostics.py'
        }
        # order_check
        try:
            chk = mt5.order_check(request)
        except Exception as e:
            chk = {'exception': str(e)}
        # normalize
        try:
            chk_dict = chk._asdict()
        except Exception:
            try:
                chk_dict = dict(chk)
            except Exception:
                chk_dict = {'raw': str(chk)}
        # last_error
        le = mt5.last_error()
        res['diagnostics'].append({'ticket': ticket, 'symbol': symbol, 'request': request, 'order_check': chk_dict, 'last_error': le})
    except Exception as e:
        res['diagnostics'].append({'error': str(e)})

OUT.write_text(json.dumps(res, indent=2), encoding='utf-8')
print('WROTE', OUT)
mt5.shutdown()
