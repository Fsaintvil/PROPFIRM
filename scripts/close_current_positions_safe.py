"""
# ruff: noqa: E402
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
