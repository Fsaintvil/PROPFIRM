"""
MT5 safe order sender helper.
Provides send_order(request, logger=None, mt5_module=None) which:
 - aligns price/sl/tp to symbol digits/point
 - applies volume preflight: floor to volume_step and enforces min_volume
 - calls mt5.order_send and raises Mt5OrderError on failure
 - returns the raw result on success

This module is intentionally dependency-light and will import MetaTrader5 lazily.
"""
from typing import Optional
import logging
import math


class Mt5OrderError(Exception):
    pass


def _get_mt5(mt5_module=None):
    if mt5_module is not None:
        return mt5_module
    try:
        import MetaTrader5 as mt5
        return mt5
    except Exception:
        return None


def _round_price_by_digits_or_point(v, s_info):
    if v is None:
        return v
    try:
        digits = getattr(s_info, "digits", None)
        point = getattr(s_info, "point", None)
        if digits is not None:
            return float(round(float(v), int(digits)))
        if point is not None and point > 0:
            return float(round(float(v) / float(point)) * float(point))
        return float(v)
    except Exception:
        try:
            return float(v)
        except Exception:
            return v


def send_order(request: dict, logger: Optional[logging.Logger] = None, mt5_module=None,
               enforce_cadence: bool = False):
    """Send an MT5 order with safe preflight adjustments.

    Args:
        request: dict for mt5.order_send
        logger: optional logger
        mt5_module: optional MetaTrader5 module instance for testing

    Returns:
        result: the object returned by mt5.order_send on success

    Raises:
        Mt5OrderError: on failure or invalid parameters
    """
    if logger is None:
        logger = logging.getLogger("mt5_safe")

    mt5 = _get_mt5(mt5_module)
    if mt5 is None:
        raise Mt5OrderError("MetaTrader5 module not available")

    # Best-effort: align price/sl/tp using symbol info
    symbol = request.get("symbol")
    s_info = None
    try:
        if symbol:
            s_info = mt5.symbol_info(symbol)
    except Exception:
        s_info = None

    try:
        if s_info is not None:
            if "price" in request and request.get("price") is not None:
                request["price"] = _round_price_by_digits_or_point(request.get("price"), s_info)
            if "sl" in request and request.get("sl") is not None:
                request["sl"] = _round_price_by_digits_or_point(request.get("sl"), s_info)
            if "tp" in request and request.get("tp") is not None:
                request["tp"] = _round_price_by_digits_or_point(request.get("tp"), s_info)
    except Exception:
        logger.debug("Price/SL/TP alignment failed", exc_info=True)

    # Volume preflight
    try:
        if s_info is not None:
            min_vol = getattr(s_info, "volume_min", None) or getattr(s_info, "min_volume", None)
            vol_step = getattr(s_info, "volume_step", None) or getattr(s_info, "trade_contract_size", None)
        else:
            min_vol = None
            vol_step = None

        if "volume" in request and request.get("volume") is not None and vol_step is not None:
            try:
                requested_vol = float(request.get("volume"))
            except Exception:
                requested_vol = None

            if requested_vol is not None:
                try:
                    n = math.floor(requested_vol / float(vol_step) + 1e-12)
                    effective_vol = float(n) * float(vol_step)
                except Exception:
                    effective_vol = requested_vol

                # decimals based on vol_step
                try:
                    decimals = 0
                    if float(vol_step) < 1:
                        decimals = max(0, -int(math.floor(math.log10(float(vol_step)))))
                except Exception:
                    decimals = 8

                try:
                    effective_vol = round(effective_vol, decimals)
                except Exception:
                    pass

                if effective_vol != requested_vol:
                    logger.info(
                        "Volume ajusté pour %s: requested=%.8f -> effective=%.8f (step=%s, min=%s)",
                        symbol,
                        requested_vol,
                        effective_vol,
                        vol_step,
                        min_vol,
                    )

                if min_vol is not None:
                    try:
                        if float(effective_vol) < float(min_vol):
                            raise Mt5OrderError(
                                f"Requested volume {requested_vol} below symbol min_volume {min_vol} after rounding (effective={effective_vol})"
                            )
                    except Mt5OrderError:
                        raise
                    except Exception:
                        # ignore comparison errors
                        pass

                # apply adjusted volume
                request["volume"] = effective_vol
    except Mt5OrderError:
        raise
    except Exception:
        logger.debug("Volume preflight failed", exc_info=True)

    # Log cleaned request
    try:
        req_copy = dict(request)
        for k in ("price", "sl", "tp", "volume"):
            if k in req_copy:
                try:
                    req_copy[k] = float(req_copy[k]) if req_copy[k] is not None else None
                except Exception:
                    pass
        logger.debug("Sending MT5 order request: %s", req_copy)
    except Exception:
        pass

    # Send
    # Optional cadence enforcement (non-invasive): consult order_cadence util
    if enforce_cadence:
        try:
            # lazy import to avoid circular deps
            from src.utils import order_cadence

            sym = request.get("symbol")
            if sym and not order_cadence.can_send(sym):
                raise Mt5OrderError(f"Cadence blocked for symbol {sym}")
        except Mt5OrderError:
            raise
        except Exception:
            # If cadence util fails, we log and continue (fail-open)
            logger.debug("order_cadence check failed, proceeding", exc_info=True)

    try:
        result = mt5.order_send(request)
    except Exception as e:
        raise Mt5OrderError(f"mt5.order_send exception: {e}")

    if result is None:
        try:
            last = mt5.last_error()
        except Exception:
            last = None
        raise Mt5OrderError(f"Résultat ordre null, last_error={last}")

    try:
        retcode = getattr(result, "retcode", None)
        comment = getattr(result, "comment", None)
        order_id = getattr(result, "order", None)
    except Exception:
        retcode, comment, order_id = None, None, None

    if hasattr(mt5, "TRADE_RETCODE_DONE") and retcode != getattr(mt5, "TRADE_RETCODE_DONE"):
        # log warning then raise
        try:
            logger.warning("MT5 order_send failed: retcode=%s, comment=%s, result=%s", retcode, comment, result)
        except Exception:
            logger.warning("MT5 order_send failed: retcode=%s, comment=%s", retcode, comment)
        raise Mt5OrderError(f"Order send failed, retcode={retcode}, comment={comment}, order={order_id}")

    return result
