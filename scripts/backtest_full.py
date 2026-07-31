"""
Backtest MOM20x3 COMPLET — 26 symboles, coûts réels, filtres prod, volume, OnlineLearner.

Reproduit fidèlement la production (strategy.py + signal_pipeline + indicators) :
  - SYMBOL_CONFIG per-symbol (seuils, SL/TP, ADX slope, pullback)
  - Volume indicators (RVOL, CMF, OBV divergence)
  - OnlineLearner rolling WR adjustment
  - Conservation Mode (min_score)
  - Trailing ATR 4 niveaux + Partial TP 60%
  - Coûts : spread + slippage + commission

Usage:
    python scripts/backtest_full.py
    python scripts/backtest_full.py --tf H1
    python scripts/backtest_full.py --tf ALL --min-trades 50
"""

import json, os, sys
from datetime import datetime
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ═══════════════════════════════════════════════════════════════════════════════
#  IMPORTS PRODUCTION — SYMBOL_CONFIG, indicateurs, THRESHOLD_MAX
# ═══════════════════════════════════════════════════════════════════════════════
from engine_simple.strategy import (
    get_symbol_full_config,
    THRESHOLD_MAX as PROD_THRESHOLD_MAX,
    THRESHOLD_MIN as PROD_THRESHOLD_MIN,
)
from engine_simple.indicators import chaikin_money_flow, relative_volume, obv_divergence

# ═══════════════════════════════════════════════════════════════════════════════
#  PARAMÈTRES DE BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044  # 0.44% par trade (prod 0.004)
MIN_BARS = 80
TIMEOUT_BARS = {"H1": 120, "H4": 60, "D1": 30}
MAX_LOT = 1.0
MIN_TRADES = 50  # seuil minimum pour considérer un symbole

# Danger hours (config prod actuelle — 3h gardées)
DANGER_HOURS = [4, 7, 21]

# ═══════════════════════════════════════════════════════════════════════════════
#  COÛTS PAR SYMBOLE — 26 symboles
# ═══════════════════════════════════════════════════════════════════════════════
# format: (spread_pips, pip_size, pip_value, contract_size)
SYMBOL_COSTS = {
    # Forex majeurs
    "EURUSD": (1.5, 0.0001, 10.0, 100_000),
    "GBPUSD": (1.5, 0.0001, 10.0, 100_000),
    "USDJPY": (1.5, 0.01, 1.0, 100_000),
    "USDCAD": (1.5, 0.0001, 10.0, 100_000),
    "USDCHF": (1.5, 0.0001, 10.0, 100_000),
    "AUDUSD": (1.5, 0.0001, 10.0, 100_000),
    "NZDUSD": (1.5, 0.0001, 10.0, 100_000),
    # Forex crosses
    "EURJPY": (2.0, 0.01, 1.0, 100_000),
    "GBPJPY": (3.0, 0.01, 1.0, 100_000),
    "EURGBP": (1.5, 0.0001, 10.0, 100_000),
    "AUDJPY": (2.0, 0.01, 1.0, 100_000),
    # Crypto
    "BTCUSD": (10.0, 0.01, 1.0, 1),
    "ETHUSD": (10.0, 0.01, 1.0, 1),
    "SOLUSD": (10.0, 0.01, 1.0, 1),
    "BNBUSD": (10.0, 0.01, 1.0, 1),
    # Indices
    "US500.cash": (2.0, 0.01, 1.0, 1),
    "US30.cash": (2.0, 0.01, 1.0, 1),
    "US100.cash": (2.0, 0.01, 1.0, 1),
    "JP225.cash": (2.0, 0.01, 1.0, 1),
    "GER40.cash": (2.0, 0.01, 1.0, 1),
    "UK100.cash": (2.0, 0.01, 1.0, 1),
    # Commodities
    "XAUUSD": (5.0, 0.01, 1.0, 100),
    "XAGUSD": (10.0, 0.001, 5.0, 5000),
    "USOIL.cash": (5.0, 0.01, 1.0, 100),
    "UKOIL.cash": (5.0, 0.001, 10.0, 10000),
    "NATGAS.cash": (5.0, 0.001, 10.0, 10000),
}
DEFAULT_SPREAD = 2.0
DEFAULT_PIP = 0.0001
DEFAULT_PIP_VALUE = 10.0
DEFAULT_CONTRACT = 100_000
SLIPPAGE_PIPS = 1.0
COMMISSION_PER_100K = 7.0

# Niveaux de trailing par régime (PRODUCTION — ftmo_config.py Juillet 2026)
# 🔧 FIX 27 Juil 2026: aligné sur TRAILING_BY_REGIME + TRAILING_BY_SYMBOL
# Changements principaux: premier lock 1.0→1.50×ATR, buffers élargis 50%
from engine_simple.ftmo_config import get_trailing_for_symbol, get_be_buffer_for_symbol

TRAILING_LEVELS = {
    "RANGING": [(1.50, 0.75), (3.00, 0.45), (4.50, 0.28), (6.00, 0.14)],
    "TREND_UP": [(1.50, 1.20), (3.00, 0.65), (5.00, 0.38), (7.00, 0.20)],
    "TREND_DOWN": [(1.50, 1.20), (3.00, 0.65), (5.00, 0.38), (7.00, 0.20)],
    "HIGH_VOL": [(1.50, 1.20), (3.00, 0.80), (5.00, 0.50), (7.00, 0.28)],
    "LOW_VOL": [(1.50, 0.65), (2.50, 0.40), (3.50, 0.22), (5.00, 0.12)],
}

# ═══════════════════════════════════════════════════════════════════════════════
#  LISTE COMPLÈTE DES 26 SYMBOLES (triés par catégorie)
# ═══════════════════════════════════════════════════════════════════════════════
ALL_SYMBOLS = [
    # Forex majeurs
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCAD",
    "USDCHF",
    "AUDUSD",
    "NZDUSD",
    # Forex crosses
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "AUDJPY",
    # Crypto
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "BNBUSD",
    # Indices
    "US500.cash",
    "US30.cash",
    "US100.cash",
    "JP225.cash",
    "GER40.cash",
    "UK100.cash",
    # Commodities
    "XAUUSD",
    "XAGUSD",
    "USOIL.cash",
    "UKOIL.cash",
    "NATGAS.cash",
]

# ═══════════════════════════════════════════════════════════════════════════════
#  FONCTIONS UTILITAIRES
# ═══════════════════════════════════════════════════════════════════════════════


def get_specs(symbol):
    """Retourne (spread_pips, pip_size, pip_value, contract_size) pour un symbole."""
    if symbol in SYMBOL_COSTS:
        return SYMBOL_COSTS[symbol]
    return (DEFAULT_SPREAD, DEFAULT_PIP, DEFAULT_PIP_VALUE, DEFAULT_CONTRACT)


def get_sym_config(symbol):
    """Retourne la config complète d'un symbole (prod SYMBOL_CONFIG + defaults)."""
    return get_symbol_full_config(symbol)


# ═══════════════════════════════════════════════════════════════════════════════
#  INDICATEURS VECTORISÉS (complets + volume)
# ═══════════════════════════════════════════════════════════════════════════════


def precalc_indicators(high, low, close, volume, period=14):
    """Pré-calcule ATR, ADX, +DI, -DI, EMA20, volume indicators."""
    n = len(close)

    # ATR (SMA du True Range)
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(n, np.nan)
    for i in range(period, n):
        atr_arr[i] = np.mean(tr[i - period : i])

    # ADX avec +DI/-DI
    up = np.diff(high)
    down = -np.diff(low)
    pos_dm = np.where((up > down) & (up > 0), up, 0)
    neg_dm = np.where((down > up) & (down > 0), down, 0)

    tr_sm = np.full(n, np.nan)
    pos_sm = np.full(n, np.nan)
    neg_sm = np.full(n, np.nan)
    for i in range(period, n):
        tr_sm[i] = np.mean(tr[i - period : i])
        pos_sm[i] = np.mean(pos_dm[i - period : i])
        neg_sm[i] = np.mean(neg_dm[i - period : i])

    pos_di = np.where(tr_sm > 0, 100 * pos_sm / tr_sm, 0)
    neg_di = np.where(tr_sm > 0, 100 * neg_sm / tr_sm, 0)
    di_sum = pos_di + neg_di
    dx = np.where(di_sum > 0, 100 * np.abs(pos_di - neg_di) / di_sum, 0)

    adx_arr = np.full(n, np.nan)
    for i in range(period * 2, n):
        adx_arr[i] = np.mean(dx[i - period : i])

    # EMA20
    ema20 = np.full(n, np.nan)
    alpha = 2 / 21
    if n > 0:
        ema20[0] = close[0]
        for i in range(1, n):
            ema20[i] = close[i] * alpha + ema20[i - 1] * (1 - alpha)

    # Volume indicators vectorisés
    # RVOL: ratio volume actuel / moyenne 50 périodes
    rvol_arr = np.full(n, 1.0)
    for i in range(51, n):
        avg_v = np.mean(volume[i - 50 : i])
        rvol_arr[i] = volume[i] / avg_v if avg_v > 0.001 else 1.0

    # CMF: Chaikin Money Flow (20 périodes)
    cmf_arr = np.full(n, 0.0)
    for i in range(20, n):
        mfm = (
            (close[i - 19 : i + 1] - low[i - 19 : i + 1]) - (high[i - 19 : i + 1] - close[i - 19 : i + 1])
        ) / np.maximum(high[i - 19 : i + 1] - low[i - 19 : i + 1], 0.0001)
        mfv = mfm * volume[i - 19 : i + 1]
        cmf_arr[i] = np.sum(mfv) / max(np.sum(volume[i - 19 : i + 1]), 0.0001)

    # OBV divergence (20 périodes)
    obv_div_arr = np.full(n, 0.0)
    obv_type_arr = np.full(n, "none", dtype=object)
    # Pre-calc OBV
    obv_vals = np.full(n, 0.0)
    for i in range(1, n):
        if close[i] > close[i - 1]:
            obv_vals[i] = obv_vals[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv_vals[i] = obv_vals[i - 1] - volume[i]
        else:
            obv_vals[i] = obv_vals[i - 1]
    for i in range(20, n):
        price_slope = close[i] - close[i - 20]
        obv_slope_v = obv_vals[i] - obv_vals[i - 20]
        strength = abs(obv_slope_v) / max(abs(obv_vals[i - 20]), 1.0)
        if price_slope > 0 and obv_slope_v < 0:
            obv_type_arr[i] = "bearish"
            obv_div_arr[i] = min(strength, 1.0)
        elif price_slope < 0 and obv_slope_v > 0:
            obv_type_arr[i] = "bullish"
            obv_div_arr[i] = min(strength, 1.0)

    return atr_arr, adx_arr, pos_di, neg_di, ema20, rvol_arr, cmf_arr, obv_type_arr, obv_div_arr


# ═══════════════════════════════════════════════════════════════════════════════
#  GÉNÉRATION DE SIGNAUX — Production complète + volume + OL
# ═══════════════════════════════════════════════════════════════════════════════


class OnlineLearnerEmu:
    """Émulation simplifiée de l'OnlineLearner — ajuste les thresholds selon WR_200."""

    def __init__(self):
        self.windows = {}  # symbol -> list of results (1.0 win, -1.0 loss)

    def record_result(self, symbol, win):
        if symbol not in self.windows:
            self.windows[symbol] = []
        self.windows[symbol].append(1.0 if win else -1.0)
        if len(self.windows[symbol]) > 200:
            self.windows[symbol] = self.windows[symbol][-200:]

    def get_adapted_threshold(self, symbol, base_thresh, base_thresh_ranging, is_trending):
        """Retourne threshold ajusté selon WR, ou None si pas d'ajustement."""
        w = self.windows.get(symbol, [])
        if len(w) < 20:
            return None  # pas assez de données
        wr = sum(1 for r in w if r > 0) / len(w)
        if is_trending:
            th = base_thresh
        else:
            th = base_thresh_ranging

        # Prod logic: WR>82% → seuil -0.5 (agressif), WR<70% → seuil +0 (neutre)
        if wr > 0.82 and len(w) >= 30:
            return max(PROD_THRESHOLD_MIN, th - 0.5)
        elif wr < 0.70 and len(w) >= 50:
            return min(PROD_THRESHOLD_MAX, th)
        return th  # neutre


def gen_signal_bar(
    i,
    close,
    high,
    low,
    volume,
    times_dt,
    atr_arr,
    adx_arr,
    pos_di,
    neg_di,
    ema20,
    rvol_arr,
    cmf_arr,
    obv_type_arr,
    obv_div_arr,
    symbol,
    sym_cfg,
    ol_emu,
):
    """Génère un signal pour la barre i — reproduction fidèle de la prod."""
    atr_v = atr_arr[i]
    adx_v = adx_arr[i]
    if np.isnan(atr_v) or atr_v <= 0 or np.isnan(adx_v) or adx_v <= 0:
        return None, None  # (signal, raw_score)

    # Danger hours + weekend
    if times_dt[i].weekday() >= 5:
        return None, None
    if times_dt[i].hour in DANGER_HOURS:
        return None, None

    mp = sym_cfg.get("momentum_period", 20)
    if i < mp + 1:
        return None, None

    # Seuils de la config prod
    thresh_trending = sym_cfg.get("threshold_trending", 2.5)
    thresh_ranging = sym_cfg.get("threshold_ranging", 2.0)
    adx_thresh = sym_cfg.get("adx_thresh", 22)
    adx_slope_th = sym_cfg.get("adx_slope_threshold", -5.0)
    adx_slope_th_strong = sym_cfg.get("adx_slope_threshold_strong", -8.0)
    pb_band_t = sym_cfg.get("pullback_band_trending", 0.5)
    pb_band_r = sym_cfg.get("pullback_band_ranging", 0.3)
    min_score = sym_cfg.get("min_score", 0.60)
    cmf_th = sym_cfg.get("cmf_threshold", 0.10)
    obv_pen_high = sym_cfg.get("obv_div_penalty_high", 0.70)
    obv_pen_low = sym_cfg.get("obv_div_penalty_low", 0.85)

    # ——— MOMENTUM ———
    mom = float(close[i] - close[i - mp])
    if np.isnan(mom) or np.isinf(mom):
        return None, None
    mom_abs = abs(mom)

    # Régime basé sur ADX (hystérésis prod: 22 entry)
    is_trending = adx_v >= adx_thresh

    # Ajustement OnlineLearner sur les thresholds
    ol_adj = ol_emu.get_adapted_threshold(symbol, thresh_trending, thresh_ranging, is_trending)
    if ol_adj is not None:
        if is_trending:
            thresh = ol_adj
        else:
            thresh = ol_adj
    else:
        thresh = thresh_trending if is_trending else thresh_ranging

    thresh = max(PROD_THRESHOLD_MIN, min(PROD_THRESHOLD_MAX, thresh))
    threshold_value = thresh * atr_v

    # Filtre momentum
    if mom_abs < threshold_value:
        return None, None

    # Raw score
    raw_score = min(1.0, mom_abs / (threshold_value * 2)) if mom_abs > 0 else 0.0

    # ——— ADX SLOPE ———
    half = max(14, mp // 2)
    adx_slope = 0.0
    if i >= half + 28 and not np.isnan(adx_arr[i]) and not np.isnan(adx_arr[i - half]):
        adx_slope = adx_arr[i] - adx_arr[i - half]

    slope_th = adx_slope_th_strong if raw_score > 0.70 else adx_slope_th
    if adx_slope < slope_th:
        return None, None  # skip: ADX en baisse

    # ——— FILTRE +DI/-DI ———
    pdi = pos_di[i]
    ndi = neg_di[i]
    if np.isnan(pdi) or np.isnan(ndi):
        return None, None

    dir_ok = True
    di_sugg = None
    if mom > 0:
        if pdi <= ndi * 0.8:
            dir_ok = False
            di_sugg = "SELL"
    else:
        if ndi <= pdi * 0.8:
            dir_ok = False
            di_sugg = "BUY"

    # ——— SIGNAL DIRECTIONNEL ———
    action = None
    score = 0.0
    if mom > 0 and mom_abs >= threshold_value:
        action = "BUY"
        score = 0.35 + raw_score * 0.60
    elif mom < 0 and mom_abs >= threshold_value:
        action = "SELL"
        score = 0.35 + raw_score * 0.60

    if action is None:
        return None, None

    # ——— DI OVERRIDE (short-term momentum) ———
    if not dir_ok and di_sugg is not None and i >= 7:
        short_mom = float(close[i] - close[i - 5])
        short_mom_abs = abs(short_mom)
        ot = threshold_value * 2.0 if adx_v < 22 else threshold_value * 0.5
        if di_sugg == "SELL" and short_mom < -ot:
            action = "SELL"
            score = 0.35 + min(1.0, short_mom_abs / (threshold_value * 2)) * 0.60
            dir_ok = True
        elif di_sugg == "BUY" and short_mom > ot:
            action = "BUY"
            score = 0.35 + min(1.0, short_mom_abs / (threshold_value * 2)) * 0.60
            dir_ok = True

    if not dir_ok:
        return None, None

    # ——— PULLBACK FILTER ———
    ev = ema20[i]
    if np.isnan(ev) or ev <= 0:
        return None, None
    pb_dist = (close[i] - ev) / ev * 100
    pb_mult = pb_band_t if is_trending else pb_band_r
    pb_band = max(0.05, min(1.0, (pb_mult * atr_v) / ev * 100))
    if abs(pb_dist) >= pb_band:
        return None, None  # skip: pas en pullback

    # Score final avant volume
    final_score = min(0.99, score)

    # ——— CONSERVATION MODE (min_score) ———
    if final_score < min_score:
        return None, None

    # ——— VOLUME INDICATORS (RVOL, CMF, OBV) ———
    rvol = rvol_arr[i]
    cmf = cmf_arr[i]
    obv_type = obv_type_arr[i]
    obv_str = obv_div_arr[i]

    # RVOL impact
    if rvol < 0.5:
        final_score *= 0.75  # volume anormalement bas
    elif rvol > 2.0:
        final_score = min(0.95, final_score * 1.10)  # volume fort

    # CMF impact
    cmf_positive = cmf > cmf_th
    cmf_negative = cmf < -cmf_th
    if action == "BUY" and cmf_positive:
        final_score = min(0.95, final_score * 1.08)  # aligné
    elif action == "SELL" and cmf_negative:
        final_score = min(0.95, final_score * 1.08)  # aligné
    elif action == "BUY" and cmf_negative:
        final_score *= 0.85  # conflit
    elif action == "SELL" and cmf_positive:
        final_score *= 0.85  # conflit

    # OBV divergence impact
    if obv_str != "none" and obv_type != "none":
        if obv_str > 0.5:
            final_score *= obv_pen_high  # divergence forte
        else:
            final_score *= obv_pen_low  # divergence faible

    # ——— RÉGIME (pour trailing) ———
    regime = "RANGING"
    if is_trending and action == "BUY":
        regime = "TREND_UP"
    elif is_trending and action == "SELL":
        regime = "TREND_DOWN"

    signal = {
        "action": action,
        "score": final_score,
        "atr": atr_v,
        "adx": adx_v,
        "regime": regime,
        "sl_atr": sym_cfg.get("sl_atr_trending", 2.0) if is_trending else sym_cfg.get("sl_atr_ranging", 1.5),
        "tp_atr": sym_cfg.get("tp_atr_trending", 5.0) if is_trending else sym_cfg.get("tp_atr_ranging", 4.0),
        "threshold_value": threshold_value,
    }
    return signal, raw_score


# ═══════════════════════════════════════════════════════════════════════════════
#  SimTrade — trade simulé avec coûts, trailing, partial TP
# ═══════════════════════════════════════════════════════════════════════════════


class SimTrade:
    __slots__ = (
        "symbol",
        "action",
        "entry",
        "sl",
        "tp",
        "atr_val",
        "regime",
        "open_bar",
        "open_time",
        "direction",
        "closed",
        "result",
        "profit_usd",
        "profit_usd_cost",
        "peak_price",
        "trailing_sl",
        "partial_closed",
        "partial_locked_profit",
        "partial_locked_profit_cost",
        "remaining_vol_ratio",
        "bars_held",
        "close_time",
        "close_price",
        "lot",
        "_pip_size",
        "_pip_value",
        "_contract_size",
        "_spread_pips",
        "commission_usd",
        "cost_pips",
    )

    def __init__(self, symbol, action, entry, sl, tp, atr_val, regime, bar_idx, bar_time, balance):
        self.symbol = symbol
        self.action = action
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.atr_val = atr_val
        self.regime = regime
        self.open_bar = bar_idx
        self.open_time = bar_time
        self.direction = 0 if action == "BUY" else 1
        self.closed = False
        self.result = None
        self.profit_usd = 0.0
        self.profit_usd_cost = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.partial_locked_profit = 0.0
        self.partial_locked_profit_cost = 0.0
        self.remaining_vol_ratio = 1.0
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.commission_usd = 0.0
        self.cost_pips = 0.0
        sp, ps, pv, cs = get_specs(symbol)
        self._spread_pips = sp
        self._pip_size = ps
        self._pip_value = pv
        self._contract_size = cs
        self._calc_lot(entry, sl, balance)

    def _pip_value_corrected(self, price=None):
        pv = self._pip_value
        if self.symbol in ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"):
            pip_per_lot_quote = self._contract_size * self._pip_size
            rate = abs(price if price is not None else self.entry)
            if self.symbol == "USDJPY":
                pv = pip_per_lot_quote / max(rate, 1e-10)
            else:
                pv = pip_per_lot_quote / 150.0
        return pv

    def _notional_usd(self):
        raw = self.lot * self._contract_size * abs(self.entry)
        if self.symbol in ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"):
            if self.symbol == "USDJPY":
                return self.lot * self._contract_size
            return raw / 150.0
        return raw

    def _calc_lot(self, entry, sl, balance):
        dist = abs(entry - sl)
        if dist > 0:
            risk = balance * RISK_PER_TRADE
            pips = dist / self._pip_size
            if pips > 0:
                pv = self._pip_value_corrected(entry)
                self.lot = risk / (pips * pv)
        self.lot = max(0.01, min(MAX_LOT, self.lot))

    def check_sl_tp(self, high, low, close, bar_idx, bar_time):
        if self.closed:
            return
        hit = False
        if self.direction == 0:
            if low <= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                hit = True
            elif high >= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                hit = True
        else:
            if high >= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                hit = True
            elif low <= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                hit = True
        if hit:
            self.closed = True
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            self._calc_pnl()

    def _calc_pnl(self):
        """Calcule PnL final en incluant partial_locked_profit (50% fermé au partial TP)."""
        pv = self._pip_value_corrected(self.close_price)
        usdpp = self.lot * self.remaining_vol_ratio * pv
        if self.direction == 0:
            pips = (self.close_price - self.entry) / self._pip_size
        else:
            pips = (self.entry - self.close_price) / self._pip_size
        remaining_pnl = pips * usdpp
        self.profit_usd = self.partial_locked_profit + remaining_pnl
        self.cost_pips = self._spread_pips + SLIPPAGE_PIPS
        notional = self._notional_usd() * self.remaining_vol_ratio
        self.commission_usd = (notional / 100_000) * COMMISSION_PER_100K * 2
        pips_cost = pips - self.cost_pips
        remaining_pnl_cost = pips_cost * usdpp - self.commission_usd
        self.profit_usd_cost = self.partial_locked_profit_cost + remaining_pnl_cost

    def update_peak(self, high, low):
        if self.closed:
            return
        if self.direction == 0:
            if high > self.peak_price:
                self.peak_price = high
        else:
            if low < self.peak_price:
                self.peak_price = low

    def update_trailing(self, atr_v):
        if self.closed or atr_v <= 0:
            return
        if self.direction == 0:
            profit_atr = (self.peak_price - self.entry) / atr_v
        else:
            profit_atr = (self.entry - self.peak_price) / atr_v
        # Niveaux de trailing : per-symbol (production) ou fallback par régime
        lvls = get_trailing_for_symbol(self.symbol, self.regime)
        first_thresh = lvls[0][0] if lvls else 1.50
        if profit_atr <= first_thresh:
            return
        dm = lvls[-1][1]
        for th, d in reversed(lvls):
            if profit_atr > th:
                dm = d
                break
        dist = dm * atr_v
        if self.direction == 0:
            ns = self.peak_price - dist
            if ns > self.trailing_sl:
                self.trailing_sl = ns
        else:
            ns = self.peak_price + dist
            if ns < self.trailing_sl:
                self.trailing_sl = ns

    def check_partial(self, atr_v, current_price=None):
        """Partial TP: ferme 50% à 70% du TP, SL → BE+ buffer pour le reste.

        Reproduction fidèle de trailer.py _check_partial_tp (production).
        🔧 FIX 27 Juil 2026: seuil 0.60→0.70, fermeture 50% réelle, BE buffer per-symbol.
        """
        if self.closed or self.partial_closed or atr_v <= 0:
            return
        price = current_price if current_price is not None else self.peak_price
        if self.direction == 0:
            if price <= self.entry:
                return
            prog = (price - self.entry) / max(self.tp - self.entry, 1e-10)
        else:
            if price >= self.entry:
                return
            prog = (self.entry - price) / max(self.entry - self.tp, 1e-10)
        # 🔧 24 Juil 2026: 0.60→0.70 — retarder le partial TP pour capturer plus de profit
        if prog < 0.70:
            return

        # 👉 FERMETURE 50% à ce niveau de prix (comme production: mt5.order_send)
        self.partial_closed = True
        self.remaining_vol_ratio = 0.5  # il reste 50% de la position

        # Profit sur la moitié fermée : prix_partiel × (lot × 0.5)
        pv = self._pip_value_corrected(price)
        usdpp_half = self.lot * 0.5 * pv
        if self.direction == 0:
            pips_partial = (price - self.entry) / self._pip_size
        else:
            pips_partial = (self.entry - price) / self._pip_size
        self.partial_locked_profit = pips_partial * usdpp_half

        # Coût sur la moitié fermée
        notional_half = self._notional_usd() * 0.5
        commission_half = (notional_half / 100_000) * COMMISSION_PER_100K
        pips_cost_partial = pips_partial - self.cost_pips * 0.5  # demi-spread pour 50%
        self.partial_locked_profit_cost = pips_cost_partial * usdpp_half - commission_half

        # 🎯 SL → BE+ buffer pour la moitié restante (get_be_buffer_for_symbol comme prod)
        be_mult = get_be_buffer_for_symbol(self.symbol, self.regime)
        be_buffer = be_mult * atr_v
        if self.direction == 0:
            be_sl = self.entry + be_buffer
            if be_sl > self.trailing_sl:
                self.trailing_sl = be_sl
        else:
            be_sl = self.entry - be_buffer
            if be_sl < self.trailing_sl:
                self.trailing_sl = be_sl

    def to_dict(self):
        return dict(
            symbol=self.symbol,
            action=self.action,
            regime=self.regime,
            entry=round(self.entry, 5),
            sl=round(self.sl, 5),
            tp=round(self.tp, 5),
            close_price=round(self.close_price, 5),
            result=self.result,
            profit_usd=round(self.profit_usd, 2),
            profit_usd_cost=round(self.profit_usd_cost, 2),
            cost_pips=round(self.cost_pips, 1),
            commission_usd=round(self.commission_usd, 2),
            lot=round(self.lot, 4),
            partial_closed=self.partial_closed,
            partial_locked_profit=round(self.partial_locked_profit, 2),
            remaining_vol_ratio=round(self.remaining_vol_ratio, 2),
            bars_held=self.bars_held,
            open_time=str(self.open_time)[:19],
            close_time=str(self.close_time)[:19] if self.close_time else "",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKTEST PAR SYMBOLE
# ═══════════════════════════════════════════════════════════════════════════════


def backtest_symbol(symbol, timeframe, df):
    """Backtest un symbole sur un TF — production complète avec volume + OL."""
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    volume = df.get("volume", df.get("tick_volume", np.ones(len(close)))).values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    sym_cfg = get_sym_config(symbol)
    times_dt = pd.to_datetime(times)

    # Pré-calcul de tous les indicateurs
    atr_arr, adx_arr, pdi, ndi, ema20, rvol_arr, cmf_arr, obv_type_arr, obv_div_arr = precalc_indicators(
        high, low, close, volume
    )

    ol_emu = OnlineLearnerEmu()

    trades = []
    open_trades = []
    bars_since_last = 999

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0

        # Mise à jour des trades ouverts
        still_open = []
        for t in open_trades:
            t.update_peak(high[i], low[i])
            tatr = atr_v if atr_v > 0 else t.atr_val
            t.check_partial(tatr, current_price=close[i])
            t.update_trailing(tatr)
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            if not t.closed and i - t.open_bar > TIMEOUT_BARS.get(timeframe, 120):
                t.closed = True
                t.close_price = close[i]
                t.close_time = times[i]
                t.result = "TIMEOUT"
                t.bars_held = i - t.open_bar
                t._calc_pnl()
            if not t.closed:
                still_open.append(t)
            else:
                # Enregistrer dans OnlineLearner
                ol_emu.record_result(t.symbol, t.profit_usd_cost > 0)
        open_trades = still_open

        bars_since_last += 1
        if atr_v <= 0:
            continue

        # Génération du signal
        signal, raw_score = gen_signal_bar(
            i,
            close,
            high,
            low,
            volume,
            times_dt,
            atr_arr,
            adx_arr,
            pdi,
            ndi,
            ema20,
            rvol_arr,
            cmf_arr,
            obv_type_arr,
            obv_div_arr,
            symbol,
            sym_cfg,
            ol_emu,
        )

        if signal is None or bars_since_last < 3:
            continue

        # Calcul SL/TP
        sd = signal["sl_atr"] * atr_v
        td = signal["tp_atr"] * atr_v
        if signal["action"] == "BUY":
            sp = close[i] - sd
            tp = close[i] + td
        else:
            sp = close[i] + sd
            tp = close[i] - td

        # Vérifier RR ≥ 1.8 (min_rr de la prod)
        min_rr = sym_cfg.get("min_rr", 1.5)
        if sd > 0 and td / sd < min_rr:
            continue

        # Pas de trade en conflit avec même direction que les ouverts
        if any(t.action == signal["action"] for t in open_trades):
            continue

        # Exécution
        t = SimTrade(symbol, signal["action"], close[i], sp, tp, atr_v, signal["regime"], i, times[i], INITIAL_BALANCE)
        trades.append(t)
        open_trades.append(t)
        bars_since_last = 0

    return trades


def backtest_multi_tf(symbol):
    """Backtest un symbole sur H1+H4+D1 et agrège."""
    all_t = []
    for tf in ("H1", "H4", "D1"):
        fp = Path(f"data/historical/{symbol}_{tf}.parquet")
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)
        if len(df) < MIN_BARS:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
        if len(df) < MIN_BARS:
            continue
        trades = backtest_symbol(symbol, tf, df)
        all_t.extend(trades)
    return all_t


# ═══════════════════════════════════════════════════════════════════════════════
#  MÉTRIQUES
# ═══════════════════════════════════════════════════════════════════════════════


def compute_metrics(closed):
    if not closed:
        return {"n": 0}
    wins = [t for t in closed if t.profit_usd_cost > 0]
    losses = [t for t in closed if t.profit_usd_cost <= 0]
    n = len(closed)
    nw = len(wins)
    wr = nw / n * 100 if n > 0 else 0
    tp = sum(t.profit_usd_cost for t in closed)
    gp = sum(max(0, t.profit_usd_cost) for t in closed)
    gl = abs(sum(min(0, t.profit_usd_cost) for t in closed))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)

    # Drawdown
    peak = INITIAL_BALANCE
    dd_max = 0.0
    bal = INITIAL_BALANCE
    for t in sorted(closed, key=lambda x: x.close_time or x.open_time):
        bal += t.profit_usd_cost
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)

    # P-value binomiale
    p = 1.0
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))

    # Profit factor par année (rough)
    years = set()
    for t in closed:
        if t.close_time:
            try:
                years.add(pd.Timestamp(t.close_time).year)
            except Exception:
                pass
    num_years = max(len(years), 1)

    return {
        "n": n,
        "wins": nw,
        "losses": n - nw,
        "win_rate": round(wr, 1),
        "total_pnl": round(tp, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "p_value": round(p, 4),
        "significant": p < 0.05 and wr > 50,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "avg_pnl": round(tp / n, 2) if n else 0,
        "avg_win": round(gp / nw, 2) if nw else 0,
        "avg_loss": round(-gl / (n - nw), 2) if n > nw else 0,
        "avg_pnl_yearly": round(tp / num_years, 2),
        "years": num_years,
    }


def avg_costs(closed):
    if not closed:
        return {}
    c = [abs(t.profit_usd - t.profit_usd_cost) for t in closed]
    return {
        "avg_cost_pips": round(float(np.mean([t.cost_pips for t in closed])), 2),
        "avg_commission_usd": round(float(np.mean([t.commission_usd for t in closed])), 2),
        "avg_total_cost_usd": round(float(np.mean(c)), 2),
        "total_costs_usd": round(sum(c), 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tf", choices=["H1", "H4", "D1", "ALL"], default="ALL", help="Timeframe: H1, H4, D1, ou ALL (cumulé)"
    )
    parser.add_argument(
        "--min-trades", type=int, default=MIN_TRADES, help="Nombre minimum de trades pour qualifier un symbole"
    )
    parser.add_argument(
        "--symbols", type=str, default=None, help="Symboles à tester (séparés par des virgules, défaut=tous)"
    )
    args = parser.parse_args()

    data_dir = Path("data/historical")
    if not data_dir.exists():
        print("❌ data/historical/ introuvable")
        sys.exit(1)

    # Sélection des symboles
    if args.symbols:
        test_symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        test_symbols = ALL_SYMBOLS

    print("=" * 130)
    print(f"  BACKTEST MOM20x3 COMPLET — Production Replica (26 symboles, 2012-2026)")
    print(f"  TF: {args.tf}  |  Min trades: {args.min_trades}  |  Symboles: {len(test_symbols)}")
    print(f"  Filtres: ADX slope + DI + DI Override + Pullback + Volume (RVOL/CMF/OBV)")
    print(f"  OnlineLearner: rolling WR 200 | Conservation Mode: min_score per-symbol")
    print(f"  Coûts: spread + slippage ({SLIPPAGE_PIPS} pip) + commission (${COMMISSION_PER_100K}/100K)")
    print(f"  Risk: {RISK_PER_TRADE * 100:.2f}%/trade, THRESHOLD_MAX={PROD_THRESHOLD_MAX}")
    print("=" * 130)

    start_all = datetime.utcnow()
    results = {}

    for sym in test_symbols:
        t0 = datetime.utcnow()
        sym_cfg = get_sym_config(sym)
        sym_tf = sym_cfg.get("timeframe", "H1")

        if args.tf == "ALL":
            all_t = backtest_multi_tf(sym)
        else:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            if len(df) < MIN_BARS:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
            if len(df) < MIN_BARS:
                continue
            all_t = backtest_symbol(sym, args.tf, df)

        closed = [t for t in all_t if t.closed]
        elapsed = (datetime.utcnow() - t0).total_seconds()

        if len(closed) < args.min_trades:
            continue

        m = compute_metrics(closed)
        costs = avg_costs(closed)

        # Calcul des années couvertes
        bars_total = 0
        if args.tf == "ALL":
            for tf in ("H1", "H4", "D1"):
                fp = data_dir / f"{sym}_{tf}.parquet"
                if fp.exists():
                    df_sz = pd.read_parquet(fp)
                    bars_total += len(df_sz)
        else:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if fp.exists():
                df_sz = pd.read_parquet(fp)
                bars_total = len(df_sz)

        results[sym] = {
            "metrics": m,
            "costs": costs,
            "survives": m.get("significant", False) and m["win_rate"] > 50 and m["total_pnl"] > 0,
            "total_bars": bars_total,
            "elapsed_s": round(elapsed, 1),
            "config_tf": sym_tf,
        }

        emoji = "✅" if results[sym]["survives"] else "⚠️" if m["win_rate"] > 50 else "❌"
        print(
            f"  {emoji} {sym:12s} {args.tf:>4s}  {m['n']:>5d} trades  "
            f"WR={m['win_rate']:>5.1f}%  PnL=${m['total_pnl']:>+9.2f}  "
            f"PF={m['profit_factor']:>5.2f}  DD={m['max_drawdown_pct']:>5.1f}%  "
            f"Cost=${costs.get('avg_total_cost_usd', 0):>+5.2f}/tr  "
            f"{elapsed:.1f}s"
        )

    total_elapsed = (datetime.utcnow() - start_all).total_seconds()

    if not results:
        print("\n❌ Aucun résultat. Vérifie les données dans data/historical/")
        return

    # Classement par PnL
    ranked = sorted(results.items(), key=lambda x: x[1]["metrics"]["total_pnl"], reverse=True)

    print(f"\n{'=' * 130}")
    print(f"  🏆 CLASSEMENT — MOM20x3 COMPLET ({args.tf}) — 2012-2026")
    print(f"  Prod replica: SYMBOL_CONFIG + volume indicators + OnlineLearner + min_score")
    print(f"{'=' * 130}")
    print(
        f"  {'#':>2s} {'Symbole':12s} {'TF':>4s} {'Trades':>6s} {'WR':>5s}  {'PnL':>10s}  "
        f"{'PF':>5s}  {'DD':>5s}  {'AvgWin':>7s}  {'AvgLoss':>7s}  {'Cost/tr':>7s}  {'Signif':>7s}"
    )
    print(f"  {'-' * 115}")

    survivors = []
    for rank, (sym, r) in enumerate(ranked[:30], 1):
        m = r["metrics"]
        c = r["costs"]
        survive = "✅" if r["survives"] else "⚠️" if m["win_rate"] > 50 else "❌"
        sig = "✅" if m.get("significant") else "❌"
        tf_label = r.get("config_tf", "H1") if args.tf == "ALL" else args.tf
        print(
            f"  {rank:>2d} {sym:12s} {tf_label:>4s} {m['n']:>6d} {m['win_rate']:>4.1f}%{survive} "
            f"${m['total_pnl']:>+9.2f} {m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>5.1f}% "
            f"${m.get('avg_win', 0):>+6.2f} ${m.get('avg_loss', 0):>+6.2f} "
            f"${c.get('avg_total_cost_usd', 0):>+6.2f} {sig}"
        )
        if r["survives"]:
            survivors.append(sym)

    # Totaux
    total_n = sum(r["metrics"]["n"] for _, r in ranked)
    total_pnl = sum(r["metrics"]["total_pnl"] for _, r in ranked)
    total_wins = sum(r["metrics"]["wins"] for _, r in ranked)
    total_gp = sum(r["metrics"]["gross_profit"] for _, r in ranked)
    total_gl = sum(r["metrics"]["gross_loss"] for _, r in ranked)
    total_wr = total_wins / total_n * 100 if total_n else 0
    total_pf = total_gp / total_gl if total_gl > 0 else 0
    print(f"  {'-' * 115}")
    print(f"  {'TOTAL':>15s} {total_n:>6d} {total_wr:>4.1f}%   ${total_pnl:>+9.2f} {total_pf:>5.2f}")
    print()

    # Survivants
    print(f"  🏆 SYMBOLES QUI SURVIVENT (WR>50%, PnL>0, p<0.05)")
    if survivors:
        for i, sym in enumerate(survivors, 1):
            r = results[sym]
            m = r["metrics"]
            c = r["costs"]
            avg_pnl_per_trade = m["total_pnl"] / m["n"] if m["n"] else 0
            print(
                f"  {i:>2d}. {sym:12s}  {m['n']:>5d} trades  WR={m['win_rate']:>5.1f}%  "
                f"PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>4.2f}  "
                f"DD={m['max_drawdown_pct']:>4.1f}%  "
                f"${avg_pnl_per_trade:>+.2f}/trade  Cost=${c.get('avg_total_cost_usd', 0):>+.2f}/tr"
            )
    else:
        print("  ❌ Aucun symbole ne survit aux coûts réels avec ces paramètres.")

    # Perdants
    losers = [sym for sym, r in ranked if not r["survives"] and r["metrics"]["n"] >= args.min_trades]
    if losers:
        print(f"\n  ❌ SYMBOLES QUI ÉCHOUENT ({len(losers)}):")
        for sym in losers[:10]:
            r = results[sym]
            m = r["metrics"]
            print(
                f"     {sym:12s}  {m['n']:>5d} trades  WR={m['win_rate']:>5.1f}%  "
                f"PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>4.2f}"
            )

    # Recommandation
    print(f"\n  {'═' * 60}")
    print(f"  RECOMMANDATION POUR LE CHALLENGE FTMO 200K$")
    print(f"  {'═' * 60}")
    if survivors:
        pnl_surv = sum(results[s]["metrics"]["total_pnl"] for s in survivors)
        print(f"  Symboles recommandés: {', '.join(survivors)}")
        print(f"  PnL net cumulé: ${pnl_surv:+.2f}")
        print(f"  ✅ Portefeuille de {len(survivors)} symboles avec edge réel après coûts.")
    else:
        print(f"  ❌ Aucun symbole ne survit. MOM20x3 n'a pas d'edge net après coûts.")
        print(f"  → Solutions:")
        print(f"    1. Réduire les coûts (broker plus serré)")
        print(f"    2. Augmenter les lots")
        print(f"    3. Changer de timeframe")
        print(f"    4. Changer de stratégie")

    # Sauvegarde
    out = Path("runtime")
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "timeframe": args.tf,
            "initial_balance": INITIAL_BALANCE,
            "risk_per_trade": RISK_PER_TRADE,
            "slippage_pips": SLIPPAGE_PIPS,
            "commission_per_100k": COMMISSION_PER_100K,
            "min_trades": args.min_trades,
            "n_symbols": len(test_symbols),
            "filters": "ADX_slope+DI+DI_override+Pullback+Volume(RVOL/CMF/OBV)+OnlineLearner+min_score",
        },
        "per_symbol": {
            sym: {
                "n": r["metrics"]["n"],
                "win_rate": r["metrics"]["win_rate"],
                "pnl": r["metrics"]["total_pnl"],
                "pf": r["metrics"]["profit_factor"],
                "dd": r["metrics"]["max_drawdown_pct"],
                "p_value": r["metrics"]["p_value"],
                "significant": r["metrics"]["significant"],
                "survives": r["survives"],
                "cost_per_trade": r["costs"].get("avg_total_cost_usd", 0),
            }
            for sym, r in results.items()
        },
        "ranking": [
            {
                "rank": i + 1,
                "symbol": s,
                "pnl": results[s]["metrics"]["total_pnl"],
                "wr": results[s]["metrics"]["win_rate"],
                "pf": results[s]["metrics"]["profit_factor"],
                "survives": results[s]["survives"],
            }
            for i, (s, _) in enumerate(ranked)
        ],
        "survivors": survivors,
        "totals": {"n": total_n, "wr": round(total_wr, 1), "pnl": round(total_pnl, 2), "pf": round(total_pf, 2)},
    }

    with open(out / "backtest_full_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Rapport: runtime/backtest_full_report.json")
    print(f"  Terminé en {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
