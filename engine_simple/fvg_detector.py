import logging

import numpy as np

logger = logging.getLogger("fvg")

FVG_LOOKBACK = 10
MAX_FVG_AGE = 48


def detect_fvg(h1_high, h1_low, lookback=10):
    hh = np.asarray(h1_high, dtype=float)
    ll = np.asarray(h1_low, dtype=float)
    if len(hh) < 3:
        return []
    n = len(hh)
    lookback = min(lookback, n - 2)
    start = n - lookback
    fvgs = []
    for i in range(start, n - 1):
        if ll[i] > hh[i - 1]:
            bottom = hh[i - 1]
            top = float(ll[i])
            fvgs.append({"type": "BULL", "top": round(top, 6),
                          "bottom": round(bottom, 6), "age": n - i})
        if hh[i] < ll[i - 1]:
            bottom = float(hh[i])
            top = ll[i - 1]
            fvgs.append({"type": "BEAR", "top": round(top, 6),
                          "bottom": round(bottom, 6), "age": n - i})
    return fvgs


def filter_active_fvgs(fvgs, current_high, current_low):
    ch = float(current_high)
    cl = float(current_low)
    active = []
    for f in fvgs:
        if f["bottom"] < ch and f["top"] > cl:
            active.append(f)
    return active


def is_price_in_fvg(price, fvg):
    return fvg["bottom"] <= price <= fvg["top"]


def fvg_score(fvgs, direction):
    score = 0.0
    for f in fvgs:
        if f["type"] == "BULL":
            if direction == "BUY":
                score += 0.10
            else:
                score -= 0.15
        if f["type"] == "BEAR":
            if direction == "SELL":
                score += 0.10
            else:
                score -= 0.15
    return round(min(0.20, max(-0.20, score)), 3)


def detect_liquidity_sweep(h4_high, h4_low, h1_high, h1_low, h1_close):
    h4h = np.asarray(h4_high, dtype=float)
    h4l = np.asarray(h4_low, dtype=float)
    h1h = np.asarray(h1_high, dtype=float)
    h1l = np.asarray(h1_low, dtype=float)
    h1c = np.asarray(h1_close, dtype=float)

    if len(h4h) < 10 or len(h1h) < 5:
        return None, None

    recent_h4_high = np.max(h4h[-10:])
    recent_h4_low = np.min(h4l[-10:])
    last_3_high = np.max(h1h[-3:])
    last_3_low = np.min(h1l[-3:])
    last_close = h1c[-1]

    if last_3_high > recent_h4_high and last_close < recent_h4_high:
        return "SWEEP_HIGH", round(recent_h4_high, 6)

    if last_3_low < recent_h4_low and last_close > recent_h4_low:
        return "SWEEP_LOW", round(recent_h4_low, 6)

    return None, None


def find_order_blocks(h1_high, h1_low, h1_close, lookback=20):
    hh = np.asarray(h1_high, dtype=float)
    ll = np.asarray(h1_low, dtype=float)
    cc = np.asarray(h1_close, dtype=float)
    n = len(hh)
    obs = []
    start = max(0, n - lookback)

    for i in range(start + 1, n - 2):
        prev_body = abs(cc[i - 1] - cc[i - 2]) if i >= 2 else 0
        curr_body = abs(cc[i] - cc[i - 1])
        curr_range = hh[i] - ll[i]
        if curr_range <= 0:
            continue
        wick_top = hh[i] - max(cc[i - 1], cc[i])
        wick_bot = min(cc[i - 1], cc[i]) - ll[i]
        total_wick = wick_top + wick_bot
        wick_ratio = total_wick / curr_range if curr_range > 0 else 1

        if prev_body > 0 and curr_body > prev_body * 0.3:
            is_bullish = cc[i] > cc[i - 1]
            if is_bullish and wick_bot < wick_top and wick_ratio < 0.5:
                mitigated = cc[-1] >= hh[i]
                obs.append({
                    "type": "bullish_ob", "index": i,
                    "high": hh[i], "low": ll[i], "close": cc[i],
                    "is_mitigated": mitigated,
                    "strength": 1 - wick_ratio,
                })
            elif not is_bullish and wick_top < wick_bot and wick_ratio < 0.5:
                mitigated = cc[-1] <= ll[i]
                obs.append({
                    "type": "bearish_ob", "index": i,
                    "high": hh[i], "low": ll[i], "close": cc[i],
                    "is_mitigated": mitigated,
                    "strength": 1 - wick_ratio,
                })
    return obs


def find_imbalances(h1_high, h1_low, h1_close, lookback=20):
    hh = np.asarray(h1_high, dtype=float)
    ll = np.asarray(h1_low, dtype=float)
    np.asarray(h1_close, dtype=float)
    n = len(hh)
    imbalances = []
    start = max(0, n - lookback)

    for i in range(start, n - 2):
        if hh[i] < ll[i + 2]:
            imbalances.append({
                "type": "bullish_imbalance",
                "index": i,
                "high": ll[i + 2],
                "low": hh[i],
                "is_mitigated": ll[i + 1] <= hh[i] and hh[i + 1] >= ll[i + 2],
                "size_pct": (ll[i + 2] - hh[i]) / max(hh[i], 0.0001),
            })
        if ll[i] > hh[i + 2]:
            imbalances.append({
                "type": "bearish_imbalance",
                "index": i,
                "high": ll[i],
                "low": hh[i + 2],
                "is_mitigated": ll[i + 1] <= hh[i + 2] and hh[i + 1] >= ll[i],
                "size_pct": (ll[i] - hh[i + 2]) / max(hh[i + 2], 0.0001),
            })
    return imbalances


def detect_swept_fvg(fvgs, current_low, current_high):
    swept = []
    for f in fvgs:
        if f["type"] == "BULL":
            if current_low <= f["bottom"]:
                swept.append({**f, "swept": True})
            else:
                swept.append({**f, "swept": False})
        elif f["type"] == "BEAR":
            if current_high >= f["top"]:
                swept.append({**f, "swept": True})
            else:
                swept.append({**f, "swept": False})
    return swept
