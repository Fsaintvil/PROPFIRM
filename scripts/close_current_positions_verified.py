"""
Close current positions with verification:
- For each position: perform order_check(request). If retcode==0 proceed.
- Call order_send(request), immediately capture mt5.last_error(), and re-query positions_get()
- Write artifacts/live_trading/close_after_diagnostics.json with detailed records
"""
import os
import json
import time
from pathlib import Path
import argparse
# keep imports minimal; no extra modules required
from src.utils.order_cadence import can_send, record_send
from src.utils.mt5_safe import send_order, Mt5OrderError

OUT = Path('artifacts') / 'live_trading' / 'close_after_diagnostics.json'
OUT.parent.mkdir(parents=True, exist_ok=True)

# CLI
ap = argparse.ArgumentParser()
ap.add_argument(
    '--place-pending',
    action='store_true',
    help=('If market closed, place pending limit orders to close positions'),
)
ap.add_argument(
    '--pending-ttl-days',
    type=int,
    default=7,
    help=('Expiration for pending orders in days'),
)
args = ap.parse_args()

# Runtime enforcement per OPERATIONAL_RULES:
# require specific env vars set to '1'
REQUIRED_ENVS = [
    'ALLOW_MT5_SEND',
    'AUTO_APPLY',
    'AUTO_DEPLOY',
    'AUTO_LEARN',
    'AUTO_ADAPT',
    'AUTO_ENRICH',
]


def check_required_envs():
    missing = []
    for k in REQUIRED_ENVS:
        v = os.getenv(k)
        if v is None or str(v) != '1':
            missing.append(k)
    if missing:
        msg = (
            "Required environment variables not set to '1': "
            + ', '.join(missing)
            + "\nAborting per operational rules."
        )
        print(msg)
        return False
    return True


# Enforce before making any sends
if not check_required_envs():
    raise SystemExit(2)

# load creds if available
creds = Path(__file__).parent.parent / 'config' / 'mt5_credentials.env'
if creds.exists():
    for line in creds.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('mt5 import failed', e)
    raise SystemExit(1)

# init
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

results = {
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'records': [],
}

positions = mt5.positions_get()
positions = list(positions) if positions is not None else []

for p in positions:
    rec = {}
    try:
        ticket = int(getattr(p, 'ticket', 0))
        symbol = getattr(p, 'symbol', None)
        volume = float(getattr(p, 'volume', 0.0))
        p_type = int(getattr(p, 'type', 0))
        order_type = (
            mt5.ORDER_TYPE_SELL
            if p_type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            rec.update({'ticket': ticket, 'symbol': symbol, 'note': 'no_tick'})
            results['records'].append(rec)
            continue
        if order_type == mt5.ORDER_TYPE_SELL:
            price = float(tick.bid)
        else:
            price = float(tick.ask)

        # adjust volume to symbol step/min
        info = mt5.symbol_info(symbol)
        try:
            step = (
                float(info.volume_step)
                if getattr(info, 'volume_step', None)
                else 0.01
            )
            vmin = (
                float(info.volume_min)
                if getattr(info, 'volume_min', None)
                else 0.01
            )
            steps = int(volume / step)
            vol_adj = max(vmin, steps * step)
            if vol_adj <= 0:
                vol_adj = vmin
        except Exception:
            vol_adj = volume

        # Use an empty comment to avoid broker-side comment validation errors
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'position': ticket,
            'symbol': symbol,
            'volume': vol_adj,
            'type': order_type,
            'price': price,
            'deviation': 20,
            'magic': 0,
            'comment': '',
        }

        rec['ticket'] = ticket
        rec['symbol'] = symbol
        rec['volume_requested'] = volume
        rec['volume_sent'] = vol_adj
        rec['request'] = request

    # cadence check: do not flood a symbol; enforce OPERATIONAL_RULES
    # cadence
        try:
            if not can_send(symbol):
                rec['note'] = 'cadence_blocked'
                results['records'].append(rec)
                continue
        except Exception:
            # precaution: if cadence module fails, allow processing (fail-open)
            pass

        # order_check
        try:
            chk = mt5.order_check(request)
        except Exception as e:
            rec['order_check'] = {'exception': str(e)}
            results['records'].append(rec)
            continue

        # normalize check
        try:
            chk_dict = chk._asdict()
        except Exception:
            try:
                chk_dict = dict(chk)
            except Exception:
                chk_dict = {'raw': str(chk)}
        rec['order_check'] = chk_dict

        # if check indicates non-zero retcode, skip
        if isinstance(chk_dict, dict):
            chk_ret = chk_dict.get('retcode')
        else:
            chk_ret = None
        if chk_ret and int(chk_ret) != 0:
            rec['note'] = 'order_check_failed'
            results['records'].append(rec)
            continue

        # send via safe wrapper with cadence enforcement
        try:
            send = send_order(request, enforce_cadence=True)
        except Mt5OrderError as e:
            # record the error like the previous behavior
            rec['order_send'] = {'exception': str(e)}
            results['records'].append(rec)
            continue
        except Exception as e:
            rec['order_send'] = {'exception': str(e)}
            results['records'].append(rec)
            continue

        # capture last_error immediately
        last_err = mt5.last_error()
        rec['last_error_after_send'] = last_err

        # try to inspect send
        try:
            send_dict = send._asdict()
        except Exception:
            try:
                send_dict = dict(send)
            except Exception:
                send_dict = {'repr': repr(send)}
        rec['order_send'] = send_dict

    # if the send resulted in a deal (closed), record the send time
    # for cadence
        try:
            deal_val = (
                float(send_dict.get('deal', 0))
                if isinstance(send_dict, dict)
                else 0.0
            )
            if deal_val and deal_val > 0:
                try:
                    record_send(symbol)
                except Exception:
                    pass
        except Exception:
            pass

    # If market closed and user asked to place pending orders, try to create
    # pending orders that will execute later when market opens
        try:
            send_retcode = None
            try:
                send_retcode = (
                    send._asdict().get('retcode')
                    if hasattr(send, '_asdict')
                    else getattr(send, 'retcode', None)
                )
            except Exception:
                try:
                    send_retcode = dict(send).get('retcode')
                except Exception:
                    send_retcode = None
        except Exception:
            send_retcode = None

        if (
            args.place_pending
            and send_retcode is not None
            and int(send_retcode) == 10018
        ):
            # Market closed; attempt to place a pending limit order to close
            # when market opens
            try:
                import time as _time

                expiration_ts = int(
                    _time.time() + args.pending_ttl_days * 24 * 3600
                )
                # choose pending type based on closing order_type
                if order_type == mt5.ORDER_TYPE_SELL:
                    pending_type = mt5.ORDER_TYPE_SELL_LIMIT
                else:
                    pending_type = mt5.ORDER_TYPE_BUY_LIMIT

                pending_req = {
                    'action': mt5.TRADE_ACTION_PENDING,
                    'symbol': symbol,
                    'volume': vol_adj,
                    'type': pending_type,
                    'price': price,
                    'deviation': 20,
                    'magic': 0,
                    'comment': '',
                    'expiration': expiration_ts,
                }
                rec['pending_request'] = pending_req
                try:
                    pending_send = send_order(
                        pending_req, enforce_cadence=False
                    )
                except Mt5OrderError as e:
                    rec['pending_send'] = {'exception': str(e)}
                except Exception as e:
                    rec['pending_send'] = {'exception': str(e)}
                else:
                    try:
                        rec['pending_send'] = pending_send._asdict()
                    except Exception:
                        try:
                            rec['pending_send'] = dict(pending_send)
                        except Exception:
                            rec['pending_send'] = {'repr': repr(pending_send)}
            except Exception as e:
                rec['pending_error'] = str(e)

        # wait a bit and re-check positions
        time.sleep(0.7)
        cur_pos = mt5.positions_get()
        cur_tickets = {int(getattr(x, 'ticket', 0)) for x in (cur_pos or [])}
        rec['still_open'] = ticket in cur_tickets

        results['records'].append(rec)

    except Exception as exc:
        results['records'].append({'error': str(exc)})

# final remaining count
try:
    rem = mt5.positions_get()
    results['remaining_positions'] = len(rem) if rem is not None else 0
except Exception:
    results['remaining_positions'] = None

OUT.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
print('WROTE', OUT)
mt5.shutdown()
