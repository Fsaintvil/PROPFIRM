"""Utilities to perform conservative, non-invasive broker preflight checks.

Functions:
- normalize_price(price, digits)
- clamp_sl_tp(current_price, stop_loss, take_profit, price_digits, broker_min_stoplevel, point_size, safety_margin_points=2)
- validate_volume(volume, min_volume, volume_step)

These are intentionally small, well-tested helpers used by the engine
in dry-run and preflight phases. They must never raise for normal inputs.
"""
from typing import Tuple, Optional


def normalize_price(price: float, digits: int) -> float:
    """Round a price to the broker's digits in a stable way."""
    try:
        if price is None:
            return 0.0
        fmt = "{:.%df}" % digits
        return float(fmt.format(float(price)))
    except Exception:
        # Fallback safe
        try:
            return round(float(price), digits)
        except Exception:
            return 0.0


def clamp_sl_tp(
    current_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    price_digits: int,
    broker_min_stoplevel: int,
    point_size: float,
    safety_margin_points: int = 2,
) -> Tuple[Optional[float], Optional[float], bool]:
    """Clamp SL/TP to broker minimum stoplevel.

    Returns (new_sl, new_tp, changed)
    - If stop_loss or take_profit is None, returns them unchanged (None).
    - Ensures distance between entry and SL/TP is >= broker_min_stoplevel * point_size.
    - Rounds results to given digits.
    """
    changed = False
    try:
        min_distance = max(1, int(broker_min_stoplevel) + int(safety_margin_points)) * float(point_size)

        def _clamp_one(px: Optional[float], side: str) -> Optional[float]:
            nonlocal changed
            try:
                if px is None:
                    return None
                dist = abs(current_price - float(px))
                if dist < min_distance:
                    # push it away from the entry price to the min distance
                    if side == "below":
                        # SL for buy -> must be below entry
                        new_px = current_price - min_distance
                    else:
                        # TP for buy -> must be above entry
                        new_px = current_price + min_distance
                    changed = True
                    return normalize_price(new_px, price_digits)
                return normalize_price(px, price_digits)
            except Exception:
                return px

        # For safety, we don't try to infer direction here. We only ensure
        # the absolute distances are reasonable. Callers must ensure correct sides.
        new_sl = _clamp_one(stop_loss, "below")
        new_tp = _clamp_one(take_profit, "above")

        return new_sl, new_tp, changed
    except Exception:
        try:
            return stop_loss, take_profit, False
        except Exception:
            return None, None, False


def validate_volume(volume: float, min_volume: float, volume_step: float) -> Tuple[bool, Optional[float]]:
    """Validate and adjust volume.

    Returns (ok, adjusted_volume). ok=True if input volume is valid; adjusted_volume
    is None if no reasonable adjustment available.
    """
    try:
        vol = float(volume)
        min_v = float(min_volume)
        step = float(volume_step) if volume_step and volume_step > 0 else 0.01

        if vol < min_v:
            # suggest minimum
            return False, float(min_v)

        # Round to nearest step
        steps = round((vol - min_v) / step)
        adj = min_v + steps * step
        # Ensure at least min_v
        if adj < min_v:
            adj = min_v

        return abs(adj - vol) < 1e-9, float(adj)
    except Exception:
        return False, None
