from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("structure")


def multi_tf_alignment(d_close: np.ndarray, h4_close: np.ndarray, h1_close: np.ndarray) -> tuple[str, int]:
    d_c = np.asarray(d_close, dtype=float)
    h4_c = np.asarray(h4_close, dtype=float)
    h1_c = np.asarray(h1_close, dtype=float)

    def _trend(ma20: float, ma50: float) -> int:
        diff = (ma20 - ma50) / max(ma50, 0.0001)
        if diff > 0.0005:
            return 1
        if diff < -0.0005:
            return -1
        return 0

    d_trend = 0
    if len(d_c) >= 50:
        d_ma20 = np.mean(d_c[-20:])
        d_ma50 = np.mean(d_c[-50:])
        d_trend = _trend(d_ma20, d_ma50)

    h4_trend = 0
    if len(h4_c) >= 50:
        h4_ma20 = np.mean(h4_c[-20:])
        h4_ma50 = np.mean(h4_c[-50:])
        h4_trend = _trend(h4_ma20, h4_ma50)

    h1_trend = 0
    if len(h1_c) >= 50:
        h1_ma20 = np.mean(h1_c[-20:])
        h1_ma50 = np.mean(h1_c[-50:])
        h1_trend = _trend(h1_ma20, h1_ma50)

    alignment = d_trend + h4_trend + h1_trend

    if alignment >= 2:
        return "BUY", alignment
    elif alignment <= -2:
        return "SELL", alignment
    return "NO_TRADE", alignment


def multi_tf_bias(d_close: np.ndarray, h4_close: np.ndarray, h1_close: np.ndarray) -> dict[str, Any]:
    direction, alignment = multi_tf_alignment(d_close, h4_close, h1_close)
    return {
        "direction": direction,
        "alignment": alignment,
        "conviction": min(1.0, abs(alignment) / 3.0),
    }


def detect_bos(
    h1_high: np.ndarray, h1_low: np.ndarray, h1_close: np.ndarray, window: int = 5
) -> tuple[str | None, float | None, int | None]:
    hh = np.asarray(h1_high, dtype=float)
    ll = np.asarray(h1_low, dtype=float)

    if len(hh) < window * 2 + 2:
        return None, None, None

    recent_high = np.max(hh[-window:])
    prev_high = np.max(hh[-(window * 2) : -window])
    recent_low = np.min(ll[-window:])
    prev_low = np.min(ll[-(window * 2) : -window])

    if recent_low < prev_low and recent_high < prev_high:
        idx = int(np.argmin(ll[-window:]) + len(ll) - window)
        return "BEARISH", round(recent_low, 6), idx

    if recent_high > prev_high and recent_low > prev_low:
        idx = int(np.argmax(hh[-window:]) + len(hh) - window)
        return "BULLISH", round(recent_high, 6), idx

    return None, None, None


def detect_choch(
    h1_high: np.ndarray, h1_low: np.ndarray, h1_close: np.ndarray, window: int = 5
) -> tuple[str | None, float | None, int | None]:
    hh = np.asarray(h1_high, dtype=float)
    ll = np.asarray(h1_low, dtype=float)

    if len(hh) < window * 4:
        return None, None, None

    n = len(hh)
    mid = n // 2
    first_half = hh[:mid]
    first_low_half = ll[:mid]
    second_half = hh[mid:]
    second_low_half = ll[mid:]

    first_trend = first_half[-1] - first_half[0]
    was_up = first_trend > 0
    was_down = first_trend < 0

    first_ll_min = np.min(first_low_half)
    first_hh_max = np.max(first_half)
    second_hh = np.max(second_half)
    second_ll = np.min(second_low_half)

    if was_up and second_ll < first_ll_min:
        idx = int(np.argmin(second_low_half) + mid)
        return "BEARISH", round(second_ll, 6), idx

    if was_down and second_hh > first_hh_max:
        idx = int(np.argmax(second_half) + mid)
        return "BULLISH", round(second_hh, 6), idx

    return None, None, None


def detect_mss(
    h1_high: np.ndarray, h1_low: np.ndarray, h1_close: np.ndarray, window: int = 5
) -> tuple[str | None, float | None, int | None]:
    hh = np.asarray(h1_high, dtype=float)
    ll = np.asarray(h1_low, dtype=float)
    n = len(hh)

    if n < window * 3:
        return None, None, None

    recent_highs = [
        np.max(hh[i : i + window]) for i in range(-window * 3, -window + 1, window) if len(hh[i : i + window]) > 0
    ]
    recent_lows = [
        np.min(ll[i : i + window]) for i in range(-window * 3, -window + 1, window) if len(ll[i : i + window]) > 0
    ]

    if len(recent_highs) < 3 or len(recent_lows) < 3:
        return None, None, None

    if recent_highs[-1] > recent_highs[-2] and recent_lows[-1] <= recent_lows[-2]:
        idx = int(np.argmax(hh[-window:]) + n - window)
        return "BULLISH_MSS", round(recent_highs[-1], 6), idx

    if recent_lows[-1] < recent_lows[-2] and recent_highs[-1] >= recent_highs[-2]:
        idx = int(np.argmin(ll[-window:]) + n - window)
        return "BEARISH_MSS", round(recent_lows[-1], 6), idx

    return None, None, None


def structure_exit_signal(
    position_type: int,
    h1_high: np.ndarray,
    h1_low: np.ndarray,
    h1_close: np.ndarray,
    window: int = 5,
    h1_time: Any = None,
) -> tuple[bool, str | None, int | None]:
    """Retourne (should_exit, reason, candle_idx).
    candle_idx = index dans le tableau de la bougie qui a cassé la structure
    (None si pas de break). Permet de comparer avec le temps d'ouverture."""
    bos_type, level, bos_idx = detect_bos(h1_high, h1_low, h1_close, window)
    if position_type == 0 and bos_type == "BEARISH":
        return True, f"BEARISH_BOS @ {level}", bos_idx
    if position_type == 1 and bos_type == "BULLISH":
        return True, f"BULLISH_BOS @ {level}", bos_idx

    mss_type, mss_level, mss_idx = detect_mss(h1_high, h1_low, h1_close, window)
    if position_type == 0 and mss_type == "BEARISH_MSS":
        return True, f"BEARISH_MSS @ {mss_level}", mss_idx
    if position_type == 1 and mss_type == "BULLISH_MSS":
        return True, f"BULLISH_MSS @ {mss_level}", mss_idx

    choch_type, choch_level, choch_idx = detect_choch(h1_high, h1_low, h1_close, window * 2)
    if position_type == 0 and choch_type == "BEARISH":
        return True, f"BEARISH_CHOCH @ {choch_level}", choch_idx
    if position_type == 1 and choch_type == "BULLISH":
        return True, f"BULLISH_CHOCH @ {choch_level}", choch_idx

    return False, None, None
