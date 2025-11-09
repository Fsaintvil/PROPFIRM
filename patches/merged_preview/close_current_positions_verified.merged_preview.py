# Merged preview for prefix: close
# Generated from 3 files

################################################################################
# FROM: scripts\close_all_positions.py
################################################################################
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
creds_path = os.path.join(
    os.path.dirname(__file__), "..", "config", "mt5_credentials.env"
)
creds_path = os.path.normpath(creds_path)
if os.path.exists(creds_path):
    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "connected": False,
    "errors": [],
    "closed": [],
    "remaining_positions": None,
}

try:
    import MetaTrader5 as mt5
    from src.utils.mt5_safe import send_order, Mt5OrderError
except Exception as e:
    report["errors"].append(f"cannot_import_mt5: {e}")
    print(json.dumps(report, indent=2))
    raise SystemExit(1)

# Initialize
login = os.getenv("MT5_LOGIN") or os.getenv("MT5_ACCOUNT")
password = os.getenv("MT5_PASSWORD") or os.getenv("MT5_PWD")
server = os.getenv("MT5_SERVER")

init_kwargs = {}
if login:
    try:
        init_kwargs["login"] = int(login)
    except Exception:
        init_kwargs["login"] = login
if password:
    init_kwargs["password"] = password
if server:
    init_kwargs["server"] = server

try:
    ok = mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()
    report["connected"] = bool(ok)
    if not ok:
        report["errors"].append("mt5.initialize_failed")
except Exception as e:
    report["errors"].append(f"mt5_initialize_exception: {e}")

if not report["connected"]:
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
    report["errors"].append(f"positions_get_failed: {e}")
    positions = []


# Helper to get tick
def get_price_for_closing(symbol, action):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    # action 'close_buy' means we must SELL to close a BUY position -> use bid
    # action 'close_sell' means we must BUY to close a SELL position -> use ask
    if action == "close_buy":
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
        pos_dict = pos._asdict() if hasattr(pos, "_asdict") else dict(pos)
        ticket = int(pos.ticket)
        symbol = pos.symbol
        volume = float(pos.volume)
        type_pos = pos.type  # 0=buy,1=sell
        if type_pos == mt5.ORDER_TYPE_BUY or type_pos == 0:
            # close by selling
            action = "close_buy"
            order_type = mt5.ORDER_TYPE_SELL
        else:
            action = "close_sell"
            order_type = mt5.ORDER_TYPE_BUY

        price = get_price_for_closing(symbol, action)
        if price is None:
            report["errors"].append(f"no_price_for_symbol:{symbol}")
            continue

        # adjust volume to broker limits
        vol_to_send = adjust_volume(symbol, volume)

        # include the position id in the request so broker closes that position
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": symbol,
            "volume": vol_to_send,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 0,
            "comment": "close_all_positions.py",
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
                result = {"exception": str(e)}
            except Exception as e:
                result = {"exception": str(e)}
            # if deal > 0 we succeeded
            try:
                deal = int(getattr(result, "deal", 0) or 0)
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
                res_dict = {"raw": str(result)}

        closed = {
            "ticket": ticket,
            "symbol": symbol,
            "volume": volume,
            "requested_price": price,
            "volume_sent": vol_to_send,
            "attempts": attempt,
            "order_result": res_dict,
        }
        report["closed"].append(closed)
        print(
            f"Closed position ticket={ticket} symbol={symbol} vol={volume} -> result={res_dict}"
        )

    except Exception as e:
        report["errors"].append(f"exception_closing_pos:{str(e)}")

# Final check of remaining positions
try:
    remaining = mt5.positions_get()
    report["remaining_positions"] = len(remaining) if remaining is not None else 0
except Exception as e:
    report["errors"].append(f"positions_get_after_failed:{e}")

# Save report
out_dir = os.path.join("artifacts", "live_trading")
os.makedirs(out_dir, exist_ok=True)
outf = os.path.join(out_dir, "close_all_positions_result.json")
with open(outf, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)

print(json.dumps(report, indent=2))

# Shutdown connection
try:
    mt5.shutdown()
except Exception:
    pass


################################################################################
# FROM: scripts\close_current_positions_safe.py
################################################################################
"""
Close all currently open positions safely:
- Iterate mt5.positions_get()
- For each position build a close request (opposite type)
- Call mt5.order_check(request), record response
- If order_check OK (no 'Invalid' in comment), call _mt5_send_safe(request)
- Write artifacts/live_trading/close_current_positions_result.json
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

OUT_DIR = Path("artifacts") / "live_trading"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# load creds from config if present
creds_path = Path(__file__).parent.parent / "config" / "mt5_credentials.env"
if creds_path.exists():
    for line in creds_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

try:
    import MetaTrader5 as mt5
except Exception as e:
    print("MT5_IMPORT_FAILED:", e)
    raise SystemExit(1)

from src.utils.mt5_safe import send_order, Mt5OrderError

init_kwargs = {}
login = os.getenv("MT5_LOGIN") or os.getenv("MT5_ACCOUNT")
password = os.getenv("MT5_PASSWORD") or os.getenv("MT5_PWD")
server = os.getenv("MT5_SERVER")
if login:
    try:
        init_kwargs["login"] = int(login)
    except Exception:
        init_kwargs["login"] = login
if password:
    init_kwargs["password"] = password
if server:
    init_kwargs["server"] = server

ok = mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()
if not ok:
    print("MT5_INITIALIZE_FAILED")

# helper to adjust volume


def adjust_volume(symbol, volume):
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return volume
        step = float(info.volume_step) if getattr(info, "volume_step", None) else 0.01
        vmin = float(info.volume_min) if getattr(info, "volume_min", None) else 0.01
        steps = int(volume / step)
        vol_adj = max(vmin, steps * step)
        if vol_adj <= 0:
            vol_adj = vmin
        return round(vol_adj, 8)
    except Exception:
        return volume


report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "results": [],
}

positions = mt5.positions_get()
if not positions:
    print("No positions found")

for p in list(positions or []):
    try:
        ticket = int(getattr(p, "ticket", 0))
        symbol = getattr(p, "symbol", None)
        volume = float(getattr(p, "volume", 0.0))
        type_pos = int(getattr(p, "type", 0))
        order_type = (
            mt5.ORDER_TYPE_SELL
            if type_pos == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            report["results"].append(
                {"ticket": ticket, "symbol": symbol, "note": "no_tick"}
            )
            continue
        price = (
            float(tick.bid) if order_type == mt5.ORDER_TYPE_SELL else float(tick.ask)
        )

        vol = adjust_volume(symbol, volume)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": symbol,
            "volume": vol,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 0,
            "comment": "close_current_positions_safe.py",
        }

        # order_check
        try:
            check = mt5.order_check(request)
        except Exception as e:
            report["results"].append(
                {
                    "ticket": ticket,
                    "symbol": symbol,
                    "note": "order_check_exception",
                    "error": str(e),
                }
            )
            continue

        try:
            check_dict = check._asdict()
        except Exception:
            try:
                check_dict = dict(check)
            except Exception:
                check_dict = {"raw": str(check)}

        rec = {
            "ticket": ticket,
            "symbol": symbol,
            "request": request,
            "order_check": check_dict,
        }

        comment = (
            check_dict.get("comment")
            if isinstance(check_dict, dict)
            else str(check_dict)
        )
        if comment and "Invalid" in str(comment):
            rec["note"] = "order_check_invalid"
            report["results"].append(rec)
            continue

        # send via safe wrapper with cadence enforcement
        try:
            send = (
                _mt5_send_safe(request)
                if _mt5_send_safe
                else send_order(request, enforce_cadence=True)
            )
        except Mt5OrderError as e:
            rec["send_exception"] = str(e)
            report["results"].append(rec)
            continue
        except Exception as e:
            rec["send_exception"] = str(e)
            report["results"].append(rec)
            continue

        try:
            send_dict = send._asdict()
        except Exception:
            try:
                send_dict = dict(send)
            except Exception:
                send_dict = {"raw": str(send)}

        rec["order_send"] = send_dict
        report["results"].append(rec)

        # small sleep
        time.sleep(0.5)

    except Exception as exc:
        report["results"].append(
            {"ticket": getattr(p, "ticket", None), "error": str(exc)}
        )

# final
try:
    rem = mt5.positions_get()
    report["remaining_positions"] = len(rem) if rem is not None else 0
except Exception:
    report["remaining_positions"] = None

outf = OUT_DIR / "close_current_positions_result.json"
outf.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("Wrote", outf)
mt5.shutdown()


################################################################################
# FROM: scripts\close_current_positions_verified.py
################################################################################
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


# End of merged preview
