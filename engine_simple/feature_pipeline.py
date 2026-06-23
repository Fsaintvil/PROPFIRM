"""Feature Pipeline — calcul de features avancées pour le scoring et le ML.

8 familles de features (référence : "Si tu veux dépasser les stratégies classiques...") :
  1. Price Action      — rendements, EMAs, range, breakout/compression
  2. Volatilité        — ATR, realized vol, Parkinson, Garman-Klass
  3. Volume            — RVOL, VWAP, OBV, CMF, CVD, delta ratio
  4. Liquidité         — daily/weekly highs/lows, sweeps, S/R
  5. Structure marché  — BOS, CHOCH, swing counts, trend force
  6. Temps/sessions    — heure UTC, sessions, jours
  7. [carnet d'ordres] — non disponible MT5 retail
  8. [crypto-specific] — non disponible MT5

Utilisation:
  features = compute_all_features(close, high, low, volume, spread, symbol)
  # → dict de ~30 features

  score_adj = compute_score_adjustment(features, signal_action)
  # → facteur de score (0.0-2.0)
"""

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np

from engine_simple.indicators import (
    atr,
    ema,
    sma,
    vwap,
    obv,
    rsi,
    bollinger_bands,
    chaikin_money_flow,
    relative_volume,
    obv_divergence,
    ema_alignment,
    macd,
    market_regime_features,
)

logger = logging.getLogger("feature_pipeline")

# ─── Constants ───────────────────────────────────────────────────────────────

# Sessions UTC
SESSION_ASIA = list(range(0, 9))  # 00:00-08:59
SESSION_LONDON = list(range(9, 17))  # 09:00-16:59
SESSION_NY = list(range(13, 22))  # 13:00-21:59
SESSION_ASIA_LONDON_OVERLAP = [9]  # 09:00
SESSION_LONDON_NY_OVERLAP = list(range(13, 17))  # 13:00-16:59

# ─── 1. Price Action Features ──────────────────────────────────────────────


def returns_features(close: np.ndarray) -> dict[str, float]:
    """Rendements sur 1, 3, 5, 10, 20, 50 périodes."""
    f: dict[str, float] = {}
    if len(close) < 2:
        return f
    for p in [1, 3, 5, 10, 20, 50]:
        if len(close) >= p + 1:
            f[f"return_{p}"] = float((close[-1] - close[-p - 1]) / max(close[-p - 1], 1e-8))
    return f


def ema_distance_features(close: np.ndarray) -> dict[str, float]:
    """Distance du prix aux EMA20/50/200 en %."""
    f: dict[str, float] = {}
    for period, name in [(20, "ema20"), (50, "ema50"), (200, "ema200")]:
        arr = ema(close, period)
        if len(arr) > 0 and not np.isnan(arr[-1]) and arr[-1] > 0:
            f[f"dist_{name}"] = float((close[-1] - arr[-1]) / arr[-1] * 100)
            f[f"slope_{name}"] = float((arr[-1] - arr[-min(len(arr), 5)]) / max(arr[-min(len(arr), 5)], 1e-8) * 100)
    return f


def range_features(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict[str, float]:
    """Position dans le range 20 périodes, breakout/compression."""
    f: dict[str, float] = {}
    if len(close) < 20:
        return f
    h20 = np.max(high[-20:])
    l20 = np.min(low[-20:])
    if h20 > l20:
        f["range_position"] = float((close[-1] - l20) / (h20 - l20))
        f["dist_high_20"] = float((h20 - close[-1]) / max(close[-1], 1e-8) * 100)
        f["dist_low_20"] = float((close[-1] - l20) / max(close[-1], 1e-8) * 100)
    # Breakout score : prix sort du range 20
    range_20 = (h20 - l20) / max(l20, 1e-8) * 100
    if range_20 > 0:
        if close[-1] > h20 * 1.001:
            f["breakout_score"] = 1.0  # breakout haut
        elif close[-1] < l20 * 0.999:
            f["breakout_score"] = -1.0  # breakout bas
        else:
            # Distance aux bornes
            f["breakout_score"] = 0.0
    # Compression score : range se rétrécit
    if len(close) >= 40:
        h40 = np.max(high[-40:-20])
        l40 = np.min(low[-40:-20])
        range_40 = (h40 - l40) / max(l40, 1e-8) * 100
        if range_40 > 0:
            f["compression_ratio"] = float(range_20 / range_40)
    return f


# ─── 2. Volatilité Features ───────────────────────────────────────────────


def volatility_features(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, open_prices: np.ndarray | None = None
) -> dict[str, float]:
    """ATR, realized vol, Parkinson, Garman-Klass, expansion/compression."""
    f: dict[str, float] = {}
    if len(close) < 15:
        return f

    # ATR 14 et 50
    atr14 = atr(high, low, close, 14)
    atr50 = atr(high, low, close, 50) if len(close) >= 55 else atr14
    atr14_val = float(atr14[-1]) if len(atr14) > 0 and not np.isnan(atr14[-1]) else 0
    # ATR percentile (rang sur N bougies)
    atr_vals = atr14[~np.isnan(atr14)]
    if len(atr_vals) > 20:
        rank = np.sum(atr_vals[-1] >= atr_vals) / len(atr_vals)
        f["atr_percentile"] = float(rank)
    f["atr_14"] = round(atr14_val, 6)
    # Ratio ATR court / long
    if len(close) >= 55:
        atr50_val = float(atr50[-1]) if len(atr50) > 0 and not np.isnan(atr50[-1]) else atr14_val
        f["atr_ratio_14_50"] = float(atr14_val / max(atr50_val, 1e-8))
    else:
        f["atr_ratio_14_50"] = 1.0

    # Réalisée volatility (écart-type des rendements)
    returns = np.diff(close) / np.maximum(close[:-1], 1e-8)
    for p in [10, 50]:
        if len(returns) >= p:
            f[f"realized_vol_{p}"] = float(np.std(returns[-p:]) * 100)
    # Ratio realized vol 10/50
    if "realized_vol_10" in f and "realized_vol_50" in f and f["realized_vol_50"] > 0:
        f["realized_vol_ratio"] = f["realized_vol_10"] / max(f["realized_vol_50"], 1e-8)

    # Parkinson Volatility (high-low based)
    if len(high) >= 20:
        hl_ratio = np.log(np.array(high[-20:]) / np.maximum(np.array(low[-20:]), 1e-8))
        parkinson = np.sqrt(np.mean(hl_ratio**2) / (4 * np.log(2))) * 100
        f["parkinson_vol"] = round(float(parkinson), 4)

    # Garman-Klass Volatility (OHLC-based) — nécessite les open prices
    if len(close) >= 20 and open_prices is not None and len(open_prices) >= 20:
        h_l = np.log(np.array(high[-20:]) / np.maximum(np.array(low[-20:]), 1e-8)) ** 2
        c_o = np.log(np.array(close[-20:]) / np.maximum(np.array(open_prices[-20:]), 1e-8)) ** 2
        garman_klass = np.sqrt(np.mean(0.5 * h_l - (2 * np.log(2) - 1) * c_o)) * 100
        f["garman_klass_vol"] = round(float(garman_klass) if not np.isnan(garman_klass) else 0, 4)

    # Expansion / Compression de volatilité
    if len(atr_vals) >= 40:
        atr_recent = np.mean(atr_vals[-10:])
        atr_prior = np.mean(atr_vals[-40:-30])
        if atr_prior > 0:
            vol_change = (atr_recent - atr_prior) / atr_prior
            f["vol_expansion"] = float(vol_change)
            f["vol_expansion_binary"] = 1.0 if vol_change > 0.2 else (-1.0 if vol_change < -0.2 else 0.0)

    return f


# ─── 3. Volume Features ──────────────────────────────────────────────────


def volume_features(close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: np.ndarray) -> dict[str, float]:
    """RVOL, VWAP, OBV, CMF, delta ratio, CVD."""
    f: dict[str, float] = {}
    if len(close) < 20:
        return f

    # RVOL (relative volume)
    rvol = relative_volume(volume, period=50)
    f["rvol"] = round(rvol, 2)

    # VWAP distance et slope
    vwap_arr = vwap(high, low, close, volume)
    if len(vwap_arr) > 0 and not np.isnan(vwap_arr[-1]) and vwap_arr[-1] > 0:
        f["vwap_distance"] = float((close[-1] - vwap_arr[-1]) / vwap_arr[-1] * 100)
        if len(vwap_arr) >= 5 and not np.isnan(vwap_arr[-5]):
            f["vwap_slope"] = float((vwap_arr[-1] - vwap_arr[-5]) / max(vwap_arr[-5], 1e-8) * 100)

    # OBV slope
    obv_arr = obv(close, volume)
    if len(obv_arr) >= 20:
        obv_change = (obv_arr[-1] - obv_arr[-20]) / max(abs(obv_arr[-20]), 1)
        f["obv_slope"] = float(obv_change)
        # OBV trend (direction)
        f["obv_trend"] = 1.0 if obv_change > 0 else (-1.0 if obv_change < 0 else 0.0)

    # OBV Divergence
    div_type, div_strength = obv_divergence(close, volume, period=20)
    f["obv_divergence"] = 1.0 if div_type == "bullish" else (-1.0 if div_type == "bearish" else 0.0)
    f["obv_div_strength"] = round(div_strength, 3)

    # CMF (Chaikin Money Flow)
    cmf = chaikin_money_flow(close, high, low, volume, period=20)
    f["cmf"] = round(cmf, 3)

    # Delta ratio : buy/sell volume approximation via MFM
    if len(high) >= 20 and len(low) >= 20:
        h_arr = np.array(high[-20:], dtype=float)
        l_arr = np.array(low[-20:], dtype=float)
        c_arr = np.array(close[-20:], dtype=float)
        v_arr = np.array(volume[-20:], dtype=float)
        mfm = ((c_arr - l_arr) - (h_arr - c_arr)) / np.maximum(h_arr - l_arr, 0.0001)
        buy_vol = np.sum(v_arr[mfm > 0])
        sell_vol = np.sum(v_arr[mfm < 0])
        if sell_vol > 0:
            f["delta_ratio"] = float(buy_vol / max(sell_vol, 0.0001))
        f["cvd"] = float(np.sum(mfm * v_arr))  # Cumulative Volume Delta approx

    return f


# ─── 5. Liquidité Features ────────────────────────────────────────────────


def liquidity_features(close: np.ndarray, high: np.ndarray, low: np.ndarray, symbol: str = "") -> dict[str, float]:
    """Daily/weekly highs/lows, sessions highs/lows."""
    f: dict[str, float] = {}
    if len(close) < 2:
        return f
    price = float(close[-1])

    # Previous day high/low (approximé avec la dernière bougie)
    if len(high) >= 24:
        f["dist_prev_day_high"] = float((np.max(high[-24:]) - price) / max(price, 1e-8) * 100)
        f["dist_prev_day_low"] = float((price - np.min(low[-24:])) / max(price, 1e-8) * 100)

    # Weekly high/low (5 jours = 120 bougies H1, 30 bougies H4)
    weekly_bars = 120 if "H1" in str(symbol) or len(close) < 30 else 30
    weekly_bars = min(weekly_bars, len(close) // 2)
    if len(high) >= weekly_bars:
        f["dist_weekly_high"] = float((np.max(high[-weekly_bars:]) - price) / max(price, 1e-8) * 100)
        f["dist_weekly_low"] = float((price - np.min(low[-weekly_bars:])) / max(price, 1e-8) * 100)

    # Equal highs/lows detection (2 pivots proches)
    if len(high) >= 20:
        peaks = []
        for i in range(3, len(high) - 3):
            if high[i] > high[i - 1] and high[i] > high[i - 2] and high[i] > high[i + 1] and high[i] > high[i + 2]:
                peaks.append((i, high[i]))
        if len(peaks) >= 2:
            last_peaks = [p[1] for p in peaks[-3:]]
            for i in range(len(last_peaks) - 1):
                dist = abs(last_peaks[i] - last_peaks[i + 1]) / max(last_peaks[i], 1e-8) * 100
                if dist < 0.3:  # < 0.3% d'écart
                    f["equal_highs"] = 1.0
                    break
            else:
                f["equal_highs"] = 0.0

    return f


# ─── 6. Structure de Marché Features ──────────────────────────────────────


def structure_features(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict[str, float]:
    """BOS, CHOCH, swing counts, trend force, range compression."""
    f: dict[str, float] = {}
    if len(close) < 30:
        return f

    # Swing highs/lows detection
    swings_high = []
    swings_low = []
    for i in range(2, len(high) - 2):
        if high[i] > high[i - 1] and high[i] > high[i - 2] and high[i] > high[i + 1] and high[i] > high[i + 2]:
            swings_high.append((i, high[i]))
        if low[i] < low[i - 1] and low[i] < low[i - 2] and low[i] < low[i + 1] and low[i] < low[i + 2]:
            swings_low.append((i, low[i]))

    f["num_swings_high"] = float(len(swings_high))
    f["num_swings_low"] = float(len(swings_low))

    # Trend force : ratio de swings dans la direction dominante
    if len(swings_high) >= 2 and len(swings_low) >= 2:
        recent_sh = [s[1] for s in swings_high[-3:]] if len(swings_high) >= 3 else [s[1] for s in swings_high]
        recent_sl = [s[1] for s in swings_low[-3:]] if len(swings_low) >= 3 else [s[1] for s in swings_low]
        if recent_sh and recent_sl:
            higher_highs = sum(1 for i in range(1, len(recent_sh)) if recent_sh[i] > recent_sh[i - 1])
            lower_lows = sum(1 for i in range(1, len(recent_sl)) if recent_sl[i] < recent_sl[i - 1])
            total = higher_highs + lower_lows
            if total > 0:
                f["trend_force"] = float((higher_highs - lower_lows) / total)

    # BOS haussier/baissier
    if len(swings_high) >= 2 and len(swings_low) >= 2:
        last_sh = swings_high[-1][1] if swings_high else 0
        prev_sh = swings_high[-2][1] if len(swings_high) >= 2 else 0
        last_sl = swings_low[-1][1] if swings_low else 0
        prev_sl = swings_low[-2][1] if len(swings_low) >= 2 else 0
        if prev_sh > 0 and last_sh > prev_sh:
            f["bos_bullish"] = 1.0
        if prev_sl > 0 and last_sl < prev_sl:
            f["bos_bearish"] = 1.0

    # Range compression
    if len(high) >= 20:
        range_10 = np.max(high[-10:]) - np.min(low[-10:])
        range_20 = np.max(high[-20:]) - np.min(low[-20:])
        if range_20 > 0:
            f["range_compression"] = float(range_10 / range_20)

    return f


# ─── 7. Temps et Sessions Features ────────────────────────────────────────


def time_features() -> dict[str, float]:
    """Heure UTC, jour semaine, sessions actives."""
    f: dict[str, float] = {}
    now = datetime.now(timezone.utc)
    hour = now.hour
    weekday = now.weekday()  # 0=Monday, 6=Sunday

    f["hour_utc"] = float(hour)
    f["weekday"] = float(weekday)
    f["is_weekend"] = 1.0 if weekday >= 5 else 0.0
    f["is_monday"] = 1.0 if weekday == 0 else 0.0
    f["is_friday"] = 1.0 if weekday == 4 else 0.0

    # Sessions
    f["session_asia"] = 1.0 if hour in SESSION_ASIA else 0.0
    f["session_london"] = 1.0 if hour in SESSION_LONDON else 0.0
    f["session_ny"] = 1.0 if hour in SESSION_NY else 0.0
    f["session_london_ny_overlap"] = 1.0 if hour in SESSION_LONDON_NY_OVERLAP else 0.0
    f["session_asia_london_overlap"] = 1.0 if hour in SESSION_ASIA_LONDON_OVERLAP else 0.0

    return f


# ─── EMA Alignment (multi-timeframe trend) ────────────────────────────────


def ema_alignment_features(close: np.ndarray, price: float | None = None) -> dict[str, float]:
    """EMA alignment score (+1 bullish, -1 bearish)."""
    f: dict[str, float] = {}
    if len(close) < 200:
        return f
    ema9 = float(ema(close, 9)[-1])
    ema20 = float(ema(close, 20)[-1])
    ema50 = float(ema(close, 50)[-1])
    ema200 = float(ema(close, 200)[-1]) if len(close) >= 200 else 0
    if price is None:
        price = float(close[-1])

    alignment = ema_alignment(ema9, ema20, ema50, ema200, price)
    f["ema_alignment"] = round(alignment, 3)

    # Combo : toutes les EMAs dans le même ordre
    if ema200 > 0:
        if ema9 > ema20 > ema50 > ema200:
            f["ema_bullish_combo"] = 1.0
        elif ema9 < ema20 < ema50 < ema200:
            f["ema_bearish_combo"] = 1.0
        else:
            f["ema_bullish_combo"] = 0.0
            f["ema_bearish_combo"] = 0.0

    return f


# ─── Spread Features ──────────────────────────────────────────────────────


def spread_features(spread: float | None = None, spread_history: list[float] | None = None) -> dict[str, float]:
    """Spread percentile et qualité."""
    f: dict[str, float] = {}
    if spread is not None:
        f["spread"] = spread
    if spread_history and len(spread_history) > 20:
        rank = sum(spread >= s for s in spread_history) / len(spread_history)
        f["spread_percentile"] = float(rank)
        f["spread_zscore"] = float((spread - np.mean(spread_history)) / max(np.std(spread_history), 0.001))
    return f


# ─── Main Entry Point ─────────────────────────────────────────────────────


def compute_all_features(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    open_prices: np.ndarray | None = None,
    volume: np.ndarray | None = None,
    spread: float | None = None,
    spread_history: list[float] | None = None,
    symbol: str = "",
) -> dict[str, float]:
    """Compute ALL features from available data.

    Args:
        close: Prix de clôture (array 1D)
        high: Prix hauts (array 1D)
        low: Prix bas (array 1D)
        volume: Volume (array 1D, optionnel)
        spread: Spread actuel en points (optionnel)
        spread_history: Historique des spreads (optionnel)
        symbol: Nom du symbole (optionnel, pour ajustements)

    Returns:
        dict de ~30 features nommées
    """
    features: dict[str, float] = {}

    # 1. Price Action
    features.update(returns_features(close))
    features.update(ema_distance_features(close))
    features.update(range_features(close, high, low))

    # 2. Volatilité
    features.update(volatility_features(high, low, close, open_prices))

    # 3. Volume (si disponible)
    if volume is not None and len(volume) == len(close):
        features.update(volume_features(close, high, low, volume))
        # Features existantes de market_regime_features (complément)
        mrf = market_regime_features(high, low, close, volume)
        features.update({f"mrf_{k}": v for k, v in mrf.items()})

    # 5. Liquidité
    features.update(liquidity_features(close, high, low, symbol))

    # 6. Structure
    features.update(structure_features(close, high, low))

    # 7. Temps
    features.update(time_features())

    # EMA Alignment
    features.update(ema_alignment_features(close))

    # Spread
    features.update(spread_features(spread, spread_history))

    logger.debug(f"[FEATURES] {symbol}: {len(features)} features computed")
    return features


def compute_score_adjustment(
    features: dict[str, float],
    signal_action: str,
) -> tuple[float, dict[str, Any]]:
    """Calcule un ajustement de score basé sur les features.

    Chaque feature vote pour ou contre le signal.
    Retourne (facteur_multiplicatif, détails).

    Facteur > 1.0 = bonus, < 1.0 = pénalité.
    """
    adj = 1.0
    reasons: dict[str, Any] = {}

    # ── EMA Alignment ──
    ema_align = features.get("ema_alignment", 0)
    if signal_action == "BUY" and ema_align > 0.3:
        adj *= 1.08
        reasons["ema_align_bonus"] = f"+8% (align={ema_align:.2f})"
    elif signal_action == "SELL" and ema_align < -0.3:
        adj *= 1.08
        reasons["ema_align_bonus"] = f"+8% (align={ema_align:.2f})"
    elif signal_action == "BUY" and ema_align < -0.5:
        adj *= 0.85
        reasons["ema_align_penalty"] = f"-15% (contre-tendance align={ema_align:.2f})"
    elif signal_action == "SELL" and ema_align > 0.5:
        adj *= 0.85
        reasons["ema_align_penalty"] = f"-15% (contre-tendance align={ema_align:.2f})"

    # ── EMA Bullish/Bearish Combo ──
    if features.get("ema_bullish_combo", 0) and signal_action == "BUY":
        adj *= 1.10
        reasons["ema_combo"] = "+10% (bullish combo)"
    elif features.get("ema_bearish_combo", 0) and signal_action == "SELL":
        adj *= 1.10
        reasons["ema_combo"] = "+10% (bearish combo)"

    # ── Volatilité : ATR percentile ──
    atr_pct = features.get("atr_percentile", 0.5)
    if atr_pct > 0.85:
        adj *= 0.90
        reasons["atr_high"] = f"-10% (ATR percentile={atr_pct:.0%})"
    elif atr_pct < 0.15:
        adj *= 1.05
        reasons["atr_low"] = f"+5% (ATR percentile={atr_pct:.0%})"

    # ── Volatilité : expansion ──
    vol_exp = features.get("vol_expansion", 0)
    if vol_exp > 0.3:
        adj *= 0.88
        reasons["vol_expansion"] = f"-12% (expansion={vol_exp:.1%})"

    # ── Volume : RVOL ──
    rvol = features.get("rvol", 1.0)
    if rvol < 0.5:
        adj *= 0.85
        reasons["rvol_low"] = f"-15% (RVOL={rvol:.1f})"
    elif rvol > 2.0:
        adj *= 1.10
        reasons["rvol_high"] = f"+10% (RVOL={rvol:.1f})"

    # ── Volume : CMF ──
    cmf = features.get("cmf", 0)
    if signal_action == "BUY" and cmf > 0.1:
        adj *= 1.08
        reasons["cmf_buy"] = f"+8% (CMF={cmf:.2f}>0.1)"
    elif signal_action == "SELL" and cmf < -0.1:
        adj *= 1.08
        reasons["cmf_sell"] = f"+8% (CMF={cmf:.2f}<-0.1)"
    elif signal_action == "BUY" and cmf < -0.1:
        adj *= 0.92
        reasons["cmf_anti_buy"] = f"-8% (CMF={cmf:.2f}<-0.1, contre BUY)"
    elif signal_action == "SELL" and cmf > 0.1:
        adj *= 0.92
        reasons["cmf_anti_sell"] = f"-8% (CMF={cmf:.2f}>0.1, contre SELL)"

    # ── VWAP ──
    vwap_dist = features.get("vwap_distance", 0)
    if signal_action == "BUY" and vwap_dist < -1.0:
        adj *= 1.06
        reasons["vwap_discount"] = f"+6% (prix {vwap_dist:.1f}% sous VWAP = discount)"
    elif signal_action == "SELL" and vwap_dist > 1.0:
        adj *= 1.06
        reasons["vwap_premium"] = f"+6% (prix {vwap_dist:.1f}% au-dessus VWAP = premium)"

    # ── OBV Divergence ──
    obv_div = features.get("obv_divergence", 0)
    obv_str = features.get("obv_div_strength", 0)
    if obv_div != 0 and obv_str > 0.3:
        if (signal_action == "BUY" and obv_div == 1) or (signal_action == "SELL" and obv_div == -1):
            adj *= 1.08
            reasons["obv_div_agree"] = f"+8% (OBV confirme, strength={obv_str:.2f})"
        else:
            adj *= 0.85
            reasons["obv_div_disagree"] = f"-15% (OBV contredit, strength={obv_str:.2f})"

    # ── Sessions ──
    if features.get("session_london_ny_overlap", 0):
        adj *= 1.05
        reasons["session_overlap"] = "+5% (London-NY overlap)"
    elif features.get("session_asia", 0) and features.get("is_weekend", 0) == 0:
        adj *= 0.95
        reasons["session_asia"] = "-5% (session Asia, liquidité faible)"

    # ── Spread ──
    spread_pct = features.get("spread_percentile", 0.5)
    if spread_pct > 0.8:
        adj *= 0.88
        reasons["spread_high"] = f"-12% (spread percentile={spread_pct:.0%})"

    # ── Range Position ──
    range_pos = features.get("range_position", 0.5)
    if signal_action == "BUY" and range_pos > 0.8:
        adj *= 0.92
        reasons["range_top"] = "-8% (prix en haut de range pour achat)"
    elif signal_action == "SELL" and range_pos < 0.2:
        adj *= 0.92
        reasons["range_bottom"] = "-8% (prix en bas de range pour vente)"

    # Clamp final
    adj = max(0.50, min(1.50, adj))
    reasons["final_adj"] = round(adj, 3)

    return adj, reasons
