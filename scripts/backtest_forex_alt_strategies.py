"""
Backtest 5 Stratégies Alternatives — 7 Paires Forex Primaires
Compare : MA Crossover, Bollinger Mean Rev, RSI Extreme, Donchian Breakout, MACD

Usage:
    python scripts/backtest_forex_alt_strategies.py
    python scripts/backtest_forex_alt_strategies.py --tf H1
    python scripts/backtest_forex_alt_strategies.py --tf ALL
"""

import json
import os
import sys
from datetime import datetime
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ═══════════════════════════════════════════════════════════════
# PARAMÈTRES GLOBAUX
# ═══════════════════════════════════════════════════════════════

INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044
MIN_BARS = 100
TIMEOUT_BARS = {"H1": 80, "H4": 40, "D1": 20}
MAX_LOT = 1.0
MIN_TRADES = 50

# Coûts (identique aux autres backtests)
DEFAULT_SPREAD = 2.0
DEFAULT_PIP = 0.0001
DEFAULT_PIP_VALUE = 10.0
DEFAULT_CONTRACT = 100_000
SLIPPAGE_PIPS = 1.0
COMMISSION_PER_100K = 7.0

# 7 paires Forex primaires
FOREX_MAJORS = ["EURUSD", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "USDJPY"]

SYMBOL_COSTS = {
    "EURUSD": (1.5, 0.0001, 10.0, 100_000),
    "GBPUSD": (1.5, 0.0001, 10.0, 100_000),
    "USDJPY": (1.5, 0.01, 1.0, 100_000),
    "USDCAD": (1.5, 0.0001, 10.0, 100_000),
    "USDCHF": (1.5, 0.0001, 10.0, 100_000),
    "AUDUSD": (1.5, 0.0001, 10.0, 100_000),
    "NZDUSD": (1.5, 0.0001, 10.0, 100_000),
}

# Trailing (commun à toutes les stratégies)
TRAILING_LEVELS = {
    "TREND_UP": [(1.0, 0.60), (2.0, 0.40), (3.0, 0.20), (5.0, 0.10)],
    "TREND_DOWN": [(1.0, 0.60), (2.0, 0.40), (3.0, 0.20), (5.0, 0.10)],
    "RANGING": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.10)],
}

DANGER_HOURS = [0, 1, 2, 3, 4, 5, 22, 23]


def get_specs(symbol):
    if symbol in SYMBOL_COSTS:
        return SYMBOL_COSTS[symbol]
    return (DEFAULT_SPREAD, DEFAULT_PIP, DEFAULT_PIP_VALUE, DEFAULT_CONTRACT)


# ═══════════════════════════════════════════════════════════════
#  INDICATEURS COMMUNS
# ═══════════════════════════════════════════════════════════════


def precalc_indicators(high, low, close, period=14):
    n = len(close)
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(n, np.nan)
    for i in range(period, n):
        atr_arr[i] = np.mean(tr[i - period : i])
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

    # EMAs
    ema12 = np.full(n, np.nan)
    alpha12 = 2 / 13
    if n > 0:
        ema12[0] = close[0]
        for i in range(1, n):
            ema12[i] = close[i] * alpha12 + ema12[i - 1] * (1 - alpha12)

    ema20 = np.full(n, np.nan)
    alpha = 2 / 21
    if n > 0:
        ema20[0] = close[0]
        for i in range(1, n):
            ema20[i] = close[i] * alpha + ema20[i - 1] * (1 - alpha)

    ema26 = np.full(n, np.nan)
    alpha26 = 2 / 27
    if n > 0:
        ema26[0] = close[0]
        for i in range(1, n):
            ema26[i] = close[i] * alpha26 + ema26[i - 1] * (1 - alpha26)

    ema50 = np.full(n, np.nan)
    alpha50 = 2 / 51
    if n > 0:
        ema50[0] = close[0]
        for i in range(1, n):
            ema50[i] = close[i] * alpha50 + ema50[i - 1] * (1 - alpha50)

    # Bollinger Bands
    bb_mid = ema20.copy()
    bb_upper = np.full(n, np.nan)
    bb_lower = np.full(n, np.nan)
    for i in range(20, n):
        std = np.std(close[i - 20 : i])
        bb_upper[i] = bb_mid[i] + 2 * std
        bb_lower[i] = bb_mid[i] - 2 * std

    # RSI
    rsi_arr = np.full(n, np.nan)
    if n > 14:
        gains = np.maximum(np.diff(close), 0)
        losses = -np.minimum(np.diff(close), 0)
        avg_gain = np.mean(gains[:14])
        avg_loss = np.mean(losses[:14])
        for i in range(14, n):
            avg_gain = (avg_gain * 13 + gains[i - 1]) / 14 if i > 14 else avg_gain
            avg_loss = (avg_loss * 13 + losses[i - 1]) / 14 if i > 14 else avg_loss
            rs = avg_gain / max(avg_loss, 1e-10)
            rsi_arr[i] = 100 - 100 / (1 + rs)

    # MACD
    ema12_macd = np.full(n, np.nan)
    ema26_macd = np.full(n, np.nan)
    if n > 0:
        ema12_macd[0] = close[0]
        ema26_macd[0] = close[0]
        for i in range(1, n):
            ema12_macd[i] = close[i] * (2 / 13) + ema12_macd[i - 1] * (1 - 2 / 13)
            ema26_macd[i] = close[i] * (2 / 27) + ema26_macd[i - 1] * (1 - 2 / 27)
    macd_line = ema12_macd - ema26_macd
    macd_signal = np.full(n, np.nan)
    if n > 0:
        macd_signal[0] = macd_line[0]
        for i in range(1, n):
            macd_signal[i] = macd_line[i] * (2 / 10) + macd_signal[i - 1] * (1 - 2 / 10)
    macd_hist = macd_line - macd_signal

    # Donchian
    dc_high = np.full(n, np.nan)
    dc_low = np.full(n, np.nan)
    dc_mid = np.full(n, np.nan)
    for i in range(20, n):
        dc_high[i] = np.max(high[i - 20 : i])
        dc_low[i] = np.min(low[i - 20 : i])
        dc_mid[i] = (dc_high[i] + dc_low[i]) / 2

    return {
        "atr": atr_arr,
        "adx": adx_arr,
        "pdi": pos_di,
        "ndi": neg_di,
        "ema12": ema12,
        "ema20": ema20,
        "ema26": ema26,
        "ema50": ema50,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "rsi": rsi_arr,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "dc_high": dc_high,
        "dc_low": dc_low,
        "dc_mid": dc_mid,
    }


# ═══════════════════════════════════════════════════════════════
#  STRATÉGIE 1 : MA CROSSOVER (EMA12 × EMA26 + ADX)
# ═══════════════════════════════════════════════════════════════


def strat_ma_crossover(close, high, low, times, ind, symbol):
    """EMA12/EMA26 crossover avec filtre ADX et EMA50 trend."""
    n = len(close)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue

        adx_v = ind["adx"][i]
        ema12_v = ind["ema12"][i]
        ema26_v = ind["ema26"][i]
        ema50_v = ind["ema50"][i]
        atr_v = ind["atr"][i]
        prev_ema12 = ind["ema12"][i - 1]
        prev_ema26 = ind["ema26"][i - 1]

        if np.isnan(adx_v) or np.isnan(ema12_v) or np.isnan(ema26_v) or np.isnan(atr_v) or atr_v <= 0:
            continue
        if np.isnan(ema50_v) or np.isnan(prev_ema12) or np.isnan(prev_ema26):
            continue

        # Crossover detection
        cross_up = prev_ema12 <= prev_ema26 and ema12_v > ema26_v
        cross_down = prev_ema12 >= prev_ema26 and ema12_v < ema26_v

        if not cross_up and not cross_down:
            continue

        # ADX filter: need trend (> 20)
        if adx_v < 20:
            continue

        # EMA50 trend filter: trade with trend
        action = None
        regime = "RANGING"
        sl = 1.5
        tp = 3.0

        if cross_up and close[i] > ema50_v:  # BUY only above EMA50
            action = "BUY"
            regime = "TREND_UP"
            sl = 1.5
            tp = 3.5
        elif cross_down and close[i] < ema50_v:  # SELL only below EMA50
            action = "SELL"
            regime = "TREND_DOWN"
            sl = 1.5
            tp = 3.5

        if action is None:
            continue

        score = min(0.85, 0.50 + adx_v / 100)
        signals[i] = {
            "action": action,
            "score": score,
            "atr": atr_v,
            "adx": adx_v,
            "regime": regime,
            "sl_atr": sl,
            "tp_atr": tp,
        }
    return signals


# ═══════════════════════════════════════════════════════════════
#  STRATÉGIE 2 : BOLLINGER MEAN REVERSION (+ RSI)
# ═══════════════════════════════════════════════════════════════


def strat_bollinger_mean_rev(close, high, low, times, ind, symbol):
    """Mean reversion: price touches BB lower→BUY, BB upper→SELL.
    RSI < 30 ou > 70 pour confirmation. Stop à l'opposé de la bande."""
    n = len(close)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue

        price = close[i]
        bb_u = ind["bb_upper"][i]
        bb_l = ind["bb_lower"][i]
        bb_m = ind["bb_mid"][i]
        rsi_v = ind["rsi"][i]
        atr_v = ind["atr"][i]

        if np.isnan(bb_u) or np.isnan(bb_l) or np.isnan(bb_m) or np.isnan(rsi_v) or np.isnan(atr_v) or atr_v <= 0:
            continue

        action = None
        regime = "RANGING"
        sl = 1.5
        tp = 3.0
        score = 0.0

        # BUY: price touches lower BB + RSI < 35 (oversold)
        if price <= bb_l and rsi_v < 35:
            action = "BUY"
            score = 0.50 + max(0, (35 - rsi_v) / 70)
            sl = 1.5  # SL below the band
            tp = 2.0  # TP at middle band

        # SELL: price touches upper BB + RSI > 65 (overbought)
        elif price >= bb_u and rsi_v > 65:
            action = "SELL"
            score = 0.50 + max(0, (rsi_v - 65) / 70)
            sl = 1.5
            tp = 2.0

        if action is None or score < 0.50:
            continue

        signals[i] = {
            "action": action,
            "score": min(0.95, score),
            "atr": atr_v,
            "adx": ind["adx"][i] if not np.isnan(ind["adx"][i]) else 0,
            "regime": regime,
            "sl_atr": sl,
            "tp_atr": tp,
        }
    return signals


# ═══════════════════════════════════════════════════════════════
#  STRATÉGIE 3 : RSI EXTREME + EMA50 TREND
# ═══════════════════════════════════════════════════════════════


def strat_rsi_extreme(close, high, low, times, ind, symbol):
    """RSI extrême avec filtre de tendance EMA50.
    En tendance haussière, BUY quand RSI < 30 (dip buying).
    En tendance baissière, SELL quand RSI > 70 (rally selling)."""
    n = len(close)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue

        price = close[i]
        ema50_v = ind["ema50"][i]
        rsi_v = ind["rsi"][i]
        atr_v = ind["atr"][i]

        if np.isnan(ema50_v) or np.isnan(rsi_v) or np.isnan(atr_v) or atr_v <= 0:
            continue

        # Trend direction from EMA50 slope
        ema50_prev = ind["ema50"][i - 5] if i >= 5 else ema50_v
        trend_up = price > ema50_v and ema50_v > ema50_prev
        trend_down = price < ema50_v and ema50_v < ema50_prev

        action = None
        score = 0.0
        sl = 1.5
        tp = 3.0

        if trend_up and rsi_v < 30:  # Dip buying in uptrend
            action = "BUY"
            score = 0.50 + (30 - rsi_v) / 60
            sl = 1.5
            tp = 3.5
        elif trend_down and rsi_v > 70:  # Rally selling in downtrend
            action = "SELL"
            score = 0.50 + (rsi_v - 70) / 60
            sl = 1.5
            tp = 3.5
        elif rsi_v < 25:  # Extreme oversold (any trend)
            action = "BUY"
            score = 0.60 + (25 - rsi_v) / 50
            sl = 1.5
            tp = 2.5
        elif rsi_v > 75:  # Extreme overbought (any trend)
            action = "SELL"
            score = 0.60 + (rsi_v - 75) / 50
            sl = 1.5
            tp = 2.5

        if action is None or score < 0.55:
            continue

        regime = "TREND_UP" if action == "BUY" else "TREND_DOWN"
        signals[i] = {
            "action": action,
            "score": min(0.95, score),
            "atr": atr_v,
            "adx": ind["adx"][i] if not np.isnan(ind["adx"][i]) else 0,
            "regime": regime,
            "sl_atr": sl,
            "tp_atr": tp,
        }
    return signals


# ═══════════════════════════════════════════════════════════════
#  STRATÉGIE 4 : DONCHIAN BREAKOUT (20 périodes)
# ═══════════════════════════════════════════════════════════════


def strat_donchian(close, high, low, times, ind, symbol):
    """Donchian Channel breakout. Buy when price > DC high, Sell when price < DC low.
    ADX > 20 filter. SL at opposite DC band."""
    n = len(close)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue

        price = close[i]
        dc_h = ind["dc_high"][i]
        dc_l = ind["dc_low"][i]
        dc_m = ind["dc_mid"][i]
        adx_v = ind["adx"][i]
        atr_v = ind["atr"][i]
        ema50_v = ind["ema50"][i]

        if np.isnan(dc_h) or np.isnan(dc_l) or np.isnan(atr_v) or atr_v <= 0:
            continue
        if np.isnan(adx_v) or np.isnan(ema50_v):
            continue

        action = None
        score = 0.0
        sl = 2.0
        tp = 4.0

        # Breakout BUY: price > DC high + ADX > 20 + above EMA50
        if price > dc_h and adx_v > 20 and price > ema50_v:
            action = "BUY"
            strength = (price - dc_h) / atr_v
            score = 0.50 + min(0.45, strength * 0.15)
            sl = 2.0  # SL below DC mid
            tp = 4.0

        # Breakout SELL: price < DC low + ADX > 20 + below EMA50
        elif price < dc_l and adx_v > 20 and price < ema50_v:
            action = "SELL"
            strength = (dc_l - price) / atr_v
            score = 0.50 + min(0.45, strength * 0.15)
            sl = 2.0
            tp = 4.0

        if action is None or score < 0.60:
            continue

        regime = "TREND_UP" if action == "BUY" else "TREND_DOWN"
        signals[i] = {
            "action": action,
            "score": min(0.95, score),
            "atr": atr_v,
            "adx": adx_v,
            "regime": regime,
            "sl_atr": sl,
            "tp_atr": tp,
        }
    return signals


# ═══════════════════════════════════════════════════════════════
#  STRATÉGIE 5 : MACD CROSSOVER
# ═══════════════════════════════════════════════════════════════


def strat_macd(close, high, low, times, ind, symbol):
    """MACD line crosses signal line. ADX > 20 filter. EMA50 trend."""
    n = len(close)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue

        macd_v = ind["macd"][i]
        macd_s = ind["macd_signal"][i]
        macd_h = ind["macd_hist"][i]
        prev_macd = ind["macd"][i - 1] if i > 0 else macd_v
        prev_signal = ind["macd_signal"][i - 1] if i > 0 else macd_s
        adx_v = ind["adx"][i]
        atr_v = ind["atr"][i]
        ema50_v = ind["ema50"][i]

        if np.isnan(macd_v) or np.isnan(macd_s) or np.isnan(adx_v) or np.isnan(atr_v) or atr_v <= 0:
            continue
        if np.isnan(ema50_v):
            continue

        # MACD crossover
        cross_up = prev_macd <= prev_signal and macd_v > macd_s
        cross_down = prev_macd >= prev_signal and macd_v < macd_s

        if not cross_up and not cross_down:
            continue

        if adx_v < 20:
            continue

        action = None
        score = 0.0
        sl = 1.5
        tp = 3.0

        if cross_up and close[i] > ema50_v:  # BUY in uptrend
            action = "BUY"
            score = 0.50 + min(0.45, abs(macd_h) * 100)
            sl = 1.5
            tp = 3.5
        elif cross_down and close[i] < ema50_v:  # SELL in downtrend
            action = "SELL"
            score = 0.50 + min(0.45, abs(macd_h) * 100)
            sl = 1.5
            tp = 3.5

        if action is None or score < 0.55:
            continue

        regime = "TREND_UP" if action == "BUY" else "TREND_DOWN"
        signals[i] = {
            "action": action,
            "score": min(0.95, score),
            "atr": atr_v,
            "adx": adx_v,
            "regime": regime,
            "sl_atr": sl,
            "tp_atr": tp,
        }
    return signals


# ═══════════════════════════════════════════════════════════════
#  SimTrade (identique aux autres backtests)
# ═══════════════════════════════════════════════════════════════


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
        "strategy",
    )

    def __init__(self, symbol, action, entry, sl, tp, atr_val, regime, bar_idx, bar_time, balance, strategy=""):
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
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.commission_usd = 0.0
        self.cost_pips = 0.0
        self.strategy = strategy
        sp, ps, pv, cs = get_specs(symbol)
        self._spread_pips = sp
        self._pip_size = ps
        self._pip_value = pv
        self._contract_size = cs
        self._calc_lot(entry, sl, balance)

    def _pip_value_corrected(self, price=None):
        pv = self._pip_value
        if self.symbol in ("USDJPY",):
            pip_per_lot_quote = self._contract_size * self._pip_size
            rate = abs(price if price is not None else self.entry)
            pv = pip_per_lot_quote / max(rate, 1e-10)
        return pv

    def _notional_usd(self):
        raw = self.lot * self._contract_size * abs(self.entry)
        if self.symbol == "USDJPY":
            return self.lot * self._contract_size
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
        pv = self._pip_value_corrected()
        usdpp = self.lot * pv
        if self.direction == 0:
            pips = (self.close_price - self.entry) / self._pip_size
        else:
            pips = (self.entry - self.close_price) / self._pip_size
        self.profit_usd = pips * usdpp
        self.cost_pips = self._spread_pips + SLIPPAGE_PIPS
        notional = self._notional_usd()
        self.commission_usd = (notional / 100_000) * COMMISSION_PER_100K * 2
        pips_cost = pips - self.cost_pips
        self.profit_usd_cost = pips_cost * usdpp - self.commission_usd

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
        if profit_atr <= 1.0:
            return
        lvls = TRAILING_LEVELS.get(self.regime, TRAILING_LEVELS["RANGING"])
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

    def check_partial(self, atr_v):
        if self.closed or self.partial_closed or atr_v <= 0:
            return
        if self.direction == 0:
            prog = (self.peak_price - self.entry) / max(self.tp - self.entry, 1e-10)
        else:
            prog = (self.entry - self.peak_price) / max(self.entry - self.tp, 1e-10)
        if prog < 0.60:
            return
        self.partial_closed = True
        be = 0.80 * atr_v
        if self.direction == 0:
            ns = self.entry + be
            if ns > self.trailing_sl:
                self.trailing_sl = ns
        else:
            ns = self.entry - be
            if ns < self.trailing_sl:
                self.trailing_sl = ns


# ═══════════════════════════════════════════════════════════════
#  BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════

STRATEGIES = {
    "MA_CROSS": strat_ma_crossover,
    "BB_MEAN_REV": strat_bollinger_mean_rev,
    "RSI_EXTREME": strat_rsi_extreme,
    "DONCHIAN": strat_donchian,
    "MACD": strat_macd,
}


def backtest_symbol_strategy(symbol, timeframe, df, strat_name, strat_func):
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    ind = precalc_indicators(high, low, close)
    times_dt = pd.to_datetime(times)
    sigs = strat_func(close, high, low, times_dt, ind, symbol)

    trades = []
    open_t = []
    bars_since = 999

    for i in range(MIN_BARS, n):
        atr_v = ind["atr"][i] if not np.isnan(ind["atr"][i]) else 0

        still = []
        for t in open_t:
            t.update_peak(high[i], low[i])
            tatr = atr_v if atr_v > 0 else t.atr_val
            t.check_partial(tatr)
            t.update_trailing(tatr)
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            if not t.closed and i - t.open_bar > TIMEOUT_BARS.get(timeframe, 80):
                t.closed = True
                t.close_price = close[i]
                t.close_time = times[i]
                t.result = "TIMEOUT"
                t.bars_held = i - t.open_bar
                t._calc_pnl()
            if not t.closed:
                still.append(t)
        open_t = still

        bars_since += 1
        if atr_v <= 0:
            continue

        sig = sigs[i]
        if sig is not None and bars_since >= 2:
            sd = sig["sl_atr"] * atr_v
            td = sig["tp_atr"] * atr_v
            if sig["action"] == "BUY":
                sp = close[i] - sd
                tp = close[i] + td
            else:
                sp = close[i] + sd
                tp = close[i] - td
            if sd > 0 and td / sd >= 1.5:
                if not any(t.action == sig["action"] for t in open_t):
                    t = SimTrade(
                        symbol,
                        sig["action"],
                        close[i],
                        sp,
                        tp,
                        atr_v,
                        sig["regime"],
                        i,
                        times[i],
                        INITIAL_BALANCE,
                        strat_name,
                    )
                    trades.append(t)
                    open_t.append(t)
                    bars_since = 0

    return trades


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
    peak = INITIAL_BALANCE
    dd_max = 0.0
    bal = INITIAL_BALANCE
    for t in sorted(closed, key=lambda x: x.close_time or x.open_time):
        bal += t.profit_usd_cost
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)
    p = 1.0
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    avg_win = gp / nw if nw else 0
    avg_loss = -gl / (n - nw) if n > nw else 0
    avg_cost = sum(abs(t.profit_usd - t.profit_usd_cost) for t in closed) / n if n else 0
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
        "avg_pnl": round(tp / n, 2) if n else 0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_cost": round(avg_cost, 2),
    }


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest 5 stratégies alternatives Forex")
    parser.add_argument("--tf", choices=["H1", "H4", "D1", "ALL"], default="H1")
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()

    data_dir = Path("data/historical")
    if not data_dir.exists():
        print("❌ data/historical/ introuvable")
        sys.exit(1)

    print("=" * 120)
    print("  BACKTEST 5 STRATÉGIES ALTERNATIVES — 7 Paires Forex Primaires")
    print(f"  TF: {args.tf}  |  Min trades: {args.min_trades}")
    print(f"  Coûts: spread réel + slippage {SLIPPAGE_PIPS} pip + commission ${COMMISSION_PER_100K}/100K")
    print(f"  Symboles: {', '.join(FOREX_MAJORS)}")
    print(f"  Stratégies: {', '.join(STRATEGIES.keys())}")
    print("=" * 120)

    results = {}
    start_all = datetime.utcnow()

    for sym in FOREX_MAJORS:
        print(f"\n  ─── {sym} ───")
        sym_results = {}
        for sname, sfunc in STRATEGIES.items():
            t0 = datetime.utcnow()
            if args.tf == "ALL":
                all_trades = []
                for tf in ("H1", "H4", "D1"):
                    fp = data_dir / f"{sym}_{tf}.parquet"
                    if not fp.exists():
                        continue
                    df = pd.read_parquet(fp)
                    if len(df) < MIN_BARS:
                        continue
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
                    if len(df) < MIN_BARS:
                        continue
                    trades = backtest_symbol_strategy(sym, tf, df, sname, sfunc)
                    all_trades.extend(trades)
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
                all_trades = backtest_symbol_strategy(sym, args.tf, df, sname, sfunc)

            closed = [t for t in all_trades if t.closed]
            elapsed = (datetime.utcnow() - t0).total_seconds()
            m = compute_metrics(closed)
            m["elapsed"] = round(elapsed, 1)
            sym_results[sname] = m

            if m["n"] >= args.min_trades:
                emoji = "✅" if m["significant"] and m["total_pnl"] > 0 else "⚠️" if m["win_rate"] > 50 else "❌"
                print(
                    f"    {emoji} {sname:12s}: {m['n']:>5d} trades  WR={m['win_rate']:>5.1f}%  "
                    f"PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>4.2f}  "
                    f"DD={m['max_drawdown_pct']:>5.1f}%  {elapsed:.1f}s"
                )
            else:
                print(f"    ⏭️ {sname:12s}: {m['n']:>5d} trades (< {args.min_trades})")

        results[sym] = sym_results

    total_elapsed = (datetime.utcnow() - start_all).total_seconds()

    # ─── TABLEAU RÉCAPITULATIF PAR STRATÉGIE ───
    print(f"\n{'=' * 120}")
    print(f"  RÉCAPITULATIF — PnL Net par Stratégie × Symbole ({args.tf})")
    print(f"{'=' * 120}")
    header = f"  {'Stratégie':<14s}"
    for sym in FOREX_MAJORS:
        header += f" {sym:>8s}"
    header += f" {'TOTAL':>10s}  {'MOY WR':>7s}  {'MOY PF':>7s}"
    print(header)
    print(f"  {'-' * 110}")

    for sname in STRATEGIES:
        line = f"  {sname:<14s}"
        total_pnl = 0
        total_n = 0
        total_w = 0
        total_gp = 0
        total_gl = 0
        for sym in FOREX_MAJORS:
            m = results.get(sym, {}).get(sname, {})
            pnl = m.get("total_pnl", 0)
            total_pnl += pnl
            total_n += m.get("n", 0)
            total_w += m.get("wins", 0)
            total_gp += m.get("gross_profit", 0)
            total_gl += m.get("gross_loss", 0)
            if m.get("n", 0) >= args.min_trades:
                sig = "✅" if m.get("significant") and pnl > 0 else "⚠️" if m.get("win_rate", 0) > 50 else "❌"
                line += f" ${pnl:>+7.0f}{sig}"
            else:
                line += f"  {'N/A':>8s}"
        avg_wr = total_w / total_n * 100 if total_n else 0
        avg_pf = total_gp / total_gl if total_gl > 0 else 0
        line += f" ${total_pnl:>+9.0f}  {avg_wr:>5.1f}%   {avg_pf:>5.2f}"
        print(line)

    print(f"  {'-' * 110}")

    # Ligne MOM20x3 pour référence (données du backtest standard)
    print(f"\n  {'═' * 60}")
    print(f"  RÉFÉRENCE MOM20x3 (backtest standard, H1, après coûts)")
    print(f"  {'═' * 60}")
    print(f"  EURUSD: 50.1% WR, -$228K, PF=0.54")
    print(f"  GBPUSD: 54.0% WR, -$243K, PF=0.61")
    print(f"  USDCHF: 48.7% WR, -$148K, PF=0.55")
    print(f"  USDCAD: 48.2% WR, -$285K, PF=0.49")
    print(f"  AUDUSD: 49.3% WR, -$305K, PF=0.43")
    print(f"  NZDUSD: 49.0% WR, -$266K, PF=0.45")
    print(f"  USDJPY: 53.9% WR, -$229K, PF=0.57")
    print()

    # Analyse du meilleur candidat
    print(f"  {'═' * 60}")
    print(f"  MEILLEUR CANDIDAT PAR STRATÉGIE")
    print(f"  {'═' * 60}")
    best_overall = None
    best_overall_pnl = -1e9
    for sname in STRATEGIES:
        best_sym = None
        best_pnl = -1e9
        best_m = None
        for sym in FOREX_MAJORS:
            m = results.get(sym, {}).get(sname, {})
            if m.get("n", 0) >= args.min_trades and m.get("total_pnl", 0) > best_pnl:
                best_pnl = m["total_pnl"]
                best_sym = sym
                best_m = m
        if best_sym and best_m:
            print(
                f"  {sname:12s}: {best_sym:8s}  {best_m['n']:>5d}t  WR={best_m['win_rate']:>5.1f}%  "
                f"PnL=${best_m['total_pnl']:>+9.2f}  PF={best_m['profit_factor']:>4.2f}  "
                f"DD={best_m['max_drawdown_pct']:>4.1f}%  Signif={'✅' if best_m.get('significant') else '❌'}"
            )
            if best_pnl > best_overall_pnl:
                best_overall_pnl = best_pnl
                best_overall = (sname, best_sym, best_m)

    if best_overall:
        sn, sy, m = best_overall
        print(f"\n  🏆 MEILLEUR RÉSULTAT GLOBAL: {sn} sur {sy}")
        print(f"     {m['n']} trades, WR={m['win_rate']}%, PnL=${m['total_pnl']:+.2f}, PF={m['profit_factor']}")
        print(f"     Survit aux coûts: {'✅ OUI' if m.get('significant') and m['total_pnl'] > 0 else '❌ NON'}")
    else:
        print(f"\n  ❌ Aucune stratégie ne produit un résultat positif significatif après coûts.")

    # Sauvegarde
    out = Path("runtime")
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "timeframe": args.tf,
            "description": "5 stratégies alternatives sur 7 paires Forex primaires",
            "slippage_pips": SLIPPAGE_PIPS,
            "commission_per_100k": COMMISSION_PER_100K,
        },
        "strategies_tested": list(STRATEGIES.keys()),
        "symbols": FOREX_MAJORS,
        "results": results,
        "best_overall": {
            "strategy": best_overall[0] if best_overall else None,
            "symbol": best_overall[1] if best_overall else None,
            "metrics": best_overall[2] if best_overall else None,
        }
        if best_overall
        else None,
    }

    def cj(o):
        if isinstance(o, dict):
            return {k: cj(v) for k, v in o.items()}
        if isinstance(o, list):
            return [cj(v) for v in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.bool_):
            return bool(o)
        return o

    with open(out / "backtest_forex_alt_strategies.json", "w") as f:
        json.dump(cj(report), f, indent=2)
    print(f"\n  Rapport: runtime/backtest_forex_alt_strategies.json")
    print(f"  Terminé en {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
