"""Auto-close positions older than a configured age (default 1800s).

This utility is conservative and non-invasive:
- it attempts to read creation timestamp from position attributes (time,
  time_create, time_update) when available;
- if no timestamp is available for a position, the position is skipped (no
  assumptions made);
- for each stale position it builds a close request and uses
  `src.utils.mt5_safe.send_order(..., enforce_cadence=False)` to attempt the
  close (we disable cadence to allow forced closures);
- each attempted close (success or failure) is appended as a NDJSON line to
  `artifacts/live_trading/auto_closes.ndjson` for auditing.

Designed to be run from cron/CI as needed.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

OUT_DIR = Path("artifacts") / "live_trading"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "auto_closes.ndjson"


def _position_created_ts(pos: Any) -> Optional[float]:
    """Try to extract a creation timestamp (epoch seconds) from a position
    object. Return None if not found.
    """
    for attr in ("time", "time_create", "time_update", "time_msc"):
        try:
            v = getattr(pos, attr, None)
            if v is None:
                continue
            # MT5 often returns seconds since epoch (int) or milliseconds
            try:
                v = int(v)
            except Exception:
                continue
            # Heuristic: if value > 1e12 treat as ms
            if v > 1_000_000_000_000:
                return float(v) / 1000.0
            return float(v)
        except Exception:
            continue
    return None


def _append_ndjson(record: Dict[str, Any]) -> None:
    try:
        with OUT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    except Exception:
        # best-effort: don't raise
        pass


def main(max_age_s: int = 1800) -> int:
    """Scan open positions and attempt to close ones older than max_age_s.

    Returns exit code 0 on success (even if no positions found), >0 on
    fatal errors (mt5 import/initialization failures).
    """
    try:
        import MetaTrader5 as mt5
    except Exception as e:
        print("MetaTrader5 import failed:", e)
        return 2

    # attempt to initialize (no credentials will use default terminal)
    try:
        mt5.initialize()
    except Exception:
        # continue even if initialize fails; positions_get may still work in
        # some setups, but treat as fatal if positions_get cannot be called.
        pass

    try:
        positions = mt5.positions_get() or []
    except Exception as e:
        print("positions_get failed:", e)
        return 3

    now = time.time()
    closed = 0
    examined = 0

    # lazy import of safe sender
    try:
        from src.utils.mt5_safe import send_order, Mt5OrderError
    except Exception:
        send_order = None
        Mt5OrderError = Exception

    for p in list(positions):
        examined += 1
        try:
            ticket = int(getattr(p, "ticket", 0))
            symbol = getattr(p, "symbol", None)
            volume = float(getattr(p, "volume", 0.0))
            p_type = int(getattr(p, "type", 0))
        except Exception:
            # skip malformed position
            continue

        created_ts = _position_created_ts(p)
        if created_ts is None:
            # cannot decide age - skip conservatively
            _append_ndjson(
                {
                    "timestamp": datetime.utcfromtimestamp(now).isoformat()
                    + "Z",
                    "ticket": ticket,
                    "symbol": symbol,
                    "note": "no_creation_timestamp",
                }
            )
            continue

        age = now - created_ts
        if age < float(max_age_s):
            # not stale
            continue

        # prepare close request (opposite type)
        try:
            order_type = (
                mt5.ORDER_TYPE_SELL
                if p_type == mt5.ORDER_TYPE_BUY
                else mt5.ORDER_TYPE_BUY
            )
        except Exception:
            # if mt5 constants unavailable, fallback numeric flip
            order_type = 1 if p_type == 0 else 0

        # get current tick price if available
        price = None
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                sell_type = getattr(mt5, "ORDER_TYPE_SELL", 1)
                if order_type == sell_type:
                    price = float(tick.bid)
                else:
                    price = float(tick.ask)
        except Exception:
            price = None

        request: Dict[str, Any] = {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 0),
            "position": ticket,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 50,
            "comment": "auto_close_stale_positions",
        }

        rec: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(now).isoformat() + "Z",
            "ticket": ticket,
            "symbol": symbol,
            "age_s": int(age),
            "max_age_s": int(max_age_s),
            "request": request,
        }

        if send_order is None:
            rec["note"] = "no_safe_sender"
            _append_ndjson(rec)
            continue

        try:
            # Force a close even if cadence would normally block
            result = send_order(request, enforce_cadence=False)
        except Mt5OrderError as e:
            rec["result"] = {"exception": str(e)}
            _append_ndjson(rec)
            continue
        except Exception as e:
            rec["result"] = {"exception": str(e)}
            _append_ndjson(rec)
            continue

        # try to normalize result
        try:
            if hasattr(result, "_asdict"):
                rec["result"] = result._asdict()
            else:
                try:
                    rec["result"] = dict(result)
                except Exception:
                    rec["result"] = repr(result)
        except Exception:
            rec["result"] = {"raw": True}

        _append_ndjson(rec)
        closed += 1

    print(f"Examined positions={examined}, attempted_closes={closed}")
    try:
        mt5.shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--max-age-s", type=int, default=1800, help="Max age in seconds"
    )
    args = ap.parse_args()
    raise SystemExit(main(max_age_s=args.max_age_s))
