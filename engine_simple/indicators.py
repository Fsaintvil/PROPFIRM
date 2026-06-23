import logging

import numpy as np

logger = logging.getLogger("indicators")


def ema(data, period):
    """Exponential Moving Average"""
    d = np.asarray(data, dtype=float)
    result = np.full_like(d, np.nan)
    if len(d) < period * 2:
        return result
    alpha = 2.0 / (period + 1)
    result[period - 1] = np.mean(d[:period])
    for i in range(period, len(d)):
        result[i] = alpha * d[i] + (1 - alpha) * result[i - 1]
    return result


def sma(data, period):
    """Simple Moving Average"""
    d = np.asarray(data, dtype=float)
    result = np.full_like(d, np.nan)
    if len(d) < period:
        return result
    cum = np.cumsum(d)
    cum[period:] = cum[period:] - cum[:-period]
    result[period - 1 :] = cum[period - 1 :] / period
    return result


def rsi(data, period=14):
    """Relative Strength Index"""
    d = np.asarray(data, dtype=float)
    result = np.full_like(d, np.nan)
    if len(d) < period + 1:
        return result
    diff = np.diff(d)
    gains = np.where(diff > 0, diff, 0)
    losses = np.where(diff < 0, -diff, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss == 0:
        result[period] = 100
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))
    for i in range(period + 1, len(d)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            result[i] = 100
        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - (100 / (1 + rs))
    return result


def macd(data, fast=12, slow=26, signal=9):
    """MACD: line, signal, histogram"""
    d = np.asarray(data, dtype=float)
    ema_fast = ema(d, fast)
    ema_slow = ema(d, slow)
    macd_line = ema_fast - ema_slow
    # Compute signal EMA only on the valid (non-NaN) segment
    valid = ~np.isnan(macd_line)
    sig_line = np.full_like(macd_line, np.nan)
    if np.any(valid):
        first_valid = np.argmax(valid)
        sig_segment = ema(macd_line[first_valid:], signal)
        sig_line[first_valid:] = sig_segment
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def bollinger_bands(data, period=20, std_dev=2.0):
    """Bollinger Bands: upper, middle, lower"""
    d = np.asarray(data, dtype=float)
    middle = sma(d, period)
    std = np.full_like(d, np.nan)
    for i in range(period - 1, len(d)):
        std[i] = np.std(d[i - period + 1 : i + 1])
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def vwap(high, low, close, volume):
    """Volume Weighted Average Price from bar data"""
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    typical = (h + lo + c) / 3
    cum_vp = np.cumsum(typical * v)
    cum_v = np.cumsum(v)
    result = cum_vp / np.maximum(cum_v, 0.0001)
    return result


def obv(close, volume):
    """On-Balance Volume"""
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    result = np.zeros_like(c)
    result[0] = v[0]
    for i in range(1, len(c)):
        if c[i] > c[i - 1]:
            result[i] = result[i - 1] + v[i]
        elif c[i] < c[i - 1]:
            result[i] = result[i - 1] - v[i]
        else:
            result[i] = result[i - 1]
    return result


def chaikin_money_flow(close, high, low, volume, period=20):
    """Chaikin Money Flow — pression d'achat/vente cumulée.

    CMF > 0.1 → accumulation (pression haussière)
    CMF < -0.1 → distribution (pression baissière)

    Args:
        close: Prix de clôture
        high: Plus haut
        low: Plus bas
        volume: Volume
        period: Période (défaut 20)

    Returns:
        float: CMF entre -1 et +1, 0.0 si données insuffisantes
    """
    c = np.asarray(close, dtype=float)
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    v = np.asarray(volume, dtype=float)
    if len(c) < period + 1:
        return 0.0
    mfm = ((c[-period:] - l[-period:]) - (h[-period:] - c[-period:])) / np.maximum(h[-period:] - l[-period:], 0.0001)
    mfv = mfm * v[-period:]
    cmf = np.sum(mfv) / np.maximum(np.sum(v[-period:]), 0.0001)
    return float(np.clip(cmf, -1.0, 1.0))


def relative_volume(volume, period=50):
    """Relative Volume — volume actuel / volume moyen.

    RVOL < 0.5 → volume anormalement bas (faux breakout possible)
    RVOL > 2.0 → volume anormalement haut (breakout confirmé)

    Args:
        volume: Volume par barre
        period: Période de référence (défaut 50)

    Returns:
        float: Ratio de volume relatif, 1.0 si données insuffisantes
    """
    v = np.asarray(volume, dtype=float)
    if len(v) < period + 1:
        return 1.0
    avg_vol = np.mean(v[-period:])
    if avg_vol < 0.001:
        return 1.0
    return float(v[-1] / avg_vol)


def obv_divergence(close, volume, period=20):
    """OBV Divergence — conflit entre tendance prix et OBV.

    Retourne:
        "bullish" si prix baisse mais OBV monte (accumulation cachée)
        "bearish" si prix monte mais OBV baisse (distribution cachée)
        "none" si pas de divergence

    Args:
        close: Prix de clôture
        volume: Volume
        period: Période (défaut 20)

    Returns:
        tuple: (divergence_type: str, strength: float)
    """
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    if len(c) < period + 1:
        return "none", 0.0
    obv_arr = obv(c, v)
    if len(obv_arr) < period:
        return "none", 0.0
    price_slope = c[-1] - c[-period]
    obv_slope_val = obv_arr[-1] - obv_arr[-period]
    strength = abs(obv_slope_val) / max(abs(obv_arr[-period]), 1.0)
    if price_slope > 0 and obv_slope_val < 0:
        return "bearish", min(strength, 1.0)
    elif price_slope < 0 and obv_slope_val > 0:
        return "bullish", min(strength, 1.0)
    return "none", 0.0


def atr(high, low, close, period=14):
    """Average True Range"""
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    result = np.full_like(c, np.nan)
    if len(c) < period + 1:
        return result
    tr = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    result[period] = np.mean(tr[:period])
    for i in range(period + 1, len(c)):
        result[i] = (result[i - 1] * (period - 1) + tr[i - 1]) / period
    return result


def stochastic_rsi(data, period=14, k=3, d=3):
    """Stochastic RSI — RETIRÉ du code mort le 23 Juin 2026.
    Conservé car importé par feature_pipeline.py et utilisé dans rsi_divergence_features()."""
    r = rsi(data, period)
    stoch = np.full_like(r, np.nan)
    if len(r) < period + k:
        return stoch, stoch
    for i in range(period, len(r)):
        lowest = np.min(r[i - period + 1 : i + 1])
        highest = np.max(r[i - period + 1 : i + 1])
        if highest - lowest > 0:
            stoch[i] = (r[i] - lowest) / (highest - lowest) * 100
        else:
            stoch[i] = 50
    k_line = ema(np.nan_to_num(stoch, nan=50), k)
    d_line = ema(k_line, d)
    return k_line, d_line


def ema_alignment(ema9, ema20, ema50, ema200, price):
    """EMA alignment score: -1 (bearish) to +1 (bullish)"""
    scores = 0
    total = 0
    pairs = [(ema9, ema20), (ema20, ema50), (ema50, ema200), (ema9, ema50)]
    for fast, slow in pairs:
        if not np.isnan(fast) and not np.isnan(slow) and fast > 0 and slow > 0:
            scores += 1 if fast > slow else -1
            total += 1
    if total == 0:
        return 0
    alignment = scores / total
    # Boost if price is above/below all EMAs
    above_all = 0
    below_all = 0
    for e in [ema9, ema20, ema50, ema200]:
        if not np.isnan(e) and e > 0:
            if price > e:
                above_all += 1
            else:
                below_all += 1
    if above_all == 4:
        alignment = min(1.0, alignment + 0.3)
    elif below_all == 4:
        alignment = max(-1.0, alignment - 0.3)
    return alignment


def fibonacci_retracement(swing_high, swing_low, current_price=None):
    """Fibonacci retracement levels from a swing high/low"""
    if swing_high is None or swing_low is None or swing_high <= swing_low:
        return {}
    diff = swing_high - swing_low
    levels = {
        "level_0": swing_low,
        "level_0236": swing_high - diff * 0.236,
        "level_0382": swing_high - diff * 0.382,
        "level_05": swing_high - diff * 0.5,
        "level_0618": swing_high - diff * 0.618,
        "level_0786": swing_high - diff * 0.786,
        "level_1": swing_high,
        "level_1272": swing_high + diff * 0.272,
        "level_1618": swing_high + diff * 0.618,
    }
    if current_price is not None:
        for k, v_level in levels.items():
            if abs(current_price - v_level) / max(v_level, 0.0001) < 0.003:
                levels["nearest"] = k
                break
        else:
            levels["nearest"] = None
    return levels


def rsi_divergence(close, rsi_values, lookback=20):
    """Detect RSI bullish/bearish divergence over lookback period"""
    c = np.asarray(close, dtype=float)
    r = np.asarray(rsi_values, dtype=float)
    if len(c) < lookback or len(r) < lookback:
        return {"bullish": False, "bearish": False, "strength": 0}
    recent_c = c[-lookback:]
    recent_r = r[-lookback:]
    c_min_idx = np.argmin(recent_c)
    c_max_idx = np.argmax(recent_c)
    r_min_idx = np.argmin(recent_r)
    r_max_idx = np.argmax(recent_r)
    bullish = False
    bearish = False
    # Bullish divergence: price makes lower low, RSI makes higher low
    if c_min_idx > r_min_idx and c[-1] < c[-lookback + c_min_idx] and r[-1] > r[-lookback + r_min_idx]:
        bullish = True
    # Bearish divergence: price makes higher high, RSI makes lower high
    if c_max_idx > r_max_idx and c[-1] > c[-lookback + c_max_idx] and r[-1] < r[-lookback + r_max_idx]:
        bearish = True
    strength = 0
    if bullish:
        strength = (r[-1] - r[-lookback + r_min_idx]) / max(r[-lookback + r_min_idx], 1)
    elif bearish:
        strength = (r[-lookback + r_max_idx] - r[-1]) / max(r[-1], 1)
    return {"bullish": bullish, "bearish": bearish, "strength": min(1, abs(strength))}


def _adx_arrays(high, low, close, period=14):
    """Calcule les arrays complets ADX, +DI, -DI alignés sur la longueur d'entrée.

    Returns:
        tuple[np.ndarray, np.ndarray, np.ndarray]: (adx_arr, pdi_arr, mdi_arr)
        alignés sur len(high). Les premières `period*2` valeurs sont NaN.
    """
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(h)
    if n < period * 2:
        return (np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan))

    up = h[1:] - h[:-1]
    down = lo[:-1] - lo[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0)
    minus_dm = np.where((down > up) & (down > 0), down, 0)
    tr = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))

    # Wilder smoothing
    pds = np.zeros(len(tr))
    mds = np.zeros(len(tr))
    trs = np.zeros(len(tr))
    pds[period - 1] = np.mean(plus_dm[:period])
    mds[period - 1] = np.mean(minus_dm[:period])
    trs[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        pds[i] = (pds[i - 1] * (period - 1) + plus_dm[i]) / period
        mds[i] = (mds[i - 1] * (period - 1) + minus_dm[i]) / period
        trs[i] = (trs[i - 1] * (period - 1) + tr[i]) / period

    # PDI / MDI per bar (safe division)
    trs_safe = np.where(trs > 1e-10, trs, np.nan)
    pdi_arr_raw = np.where(trs > 1e-10, 100.0 * pds / trs_safe, 0.0)
    mdi_arr_raw = np.where(trs > 1e-10, 100.0 * mds / trs_safe, 0.0)

    # DX array (safe division)
    di_sum = pdi_arr_raw + mdi_arr_raw
    di_sum_safe = np.where(di_sum > 0.001, di_sum, np.nan)
    dx_arr = np.where((trs > 1e-10) & (di_sum > 0.001), 100.0 * np.abs(pdi_arr_raw - mdi_arr_raw) / di_sum_safe, 0.0)

    # Second Wilder smoothing on DX
    adx_arr_raw = np.zeros(len(dx_arr))
    adx_arr_raw[period - 1] = np.mean(dx_arr[period - 1 : period * 2 - 1])
    for i in range(period, len(dx_arr)):
        adx_arr_raw[i] = (adx_arr_raw[i - 1] * (period - 1) + dx_arr[i]) / period

    # Pad front with NaN to align with original length (offset = 1 from tr vs h)
    pad = np.full(n - len(adx_arr_raw), np.nan)
    return (np.concatenate([pad, adx_arr_raw]), np.concatenate([pad, pdi_arr_raw]), np.concatenate([pad, mdi_arr_raw]))


def adx(high, low, close, period=14):
    """Average Directional Index (ADX) — Wilder smoothing.

    Returns:
        tuple[float, float, float]: (adx, plus_di, minus_di) dernières valeurs.
        ou (0.0, 0.0, 0.0) si pas assez de données.
    """
    adx_arr, pdi_arr, mdi_arr = _adx_arrays(high, low, close, period)
    # Filtrer les NaN
    valid = ~np.isnan(adx_arr)
    if not np.any(valid):
        return (0.0, 0.0, 0.0)
    return (float(adx_arr[valid][-1]), float(pdi_arr[valid][-1]), float(mdi_arr[valid][-1]))


def adx_arrays(high, low, close, period=14):
    """ADX avec arrays complets — idéal pour backtests O(n).

    Returns:
        tuple[np.ndarray, np.ndarray, np.ndarray]: (adx_arr, plus_di_arr, minus_di_arr)
        alignés sur len(high). Les premières ~28 valeurs sont NaN.
    """
    return _adx_arrays(high, low, close, period)


def market_regime_features(high, low, close, volume, period=50):
    """Compute additional features for regime detection"""
    h, lo, c, v = [np.asarray(x, dtype=float) for x in (high, low, close, volume)]
    features = {}
    if len(c) < period:
        return features

    atr_vals = atr(h, lo, c)
    features["atr_current"] = atr_vals[-1] if len(atr_vals) > 0 and not np.isnan(atr_vals[-1]) else 0
    features["atr_ma"] = np.mean(atr_vals[-20:]) if len(atr_vals) >= 20 else 0

    obv_vals = obv(c, v)
    features["obv_slope"] = (obv_vals[-1] - obv_vals[-20]) / max(abs(obv_vals[-20]), 1)

    rsi_vals = rsi(c)
    features["rsi"] = rsi_vals[-1] if len(rsi_vals) > 0 and not np.isnan(rsi_vals[-1]) else 50
    features["rsi_slope"] = (rsi_vals[-1] - rsi_vals[-10]) if len(rsi_vals) >= 10 and not np.isnan(rsi_vals[-10]) else 0

    macd_line, _, macd_hist = macd(c)
    features["macd_hist"] = macd_hist[-1] if len(macd_hist) > 0 and not np.isnan(macd_hist[-1]) else 0
    macd_ok = len(macd_hist) >= 5 and not np.isnan(macd_hist[-5])
    features["macd_slope"] = (macd_hist[-1] - macd_hist[-5]) if macd_ok else 0

    bb_upper, bb_mid, bb_lower = bollinger_bands(c)
    if len(bb_upper) > 0 and not np.isnan(bb_upper[-1]):
        bb_width = (bb_upper[-1] - bb_lower[-1]) / max(bb_mid[-1], 0.0001)
        features["bb_width"] = bb_width
        features["bb_position"] = (c[-1] - bb_lower[-1]) / max(bb_upper[-1] - bb_lower[-1], 0.0001)
    else:
        features["bb_width"] = 0
        features["bb_position"] = 0.5

    return features
