from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("market_structure")


def swing_points(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3) -> np.ndarray:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(h)
    swings = np.zeros(n, dtype=int)
    for i in range(left, n - right):
        if all(h[i] > h[i - left : i]) and all(h[i] >= h[i + 1 : i + right + 1]):
            swings[i] = 1
        if all(lo[i] < lo[i - left : i]) and all(lo[i] <= lo[i + 1 : i + right + 1]):
            swings[i] = -1
    return swings


def higher_highs(high: np.ndarray, swings: np.ndarray) -> tuple[bool, list]:
    h = np.asarray(high, dtype=float)
    highs = [(i, h[i]) for i in range(len(swings)) if swings[i] == 1]
    if len(highs) < 3:
        return False, []
    return all(highs[j][1] > highs[j - 1][1] for j in range(1, len(highs))), highs


def lower_lows(low: np.ndarray, swings: np.ndarray) -> tuple[bool, list]:
    lo = np.asarray(low, dtype=float)
    lows = [(i, lo[i]) for i in range(len(swings)) if swings[i] == -1]
    if len(lows) < 3:
        return False, []
    return all(lows[j][1] < lows[j - 1][1] for j in range(1, len(lows))), lows


def label_swing_structure(high: np.ndarray, low: np.ndarray, swings: np.ndarray) -> dict[str, Any]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    high_idxs = [(i, h[i]) for i in range(len(swings)) if swings[i] == 1]
    low_idxs = [(i, lo[i]) for i in range(len(swings)) if swings[i] == -1]

    labels = {"highs": [], "lows": [], "structure": "neutral", "trend_score": 0}

    for j in range(1, len(high_idxs)):
        prev_h = high_idxs[j - 1][1]
        curr_h = high_idxs[j][1]
        if curr_h > prev_h:
            labels["highs"].append({"idx": high_idxs[j][0], "level": curr_h, "label": "HH"})
        else:
            labels["highs"].append({"idx": high_idxs[j][0], "level": curr_h, "label": "LH"})

    for j in range(1, len(low_idxs)):
        prev_l = low_idxs[j - 1][1]
        curr_l = low_idxs[j][1]
        if curr_l < prev_l:
            labels["lows"].append({"idx": low_idxs[j][0], "level": curr_l, "label": "LL"})
        else:
            labels["lows"].append({"idx": low_idxs[j][0], "level": curr_l, "label": "HL"})

    recent_high_labels = [x["label"] for x in labels["highs"][-4:]] if labels["highs"] else []
    recent_low_labels = [x["label"] for x in labels["lows"][-4:]] if labels["lows"] else []

    # Uptrend: HH + HL sequence
    if "HH" in recent_high_labels and "HL" in recent_low_labels:
        labels["structure"] = "bullish"
        labels["trend_score"] = 1
    # Downtrend: LH + LL sequence
    elif "LH" in recent_high_labels and "LL" in recent_low_labels:
        labels["structure"] = "bearish"
        labels["trend_score"] = -1
    else:
        labels["structure"] = "ranging"
        labels["trend_score"] = 0

    return labels


def break_of_structure(high: np.ndarray, low: np.ndarray, swings: np.ndarray) -> dict[str, Any]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    last_swing_high = None
    last_swing_low = None
    for i in range(len(swings) - 1, -1, -1):
        if swings[i] == 1 and last_swing_high is None:
            last_swing_high = (i, h[i])
        if swings[i] == -1 and last_swing_low is None:
            last_swing_low = (i, lo[i])
        if last_swing_high is not None and last_swing_low is not None:
            break

    # Also look one more level deeper for confirmed BOS
    prev_swing_high = None
    prev_swing_low = None
    for i in range(len(swings) - 1, -1, -1):
        if swings[i] == 1 and prev_swing_high is None and (last_swing_high is None or i < last_swing_high[0]):
            prev_swing_high = (i, h[i])
            break
    for i in range(len(swings) - 1, -1, -1):
        if swings[i] == -1 and prev_swing_low is None and (last_swing_low is None or i < last_swing_low[0]):
            prev_swing_low = (i, lo[i])
            break

    result = {
        "bullish_bos": False,
        "bearish_bos": False,
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
        "prev_swing_high": prev_swing_high,
        "prev_swing_low": prev_swing_low,
    }
    if last_swing_high and h[-1] > last_swing_high[1]:
        result["bullish_bos"] = True
    if last_swing_low and lo[-1] < last_swing_low[1]:
        result["bearish_bos"] = True
    return result


def change_of_character(swings: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict[str, Any]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    highs = [(i, h[i]) for i in range(len(swings)) if swings[i] == 1]
    lows = [(i, lo[i]) for i in range(len(swings)) if swings[i] == -1]

    result = {"bullish_choch": False, "bearish_choch": False, "structure": "neutral"}

    if len(highs) >= 3 and len(lows) >= 3:
        # Bullish CHOCH: was in downtrend (LH/LL), now breaks above last HH
        prev_highs_down = all(highs[j][1] <= highs[j - 1][1] for j in range(1, len(highs)))
        if prev_highs_down and h[-1] > highs[-1][1]:
            result["bullish_choch"] = True
            result["structure"] = "bullish"

        # Bearish CHOCH: was in uptrend (HH/HL), now breaks below last LL
        prev_lows_up = all(lows[j][1] >= lows[j - 1][1] for j in range(1, len(lows)))
        if prev_lows_up and lo[-1] < lows[-1][1]:
            result["bearish_choch"] = True
            result["structure"] = "bearish"

    return result


def find_order_blocks(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, swings: np.ndarray, lookback: int = 50
) -> list[dict[str, Any]]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(c)
    order_blocks = []
    start = max(0, n - lookback)

    for i in range(start + 1, n - 2):
        body = abs(c[i] - c[i - 1]) if i > 0 else h[i] - lo[i]
        candle_range = h[i] - lo[i]
        if candle_range <= 0:
            continue
        wick_ratio = (candle_range - body) / candle_range

        if swings[i] == 1:
            if wick_ratio < 0.4:
                ob_high = h[i]
                ob_low = lo[i]
                mitigated = c[-1] <= ob_low
                order_blocks.append(
                    {
                        "type": "bearish",
                        "index": i,
                        "high": ob_high,
                        "low": ob_low,
                        "is_mitigated": mitigated,
                        "strength": 1 - wick_ratio,
                        "candle_body": body,
                    }
                )
        elif swings[i] == -1 and wick_ratio < 0.4:
            ob_high = h[i]
            ob_low = lo[i]
            mitigated = c[-1] >= ob_high
            order_blocks.append(
                {
                    "type": "bullish",
                    "index": i,
                    "high": ob_high,
                    "low": ob_low,
                    "is_mitigated": mitigated,
                    "strength": 1 - wick_ratio,
                    "candle_body": body,
                }
            )

    return order_blocks


def find_fvg(
    high: np.ndarray, low: np.ndarray, lookback: int = 50, threshold_pct: float = 0.0001
) -> list[dict[str, Any]]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(h)
    fvgs = []
    start = max(0, n - lookback)
    for i in range(start, n - 2):
        if h[i] < lo[i + 2]:
            fvg_high = lo[i + 2]
            fvg_low = h[i]
            if fvg_high - fvg_low > threshold_pct:
                filled_lo = lo[i + 1]
                filled_hi = h[i + 1]
                is_mitigated = filled_lo <= fvg_low and filled_hi >= fvg_high
                half_filled = (filled_lo <= fvg_low < filled_hi) or (filled_lo < fvg_high <= filled_hi)
                fvgs.append(
                    {
                        "type": "bullish",
                        "index": i,
                        "high": fvg_high,
                        "low": fvg_low,
                        "is_mitigated": is_mitigated,
                        "half_filled": half_filled and not is_mitigated,
                        "size_pct": (fvg_high - fvg_low) / max(fvg_low, 0.0001),
                    }
                )
        elif lo[i] > h[i + 2]:
            fvg_low = h[i + 2]
            fvg_high = lo[i]
            if fvg_high - fvg_low > threshold_pct:
                filled_lo = lo[i + 1]
                filled_hi = h[i + 1]
                is_mitigated = filled_lo <= fvg_low and filled_hi >= fvg_high
                half_filled = (filled_lo <= fvg_low < filled_hi) or (filled_lo < fvg_high <= filled_hi)
                fvgs.append(
                    {
                        "type": "bearish",
                        "index": i,
                        "high": fvg_high,
                        "low": fvg_low,
                        "is_mitigated": is_mitigated,
                        "half_filled": half_filled and not is_mitigated,
                        "size_pct": (fvg_high - fvg_low) / max(fvg_low, 0.0001),
                    }
                )
    return fvgs


def find_liquidity_sweeps(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, swings: np.ndarray, lookback: int = 50
) -> list[dict[str, Any]]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(h)
    sweeps = []
    start = max(0, n - lookback)

    for i in range(start, n):
        if swings[i] == 1 and i < n - 3:
            swing_h = h[i]
            for j in range(i + 1, min(i + 15, n - 1)):
                if h[j] > swing_h and c[j] < swing_h:
                    sweeps.append(
                        {
                            "type": "bearish_sweep",
                            "swing_idx": i,
                            "sweep_idx": j,
                            "swing_level": swing_h,
                            "close": c[j],
                            "distance_pct": (h[j] - swing_h) / max(swing_h, 0.0001),
                        }
                    )
                    break
    for i in range(start, n):
        if swings[i] == -1 and i < n - 3:
            swing_l = lo[i]
            for j in range(i + 1, min(i + 15, n - 1)):
                if lo[j] < swing_l and c[j] > swing_l:
                    sweeps.append(
                        {
                            "type": "bullish_sweep",
                            "swing_idx": i,
                            "sweep_idx": j,
                            "swing_level": swing_l,
                            "close": c[j],
                            "distance_pct": (swing_l - lo[j]) / max(swing_l, 0.0001),
                        }
                    )
                    break
    return sweeps


def equal_highs_lows(high: np.ndarray, low: np.ndarray, threshold_pct: float = 0.001) -> dict[str, Any]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(h)
    eq_highs = []
    eq_lows = []
    if n < 10:
        return {"highs": [], "lows": [], "count": 0}
    for i in range(max(0, n - 80), n - 3):
        for j in range(i + 1, min(n - 1, i + 20)):
            if abs(h[i] - h[j]) / max(h[i], 0.0001) < threshold_pct:
                touched = any(lo[k] <= h[j] <= h[k] for k in range(j + 1, min(n, j + 5)))
                eq_highs.append({"index": j, "level": h[j], "first_idx": i, "touched": touched})
                break
        for j in range(i + 1, min(n - 1, i + 20)):
            if abs(lo[i] - lo[j]) / max(lo[i], 0.0001) < threshold_pct:
                touched = any(lo[k] <= lo[j] <= h[k] for k in range(j + 1, min(n, j + 5)))
                eq_lows.append({"index": j, "level": lo[j], "first_idx": i, "touched": touched})
                break
    return {"highs": eq_highs, "lows": eq_lows, "count": len(eq_highs) + len(eq_lows)}


def trendlines(high: np.ndarray, low: np.ndarray, min_touch: int = 2) -> dict[str, Any]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(h)
    result = {"ascending": None, "descending": None, "slope": 0}
    if n < 20:
        return result

    recent_lows = [
        (i, lo[i]) for i in range(max(0, n - 20), n) if i > 0 and i < n - 1 and lo[i] < lo[i - 1] and lo[i] < lo[i + 1]
    ]
    if len(recent_lows) >= min_touch:
        lows_arr = np.array([x[1] for x in recent_lows])
        if len(lows_arr) >= 3:
            x_vals = np.array([x[0] for x in recent_lows])
            slope, intercept = np.polyfit(x_vals, lows_arr, 1)
            if slope > 0:
                result["ascending"] = {
                    "slope": slope,
                    "intercept": intercept,
                    "touches": len(recent_lows),
                    "current_value": slope * n + intercept,
                }

    recent_highs = [
        (i, h[i]) for i in range(max(0, n - 20), n) if i > 0 and i < n - 1 and h[i] > h[i - 1] and h[i] > h[i + 1]
    ]
    if len(recent_highs) >= min_touch:
        highs_arr = np.array([x[1] for x in recent_highs])
        if len(highs_arr) >= 3:
            x_vals = np.array([x[0] for x in recent_highs])
            slope, intercept = np.polyfit(x_vals, highs_arr, 1)
            if slope < 0:
                result["descending"] = {
                    "slope": slope,
                    "intercept": intercept,
                    "touches": len(recent_highs),
                    "current_value": slope * n + intercept,
                }
    result["slope"] = (result["ascending"]["slope"] if result["ascending"] else 0) + (
        result["descending"]["slope"] if result["descending"] else 0
    )
    return result


def find_mss(high: np.ndarray, low: np.ndarray, swings: np.ndarray, lookback: int = 30) -> list[dict[str, Any]]:
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(h)
    mss_list = []
    start = max(0, n - lookback)
    highs = [(i, h[i]) for i in range(len(swings)) if swings[i] == 1]
    lows = [(i, lo[i]) for i in range(len(swings)) if swings[i] == -1]

    if len(highs) >= 3 and len(lows) >= 3:
        for j in range(1, len(highs)):
            if highs[j][0] < start:
                continue
            prev_h = highs[j - 1][1]
            curr_h = highs[j][1]
            if curr_h > prev_h or curr_h < prev_h:
                corresponding_low = [l for l in lows if l[0] > highs[j - 1][0] and l[0] < highs[j][0]]
                if corresponding_low:
                    low_before = min(l[1] for l in corresponding_low)
                    if lo[-1] < low_before:
                        mss_list.append(
                            {
                                "type": "bearish_mss",
                                "idx": highs[j][0],
                                "break_level": low_before,
                                "swing_high": prev_h,
                            }
                        )

    for j in range(1, len(lows)):
        if lows[j][0] < start:
            continue
        prev_l = lows[j - 1][1]
        curr_l = lows[j][1]
        if curr_l < prev_l or curr_l > prev_l:
            corresponding_high = [h_pt for h_pt in highs if h_pt[0] > lows[j - 1][0] and h_pt[0] < lows[j][0]]
            if corresponding_high:
                high_before = max(x[1] for x in corresponding_high)
                if h[-1] > high_before:
                    mss_list.append(
                        {
                            "type": "bullish_mss",
                            "idx": lows[j][0],
                            "break_level": high_before,
                            "swing_low": prev_l,
                        }
                    )

    return mss_list


def analyze_market_structure(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, lookback: int = 100
) -> dict[str, Any]:
    if len(close) < 30:
        return {"trend": "unknown", "score": 0}

    swings = swing_points(high, low, left=3, right=3)
    labels = label_swing_structure(high, low, swings)
    bos = break_of_structure(high, low, swings)
    choch = change_of_character(swings, high, low)
    order_blocks = find_order_blocks(high, low, close, swings, lookback)
    fvgs = find_fvg(high, low, lookback)
    sweeps = find_liquidity_sweeps(high, low, close, swings, lookback)
    eq_hl = equal_highs_lows(high, low)
    tlines = trendlines(high, low)
    mss = find_mss(high, low, swings)

    unmitigated_obs = sum(1 for ob in order_blocks if not ob.get("is_mitigated", True))
    unmitigated_fvgs = sum(1 for f in fvgs if not f.get("is_mitigated", True))

    trend = labels["structure"]
    score = labels["trend_score"]

    if score == 0 and bos.get("bullish_bos"):
        trend = "bullish"
        score = 0.7
    elif score == 0 and bos.get("bearish_bos"):
        trend = "bearish"
        score = -0.7
    elif score == 0 and choch.get("bullish_choch"):
        trend = "bullish"
        score = 0.5
    elif score == 0 and choch.get("bearish_choch"):
        trend = "bearish"
        score = -0.5

    recent_sweeps = [s for s in sweeps if s["sweep_idx"] >= len(swings) - 8]
    recent_bos = bos.get("bullish_bos") or bos.get("bearish_bos")
    recent_choch = choch.get("bullish_choch") or choch.get("bearish_choch")

    return {
        "trend": trend,
        "score": score,
        "swings": swings,
        "labels": labels,
        "bos": bos,
        "choch": choch,
        "mss": mss,
        "order_blocks": order_blocks[-10:],
        "fvgs": fvgs[-10:],
        "sweeps": sweeps[-5:],
        "recent_sweeps": recent_sweeps,
        "equal_highs_lows": eq_hl,
        "trendlines": tlines,
        "unmitigated_obs": unmitigated_obs,
        "unmitigated_fvgs": unmitigated_fvgs,
        "recent_swing_high": bos.get("last_swing_high"),
        "recent_swing_low": bos.get("last_swing_low"),
        "recent_bos": recent_bos,
        "recent_choch": recent_choch,
    }
