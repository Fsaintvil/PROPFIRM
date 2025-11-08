"""
Ferme toutes les positions ouvertes via MetaTrader5.
Usage: python scripts/close_all_positions.py
Le script lit `config/mt5_credentials.env` s'il existe pour charger MT5_ACCOUNT/MT5_PASSWORD/MT5_SERVER.
Sortie: artifacts/live_trading/close_all_positions_result.json
"""
import os
import json
import time

# Load .env-style credentials if available
creds_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'mt5_credentials.env')
creds_path = os.path.normpath(creds_path)
if os.path.exists(creds_path):
    try:
        with open(creds_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

report = {
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'connected': False,
    'errors': [],
    'closed': [],
    'remaining_positions': None,
}

try:
    import MetaTrader5 as mt5
    from src.utils.mt5_safe import send_order, Mt5OrderError
except Exception as e:
    report['errors'].append(f'cannot_import_mt5: {e}')
    print(json.dumps(report, indent=2))
    raise SystemExit(1)

# Initialize
login = os.getenv('MT5_LOGIN') or os.getenv('MT5_ACCOUNT')
password = os.getenv('MT5_PASSWORD') or os.getenv('MT5_PWD')
server = os.getenv('MT5_SERVER')

init_kwargs = {}
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
    report['connected'] = bool(ok)
    if not ok:
        report['errors'].append('mt5.initialize_failed')
except Exception as e:
    report['errors'].append(f'mt5_initialize_exception: {e}')

if not report['connected']:
    print(json.dumps(report, indent=2))
    raise SystemExit(1)

# Fetch positions
try:
    positions = mt5.positions_get()
    if positions is None:
        positions = []
    else:
        positions = list(positions)
except Exception as e:
    report['errors'].append(f'positions_get_failed: {e}')
    positions = []

# Helper to get tick
def get_price_for_closing(symbol, action):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    # action 'close_buy' means we must SELL to close a BUY position -> use bid
    # action 'close_sell' means we must BUY to close a SELL position -> use ask
    if action == 'close_buy':
        return float(tick.bid)
    else:
        return float(tick.ask)


def adjust_volume(symbol, volume):
    """Adjust volume to broker's allowed step and minimum for the symbol."""
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return volume
        step = float(info.volume_step) if info.volume_step else 0.01
        vmin = float(info.volume_min) if info.volume_min else 0.01
        # floor to step
        steps = int(volume / step)
        vol_adj = max(vmin, steps * step)
        # avoid returning 0.0
        if vol_adj <= 0:
            vol_adj = vmin
        return round(vol_adj, 8)
    except Exception:
        return volume

# Close each position
for pos in positions:
    try:
        pos_dict = pos._asdict() if hasattr(pos, '_asdict') else dict(pos)
        ticket = int(pos.ticket)
        symbol = pos.symbol
        volume = float(pos.volume)
        type_pos = pos.type  # 0=buy,1=sell
        if type_pos == mt5.ORDER_TYPE_BUY or type_pos == 0:
            # close by selling
            action = 'close_buy'
            order_type = mt5.ORDER_TYPE_SELL
        else:
            action = 'close_sell'
            order_type = mt5.ORDER_TYPE_BUY

        price = get_price_for_closing(symbol, action)
        if price is None:
            report['errors'].append(f'no_price_for_symbol:{symbol}')
            continue

        # adjust volume to broker limits
        vol_to_send = adjust_volume(symbol, volume)

        # include the position id in the request so broker closes that position
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'position': ticket,
            'symbol': symbol,
            'volume': vol_to_send,
            'type': order_type,
            'price': price,
            'deviation': 20,
            'magic': 0,
            'comment': 'close_all_positions.py',
        }

        # try several times if necessary
        result = None
        attempt = 0
        while attempt < 3:
            attempt += 1
            try:
                result = send_order(request, enforce_cadence=True)
            except Mt5OrderError as e:
                # mimic previous behavior: keep trying unless it's a fatal
                result = {'exception': str(e)}
            except Exception as e:
                result = {'exception': str(e)}
            # if deal > 0 we succeeded
            try:
                deal = int(getattr(result, 'deal', 0) or 0)
            except Exception:
                deal = 0
            if deal and deal > 0:
                break
            # small backoff
            time.sleep(1 + attempt * 0.5)
        # result may be tuple-like
        res_dict = None
        try:
            res_dict = result._asdict()
        except Exception:
            try:
                res_dict = dict(result)
            except Exception:
                res_dict = {'raw': str(result)}

        closed = {
            'ticket': ticket,
            'symbol': symbol,
            'volume': volume,
            'requested_price': price,
            'volume_sent': vol_to_send,
            'attempts': attempt,
            'order_result': res_dict,
        }
        report['closed'].append(closed)
        print(f"Closed position ticket={ticket} symbol={symbol} vol={volume} -> result={res_dict}")

    except Exception as e:
        report['errors'].append(f'exception_closing_pos:{str(e)}')

# Final check of remaining positions
try:
    remaining = mt5.positions_get()
    report['remaining_positions'] = len(remaining) if remaining is not None else 0
except Exception as e:
    report['errors'].append(f'positions_get_after_failed:{e}')

# Save report
out_dir = os.path.join('artifacts', 'live_trading')
os.makedirs(out_dir, exist_ok=True)
outf = os.path.join(out_dir, 'close_all_positions_result.json')
with open(outf, 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, default=str)

print(json.dumps(report, indent=2))

# Shutdown connection
try:
    mt5.shutdown()
except Exception:
    pass
