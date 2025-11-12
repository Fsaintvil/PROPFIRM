#!/usr/bin/env python3
"""
Script temporaire : ferme toutes les positions via MetaTrader5 directement (fallback)
Écrit artifacts/live_trading/close_all_positions_result_manual.json
"""
import os, json, time
from pathlib import Path

# load creds
creds_path = Path(__file__).parent / "config" / "mt5_credentials.env"
if creds_path.exists():
    for line in creds_path.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k,v = line.split('=',1)
        os.environ.setdefault(k.strip(), v.strip())

out_dir = Path('artifacts') / 'live_trading'
out_dir.mkdir(parents=True, exist_ok=True)
outfile = out_dir / 'close_all_positions_result_manual.json'
report = {'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'errors': [], 'closed': [], 'remaining_positions': None}

try:
    import MetaTrader5 as mt5
except Exception as e:
    report['errors'].append(f'mt5_import_failed: {e}')
    print(json.dumps(report, indent=2))
    raise SystemExit(1)

# initialize
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

try:
    ok = mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()
    if not ok:
        report['errors'].append('mt5_initialize_failed')
        Path(outfile).write_text(json.dumps(report, indent=2), encoding='utf-8')
        raise SystemExit(1)
except Exception as e:
    report['errors'].append(f'mt5_initialize_exception: {e}')
    Path(outfile).write_text(json.dumps(report, indent=2), encoding='utf-8')
    raise SystemExit(1)

# fetch positions
try:
    positions = mt5.positions_get()
    if positions is None:
        positions = []
    else:
        positions = list(positions)
except Exception as e:
    report['errors'].append(f'positions_get_failed: {e}')
    positions = []

# helper
def get_price_for_closing(symbol, action):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    if action == 'close_buy':
        return float(tick.bid)
    return float(tick.ask)


def adjust_volume(symbol, volume):
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return volume
        step = float(getattr(info,'volume_step',0.01) or 0.01)
        vmin = float(getattr(info,'volume_min',0.01) or 0.01)
        steps = int(volume/step)
        vol_adj = max(vmin, steps*step)
        if vol_adj<=0:
            vol_adj = vmin
        return round(vol_adj,8)
    except Exception:
        return volume

for pos in positions:
    try:
        ticket = int(getattr(pos,'ticket',0))
        symbol = getattr(pos,'symbol',None)
        volume = float(getattr(pos,'volume',0.0))
        type_pos = int(getattr(pos,'type',0))
        if type_pos == mt5.ORDER_TYPE_BUY:
            action='close_buy'
            order_type = mt5.ORDER_TYPE_SELL
        else:
            action='close_sell'
            order_type = mt5.ORDER_TYPE_BUY
        price = get_price_for_closing(symbol, action)
        if price is None:
            report['errors'].append(f'no_price_for_symbol:{symbol}')
            continue
        vol_to_send = adjust_volume(symbol, volume)
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'position': ticket,
            'symbol': symbol,
            'volume': vol_to_send,
            'type': order_type,
            'price': price,
            'deviation': 50,
            'magic': 0,
            'comment': 'tmp_close_all_direct.py'
        }
        attempt = 0
        result = None
        while attempt < 5:
            attempt += 1
            try:
                result = mt5.order_send(request)
            except Exception as e:
                result = {'exception': str(e)}
            # try to interpret result
            try:
                deal = int(getattr(result,'deal',0) or 0)
            except Exception:
                deal = 0
            if deal>0:
                break
            time.sleep(0.5+attempt*0.2)
        try:
            res_dict = result._asdict()
        except Exception:
            try:
                res_dict = dict(result)
            except Exception:
                res_dict = {'raw': str(result)}
        closed = {'ticket': ticket, 'symbol': symbol, 'volume': volume, 'volume_sent': vol_to_send, 'attempts': attempt, 'order_result': res_dict}
        report['closed'].append(closed)
    except Exception as e:
        report['errors'].append(f'exception:{e}')

# final
try:
    rem = mt5.positions_get()
    report['remaining_positions'] = len(rem) if rem is not None else 0
except Exception:
    report['remaining_positions'] = None

outfile.write_text(json.dumps(report, indent=2), encoding='utf-8')
print('Wrote', outfile)

try:
    mt5.shutdown()
except Exception:
    pass
