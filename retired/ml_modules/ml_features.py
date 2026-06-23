import logging

import numpy as np

from engine_simple.indicators import (
    anchored_vwap,
    atr,
    bollinger_bands,
    ema,
    ema_alignment,
    macd,
    obv,
    premium_discount_zones,
    rsi,
    stochastic_rsi,
    vwap,
)
from engine_simple.market_structure import analyze_market_structure

logger = logging.getLogger("ml_features")

FULL_FEATURE_NAMES = [
    # Price & Returns (core)
    "return_1", "return_5", "return_10", "return_20",
    # EMAs (normalized ratios only, keep 3 most informative)
    "ema9_20", "ema20_50",
    "price_vs_ema9", "price_vs_ema50",
    # RSI
    "rsi", "rsi_change_5",
    # MACD (histogram only, line/signal redundant)
    "macd_hist", "macd_hist_change_3",
    # Bollinger Bands
    "bb_position", "bb_width",
    # ATR (normalized + change, remove raw)
    "atr_pct", "atr_change_5",
    # Volume
    "obv_trend", "obv_divergence",
    "vwap_distance",
    # Stochastic (remove overbought/oversold flags, derived from k)
    "stoch_k", "stoch_d",
    # Market Structure (keep core, remove noisy specifics)
    "structure_score",
    "bos_present", "choch_present",
    # Session
    "session_weight",
    # Composite
    "ema_alignment_score", "confluence_score",
    # ICT / Orderflow (most predictive)
    "avwap_distance", "pd_zone",
    "eq_hl_count", "trendline_slope",
]

def compute_features(high, low, close, volume, tick_volume, spread=None):
    """Compute all features for ML models. Returns dict of feature_name -> value."""
    c = np.asarray(close, dtype=float)
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    v = np.asarray(volume, dtype=float)

    n = len(c)
    features = {}

    if n < 50:
        return {name: 0.5 for name in FULL_FEATURE_NAMES}

    # Returns
    features["return_1"] = (c[-1] - c[-2]) / max(c[-2], 0.0001) if n >= 2 else 0
    features["return_5"] = (c[-1] - c[-6]) / max(c[-6], 0.0001) if n >= 6 else 0
    features["return_10"] = (c[-1] - c[-11]) / max(c[-11], 0.0001) if n >= 11 else 0
    features["return_20"] = (c[-1] - c[-21]) / max(c[-21], 0.0001) if n >= 21 else 0

    # EMAs (keep only normalized ratios + price position)
    e9 = ema(c, 9)
    e20 = ema(c, 20)
    e50 = ema(c, 50)
    e200 = ema(c, 200)

    e9_ok = not np.isnan(e9[-1]) and not np.isnan(e20[-1])
    e20_ok = not np.isnan(e20[-1]) and not np.isnan(e50[-1])
    features["ema9_20"] = (e9[-1] - e20[-1]) / max(e20[-1], 0.0001) if e9_ok else 0
    features["ema20_50"] = (e20[-1] - e50[-1]) / max(e50[-1], 0.0001) if e20_ok else 0

    e9v = e9[-1] if len(e9) > 0 and not np.isnan(e9[-1]) else 0
    e50v = e50[-1] if len(e50) > 0 and not np.isnan(e50[-1]) else 0
    features["price_vs_ema9"] = 1 if c[-1] > e9v else -1 if e9v > 0 else 0
    features["price_vs_ema50"] = 1 if c[-1] > e50v else -1 if e50v > 0 else 0

    # RSI
    rsi_arr = rsi(c)
    features["rsi"] = rsi_arr[-1] if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50
    if len(rsi_arr) >= 6 and not np.isnan(rsi_arr[-6]):
        features["rsi_change_5"] = rsi_arr[-1] - rsi_arr[-6]
    else:
        features["rsi_change_5"] = 0

    # MACD (histogram only)
    _, _, macd_hist = macd(c)
    features["macd_hist"] = macd_hist[-1] if len(macd_hist) > 0 and not np.isnan(macd_hist[-1]) else 0
    if len(macd_hist) >= 4 and not np.isnan(macd_hist[-4]):
        features["macd_hist_change_3"] = macd_hist[-1] - macd_hist[-4]
    else:
        features["macd_hist_change_3"] = 0

    # Bollinger Bands
    bb_upper, bb_mid, bb_lower = bollinger_bands(c)
    if len(bb_upper) > 0 and not np.isnan(bb_upper[-1]):
        features["bb_position"] = (c[-1] - bb_lower[-1]) / max(bb_upper[-1] - bb_lower[-1], 0.0001)
        features["bb_width"] = (bb_upper[-1] - bb_lower[-1]) / max(bb_mid[-1], 0.0001)
    else:
        features["bb_position"] = 0.5
        features["bb_width"] = 0

    # ATR
    atr_arr = atr(h, lo, c)
    atr_ok = len(atr_arr) > 0 and not np.isnan(atr_arr[-1]) and c[-1] > 0
    features["atr_pct"] = atr_arr[-1] / max(c[-1], 0.0001) * 100 if atr_ok else 0
    if len(atr_arr) >= 6 and not np.isnan(atr_arr[-6]):
        features["atr_change_5"] = (atr_arr[-1] - atr_arr[-6]) / max(atr_arr[-6], 0.0001)
    else:
        features["atr_change_5"] = 0

    # OBV
    obv_arr = obv(c, v)
    if len(obv_arr) > 20:
        obv_t = obv_arr[-1] - obv_arr[-20]
        features["obv_trend"] = 1 if obv_t > 0 else -1 if obv_t < 0 else 0
        features["obv_divergence"] = 1 if (c[-1] > c[-20] and obv_t < 0) or (c[-1] < c[-20] and obv_t > 0) else 0
    else:
        features["obv_trend"] = 0
        features["obv_divergence"] = 0

    # VWAP
    vwap_arr = vwap(h, lo, c, v)
    if len(vwap_arr) > 0 and not np.isnan(vwap_arr[-1]) and vwap_arr[-1] > 0:
        features["vwap_distance"] = (c[-1] - vwap_arr[-1]) / max(vwap_arr[-1], 0.0001)
    else:
        features["vwap_distance"] = 0

    # Stochastic
    stoch_k, stoch_d = stochastic_rsi(c)
    features["stoch_k"] = stoch_k[-1] if len(stoch_k) > 0 and not np.isnan(stoch_k[-1]) else 50
    features["stoch_d"] = stoch_d[-1] if len(stoch_d) > 0 and not np.isnan(stoch_d[-1]) else 50

    # Market Structure
    ms = analyze_market_structure(h, lo, c)
    features["structure_score"] = ms.get("score", 0)
    bos = ms.get("bos", {})
    features["bos_present"] = 1 if bos.get("bullish_bos") or bos.get("bearish_bos") else 0
    choch = ms.get("choch", {})
    features["choch_present"] = 1 if choch.get("bullish_choch") or choch.get("bearish_choch") else 0

    # Session
    features["session_weight"] = 0.5

    # Composite
    e20v = e20[-1] if len(e20) > 0 and not np.isnan(e20[-1]) else 0
    e200v = e200[-1] if len(e200) > 0 and not np.isnan(e200[-1]) else 0
    features["ema_alignment_score"] = ema_alignment(e9v, e20v, e50v, e200v, c[-1])
    features["confluence_score"] = (features.get("ema_alignment_score", 0) +
                                    features.get("structure_score", 0) +
                                    features.get("obv_trend", 0) * 0.3 +
                                    features.get("rsi", 50) / 100 - 0.5) / 3
    features["confluence_score"] = max(-1, min(1, features["confluence_score"]))

    # Anchored VWAP + Premium/Discount
    features["avwap_distance"] = anchored_vwap(h, lo, c, v, anchor_idx=max(0, n - 24))[1] if n > 24 else 0
    vwap_last = vwap_arr[-1] if len(vwap_arr) > 0 and not np.isnan(vwap_arr[-1]) else None
    pd = premium_discount_zones(c[-1], vwap_last, h[-1], lo[-1])
    features["pd_zone"] = 1 if pd.get("zone") == "premium" else -1 if pd.get("zone") == "discount" else 0
    features["eq_hl_count"] = ms.get("equal_highs_lows", {}).get("count", 0)
    features["trendline_slope"] = ms.get("trendlines", {}).get("slope", 0)

    # Fill NaN (binary → 0, continuous → 0.5)
    BINARY_FEATURES = {"bos_present", "choch_present",
                       "obv_trend", "obv_divergence", "session_weight",
                       "pd_zone"}
    for k in FULL_FEATURE_NAMES:
        if k not in features or np.isnan(features.get(k, 0)):
            features[k] = 0.0 if k in BINARY_FEATURES else 0.5

    return features


class FeatureEngine:
    """Wrapper class for ml_ensemble.py compatibility"""

    def compute_features(self, rates):
        """rates: list of MT5 rate tuples [(time, open, high, low, close, tick_volume, spread, real_volume), ...]"""
        if rates is None or len(rates) < 20:
            return {name: 0.5 for name in FULL_FEATURE_NAMES}
        if hasattr(rates, 'dtype'):
            rates_list = [tuple(r) for r in rates]
        else:
            rates_list = list(rates) if not isinstance(rates, list) else rates
        high = np.array([r[2] for r in rates_list], dtype=float)
        low = np.array([r[3] for r in rates_list], dtype=float)
        close = np.array([r[4] for r in rates_list], dtype=float)
        has_vol = len(rates_list[0]) > 5
        volume = np.array([r[5] for r in rates_list], dtype=float) if has_vol else np.ones(len(rates_list))
        tick_volume = volume
        spread_val = np.array([r[6] for r in rates_list], dtype=float) if len(rates_list[0]) > 6 else None
        return compute_features(high, low, close, volume, tick_volume, spread_val)
