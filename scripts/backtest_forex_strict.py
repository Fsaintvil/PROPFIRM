"""
Backtest MOM20x3 — FOREX STRICT : paramètres plus restrictifs pour vérifier
si le Forex peut devenir rentable après coûts avec une sélection plus sévère.

Paramètres "stricts" :
  - Threshold trending: 3.0×ATR (vs 2.5×)
  - Threshold ranging:  2.5×ATR (vs 2.0×)
  - ADX trending ≥ 28 (vs 25)
  - Min score ≥ 0.70 (filtre les signaux faibles)
  - Slippage: 2.0 pips (vs 1.0)
  - Pullback band resserré

Usage:
    python scripts/backtest_forex_strict.py
    python scripts/backtest_forex_strict.py --tf H1
    python scripts/backtest_forex_strict.py --tf ALL
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
# PARAMÈTRES STRICTS (plus restrictifs que la version standard)
# ═══════════════════════════════════════════════════════════════

INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044
MIN_BARS = 80
TIMEOUT_BARS = {"H1": 120, "H4": 60, "D1": 30}
MAX_LOT = 1.0
MIN_TRADES = 50  # plus bas car moins de trades avec filtres stricts

# Danger hours (inchangé)
DANGER_HOURS = [0, 1, 6, 7, 9, 12, 15, 18]

# ═══ SEUILS PLUS STRICTS ═══
THRESHOLD_TRENDING = 3.0  # était 2.5 — signaux plus forts seulement
THRESHOLD_RANGING = 2.5  # était 2.0
THRESHOLD_MAX = 3.5
THRESHOLD_MIN = 2.0
ADX_TRENDING_THRESHOLD = 28  # était 25 — que des trends fortes

# SL/TP (inchangés)
SL_ATR_TRENDING = 2.0
TP_ATR_TRENDING = 5.0
SL_ATR_RANGING = 1.5
TP_ATR_RANGING = 4.0

# ═══ SCORE MIN ═══
MIN_SCORE = 0.70  # filtre les signaux faibles

# ═══ COÛTS PLUS RÉALISTES ═══
DEFAULT_SPREAD = 2.0
DEFAULT_PIP = 0.0001
DEFAULT_PIP_VALUE = 10.0
DEFAULT_CONTRACT = 100_000
SLIPPAGE_PIPS = 2.0  # était 1.0 — plus réaliste
COMMISSION_PER_100K = 7.0  # inchangé

TRAILING_LEVELS = {
    "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
    "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
    "LOW_VOL": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
}

# ═══ SYMBOLES FOREX SEULEMENT ═══
FOREX_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "USDJPY",
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "AUDJPY",
]

# Périodes momentum par symbole
SYMBOL_MOMENTUM_PERIODS = {
    "USDCAD": 24,
    "USDCHF": 14,
    "EURUSD": 18,
    "GBPUSD": 20,
    "AUDUSD": 24,
    "NZDUSD": 22,
    "EURJPY": 20,
    "GBPJPY": 20,
    "USDJPY": 20,
    "EURGBP": 20,
    "AUDJPY": 20,
}

# Coûts par symbole (inchangés, réalistes)
SYMBOL_COSTS = {
    "EURUSD": (1.5, 0.0001, 10.0, 100_000),
    "GBPUSD": (1.5, 0.0001, 10.0, 100_000),
    "USDJPY": (1.5, 0.01, 1.0, 100_000),
    "USDCAD": (1.5, 0.0001, 10.0, 100_000),
    "USDCHF": (1.5, 0.0001, 10.0, 100_000),
    "AUDUSD": (1.5, 0.0001, 10.0, 100_000),
    "NZDUSD": (1.5, 0.0001, 10.0, 100_000),
    "EURJPY": (2.0, 0.01, 1.0, 100_000),
    "GBPJPY": (3.0, 0.01, 1.0, 100_000),
    "EURGBP": (1.5, 0.0001, 10.0, 100_000),
    "AUDJPY": (2.0, 0.01, 1.0, 100_000),
}


def get_specs(symbol):
    if symbol in SYMBOL_COSTS:
        return SYMBOL_COSTS[symbol]
    return (DEFAULT_SPREAD, DEFAULT_PIP, DEFAULT_PIP_VALUE, DEFAULT_CONTRACT)


# ═══════════════════════════════════════════════════════════════
#  INDICATEURS VECTORISÉS
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
    ema20 = np.full(n, np.nan)
    alpha = 2 / 21
    if n > 0:
        ema20[0] = close[0]
        for i in range(1, n):
            ema20[i] = close[i] * alpha + ema20[i - 1] * (1 - alpha)
    return atr_arr, adx_arr, pos_di, neg_di, ema20


# ═══════════════════════════════════════════════════════════════
#  SIGNALS — VERSION STRICTE
# ═══════════════════════════════════════════════════════════════


def batch_strict_signals(close, high, low, times, atr_arr, adx_arr, pos_di, neg_di, ema20, symbol):
    """Signaux MOM20x3 avec paramètres STRICTS pour le Forex."""
    n = len(close)
    mp = SYMBOL_MOMENTUM_PERIODS.get(symbol, 20)
    half = max(14, mp // 2)
    adx_slope_arr = np.zeros(n)
    for i in range(half + 28, n):
        if not np.isnan(adx_arr[i]) and not np.isnan(adx_arr[i - half]):
            adx_slope_arr[i] = adx_arr[i] - adx_arr[i - half]
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i]
        adx_v = adx_arr[i]
        if np.isnan(atr_v) or atr_v <= 0 or np.isnan(adx_v) or adx_v <= 0:
            continue
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue
        if i < mp + 1:
            continue
        mom = float(close[i] - close[i - mp])
        mom_abs = abs(mom)

        # ═══ ADX plus strict pour trending ═══
        is_trending = adx_v >= ADX_TRENDING_THRESHOLD  # 28 au lieu de 25

        # ═══ Thresholds plus stricts ═══
        thresh = THRESHOLD_TRENDING if is_trending else THRESHOLD_RANGING
        thresh = max(THRESHOLD_MIN, min(THRESHOLD_MAX, thresh))
        tv = thresh * atr_v
        if mom_abs < tv:
            continue

        raw_score = min(1.0, mom_abs / (tv * 2)) if mom_abs > 0 else 0.0

        # ADX slope (plus strict)
        st = -3.0 if raw_score > 0.75 else -1.5  # plus strict que -3.5/-2.0
        if adx_slope_arr[i] < st:
            continue

        # Direction filter
        pdi = pos_di[i]
        ndi = neg_di[i]
        if np.isnan(pdi) or np.isnan(ndi):
            continue
        dir_ok = True
        di_sugg = None
        action = None
        score = 0.0

        if mom > 0:
            if pdi <= ndi * 0.7:  # plus strict que 0.8
                dir_ok = False
                di_sugg = "SELL"
        else:
            if ndi <= pdi * 0.7:  # plus strict que 0.8
                dir_ok = False
                di_sugg = "BUY"

        if mom > 0 and mom_abs >= tv:
            action = "BUY"
            score = 0.50 + raw_score * 0.45
        elif mom < 0 and mom_abs >= tv:
            action = "SELL"
            score = 0.50 + raw_score * 0.45

        if action is None:
            continue

        # DI Override (plus strict)
        if not dir_ok and di_sugg is not None and i >= 7:
            sm = float(close[i] - close[i - 5])
            sma = abs(sm)
            ot = tv * 2.5 if adx_v < ADX_TRENDING_THRESHOLD else tv * 0.8  # plus strict
            if di_sugg == "SELL" and sm < -ot:
                action = "SELL"
                score = 0.50 + min(1.0, sma / (tv * 2)) * 0.45
                dir_ok = True
            elif di_sugg == "BUY" and sm > ot:
                action = "BUY"
                score = 0.50 + min(1.0, sma / (tv * 2)) * 0.45
                dir_ok = True

        if not dir_ok:
            continue

        # ═══ Score minimum ═══
        if score < MIN_SCORE:
            continue

        # Pullback (plus serré)
        ev = ema20[i]
        if np.isnan(ev) or ev <= 0:
            continue
        pb_dist = (close[i] - ev) / ev * 100
        pb_mult = 0.4 if is_trending else 0.2  # plus serré que 0.5/0.3
        pb_band = max(0.03, min(0.8, (pb_mult * atr_v) / ev * 100))
        pb_active = abs(pb_dist) < pb_band

        regime = ("TREND_UP" if action == "BUY" else "TREND_DOWN") if is_trending else "RANGING"
        signals[i] = {
            "action": action,
            "score": min(0.99, score),
            "atr": atr_v,
            "adx": adx_v,
            "regime": regime,
            "sl_atr": SL_ATR_TRENDING if is_trending else SL_ATR_RANGING,
            "tp_atr": TP_ATR_TRENDING if is_trending else TP_ATR_RANGING,
            "threshold_value": tv,
        }
    return signals


# ═══════════════════════════════════════════════════════════════
#  SimTrade (identique au backtest standard)
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
            if self.symbol == "USDJPY":
                rate = abs(price if price is not None else self.entry)
                pv = pip_per_lot_quote / max(rate, 1e-10)
            else:
                pv = pip_per_lot_quote / 150.0
        return pv

    def _notional_usd(self):
        raw = self.lot * self._contract_size * abs(self.entry)
        if self.symbol in ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"):
            if self.symbol == "USDJPY":
                return self.lot * self._contract_size
            else:
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
            bars_held=self.bars_held,
            open_time=str(self.open_time)[:19],
            close_time=str(self.close_time)[:19] if self.close_time else "",
        )


# ═══════════════════════════════════════════════════════════════
#  BACKTEST PAR SYMBOLE
# ═══════════════════════════════════════════════════════════════


def backtest_symbol(symbol, timeframe, df):
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    atr_arr, adx_arr, pdi, ndi, ema20 = precalc_indicators(high, low, close)
    times_dt = pd.to_datetime(times)
    sigs = batch_strict_signals(close, high, low, times_dt, atr_arr, adx_arr, pdi, ndi, ema20, symbol)

    trades, trades_cost = [], []
    open_t, open_tc = [], []
    bars_since = 999

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0

        for lst in (open_t, open_tc):
            still = []
            for t in lst:
                t.update_peak(high[i], low[i])
                tatr = atr_v if atr_v > 0 else t.atr_val
                t.check_partial(tatr)
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
                    still.append(t)
            lst[:] = still

        bars_since += 1
        if atr_v <= 0:
            continue

        sig = sigs[i]
        if sig is not None and bars_since >= 3:
            sd = sig["sl_atr"] * atr_v
            td = sig["tp_atr"] * atr_v
            if sig["action"] == "BUY":
                sp = close[i] - sd
                tp = close[i] + td
            else:
                sp = close[i] + sd
                tp = close[i] - td
            if sd > 0 and td / sd >= 2.0:
                if not any(t.action == sig["action"] for t in open_t):
                    t = SimTrade(
                        symbol, sig["action"], close[i], sp, tp, atr_v, sig["regime"], i, times[i], INITIAL_BALANCE
                    )
                    trades.append(t)
                    open_t.append(t)
                    tc = SimTrade(
                        symbol, sig["action"], close[i], sp, tp, atr_v, sig["regime"], i, times[i], INITIAL_BALANCE
                    )
                    trades_cost.append(tc)
                    open_tc.append(tc)
                    bars_since = 0

    return trades, trades_cost


# ═══════════════════════════════════════════════════════════════
#  MÉTRIQUES
# ═══════════════════════════════════════════════════════════════


def compute_metrics(closed, use_cost=False):
    if not closed:
        return {"n": 0}
    key = "profit_usd_cost" if use_cost else "profit_usd"
    wins = [t for t in closed if getattr(t, key) > 0]
    losses = [t for t in closed if getattr(t, key) <= 0]
    n = len(closed)
    nw = len(wins)
    wr = nw / n * 100 if n > 0 else 0
    tp = sum(getattr(t, key) for t in closed)
    gp = sum(max(0, getattr(t, key)) for t in closed)
    gl = abs(sum(min(0, getattr(t, key)) for t in closed))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
    peak = INITIAL_BALANCE
    dd_max = 0.0
    bal = INITIAL_BALANCE
    for t in sorted(closed, key=lambda x: x.close_time or x.open_time):
        bal += getattr(t, key)
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)
    p = 1.0
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
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


# ═══════════════════════════════════════════════════════════════
#  MULTI-TF
# ═══════════════════════════════════════════════════════════════


def backtest_multi_tf(symbol):
    all_t, all_tc = [], []
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
        t, tc = backtest_symbol(symbol, tf, df)
        all_t.extend(t)
        all_tc.extend(tc)
    return all_t, all_tc


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest FOREX STRICT MOM20x3")
    parser.add_argument("--tf", choices=["H1", "H4", "D1", "ALL"], default="ALL")
    parser.add_argument("--min-trades", type=int, default=MIN_TRADES)
    args = parser.parse_args()

    data_dir = Path("data/historical")
    if not data_dir.exists():
        print("❌ data/historical/ introuvable")
        sys.exit(1)

    print("=" * 120)
    print("  BACKTEST MOM20x3 — FOREX STRICT (paramètres resserrés)")
    print(f"  TF: {args.tf}  |  Min trades: {args.min_trades}")
    print(f"  ═══ Paramètres stricts ═══")
    print(f"  Threshold trending: {THRESHOLD_TRENDING}×ATR  |  ranging: {THRESHOLD_RANGING}×ATR")
    print(f"  ADX trending ≥ {ADX_TRENDING_THRESHOLD}  |  Min score: {MIN_SCORE}")
    print(f"  Slippage: {SLIPPAGE_PIPS} pips  |  Commission: ${COMMISSION_PER_100K}/100K")
    print(f"  Spreads: réels (1.5-3.0 pips selon paire)")
    print(f"  Filtres: ADX slope strict + DI + Override + Pullback serré + Danger hours")
    print(f"  Symboles: {', '.join(FOREX_SYMBOLS)}")
    print("=" * 120)

    start_all = datetime.utcnow()
    results = {}

    for sym in FOREX_SYMBOLS:
        t0 = datetime.utcnow()
        if args.tf == "ALL":
            cls, clc = backtest_multi_tf(sym)
        else:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                print(f"  ⚠️ {sym:12s}  Fichier introuvable: {fp.name}")
                continue
            df = pd.read_parquet(fp)
            if len(df) < MIN_BARS:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
            if len(df) < MIN_BARS:
                continue
            cls, clc = backtest_symbol(sym, args.tf, df)

        closed_p = [t for t in cls if t.closed]
        closed_pc = [t for t in clc if t.closed]
        elapsed = (datetime.utcnow() - t0).total_seconds()

        if len(closed_pc) < args.min_trades:
            print(f"  ⏭️ {sym:12s}  {len(closed_pc)} trades (< {args.min_trades})  {elapsed:.1f}s")
            continue

        m_p = compute_metrics(closed_p, use_cost=False)
        m_pc = compute_metrics(closed_pc, use_cost=True)
        costs = avg_costs(closed_pc)

        results[sym] = {
            "trades": m_p,
            "trades_cost": m_pc,
            "costs": costs,
            "survives": m_pc.get("significant", False) and m_pc["win_rate"] > 50 and m_pc["total_pnl"] > 0,
            "elapsed_s": round(elapsed, 1),
        }

        emoji = "✅" if results[sym]["survives"] else "⚠️" if m_pc["win_rate"] > 50 else "❌"
        print(
            f"  {emoji} {sym:12s}  Prod+Cost: {m_pc['n']:>5d} trades  "
            f"WR={m_pc['win_rate']:>5.1f}%  PnL=${m_pc['total_pnl']:>+9.2f}  "
            f"PF={m_pc['profit_factor']:>5.2f}  DD={m_pc['max_drawdown_pct']:>5.1f}%  "
            f"Cost=${costs.get('avg_total_cost_usd', 0):>+6.2f}/tr  "
            f"{elapsed:.1f}s"
        )

    total_elapsed = (datetime.utcnow() - start_all).total_seconds()

    if not results:
        print("\n❌ Aucun résultat. Vérifie les données dans data/historical/")
        return

    ranked = sorted(results.items(), key=lambda x: x[1]["trades_cost"]["total_pnl"], reverse=True)

    print(f"\n{'=' * 120}")
    print(f"  🏆 CLASSEMENT FOREX STRICT — MOM20x3 PROD+COÛTS ({args.tf}) — 2012-2026")
    print(f"  Trié par PnL net (après tous filtres + coûts + paramètres stricts)")
    print(f"{'=' * 120}")
    print(
        f"  {'#':>2s} {'Symbole':12s} {'Trades':>6s} {'WR':>5s}  {'PnL':>10s}  {'PF':>5s}  {'DD':>5s}  "
        f"{'AvgPnL':>7s}  {'Cost/tr':>7s}  {'Signif':>7s}"
    )
    print(f"  {'-' * 95}")

    survivors = []
    for rank, (sym, r) in enumerate(ranked, 1):
        pc = r["trades_cost"]
        c = r["costs"]
        sig = "✅" if pc.get("significant") else "❌"
        survive = "✅" if r["survives"] else ""
        avg_pnl = pc["total_pnl"] / pc["n"] if pc["n"] else 0
        print(
            f"  {rank:>2d} {sym:12s} {pc['n']:>6d} {pc['win_rate']:>4.1f}%{survive} "
            f"${pc['total_pnl']:>+9.2f} {pc['profit_factor']:>5.2f} {pc['max_drawdown_pct']:>5.1f}% "
            f"${avg_pnl:>+6.2f} ${c.get('avg_total_cost_usd', 0):>+6.2f} {sig}"
        )
        if r["survives"]:
            survivors.append(sym)

    total_n = sum(r["trades_cost"]["n"] for _, r in ranked)
    total_pnl = sum(r["trades_cost"]["total_pnl"] for _, r in ranked)
    total_wins = sum(r["trades_cost"]["wins"] for _, r in ranked)
    total_gp = sum(r["trades_cost"]["gross_profit"] for _, r in ranked)
    total_gl = sum(r["trades_cost"]["gross_loss"] for _, r in ranked)
    total_wr = total_wins / total_n * 100 if total_n else 0
    total_pf = total_gp / total_gl if total_gl > 0 else 0
    print(f"  {'-' * 95}")
    print(f"  {'TOTAL FOREX':>15s} {total_n:>6d} {total_wr:>4.1f}%   ${total_pnl:>+9.2f} {total_pf:>5.2f}")
    print()

    # Analyse comparative avec le backtest standard (anciens résultats)
    print(f"  {'═' * 60}")
    print(f"  ANALYSE : Stricter vs Standard")
    print(f"  {'═' * 60}")
    print(f"  Paramètres strict:  thresh_trend={THRESHOLD_TRENDING}, thresh_range={THRESHOLD_RANGING},")
    print(f"  ADX>={ADX_TRENDING_THRESHOLD}, score>={MIN_SCORE}, slippage={SLIPPAGE_PIPS}")
    print(f"  Paramètres standard: thresh_trend=2.5, thresh_range=2.0, ADX>=25, slippage=1.0")
    print()

    if survivors:
        print(f"  ✅ Symboles Forex survivants: {', '.join(survivors)}")
        pnl_surv = sum(results[s]["trades_cost"]["total_pnl"] for s in survivors)
        print(f"  PnL net cumulé: ${pnl_surv:+.2f}")
    else:
        print(f"  ❌ Aucun symbole Forex ne survit aux coûts avec paramètres stricts.")
        print()
        print(f"  → VERDICT : Le Forex n'a PAS d'edge net après coûts,")
        print(f"     même avec des paramètres d'entrée plus stricts.")
        print(f"     La réduction du nombre de trades ne compense pas")
        print(f"     le spread + slippage + commission.")

    # Sauvegarde
    out = Path("runtime")
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "timeframe": args.tf,
            "type": "FOREX_STRICT",
            "threshold_trending": THRESHOLD_TRENDING,
            "threshold_ranging": THRESHOLD_RANGING,
            "adx_trending_threshold": ADX_TRENDING_THRESHOLD,
            "min_score": MIN_SCORE,
            "slippage_pips": SLIPPAGE_PIPS,
            "commission_per_100k": COMMISSION_PER_100K,
            "initial_balance": INITIAL_BALANCE,
            "risk_per_trade": RISK_PER_TRADE,
        },
        "per_symbol": results,
        "ranking": [
            {
                "rank": i + 1,
                "symbol": s,
                "pnl": results[s]["trades_cost"]["total_pnl"],
                "wr": results[s]["trades_cost"]["win_rate"],
                "pf": results[s]["trades_cost"]["profit_factor"],
                "survives": results[s]["survives"],
            }
            for i, (s, _) in enumerate(ranked)
        ],
        "survivors": survivors,
        "totals": {"n": total_n, "wr": round(total_wr, 1), "pnl": round(total_pnl, 2), "pf": round(total_pf, 2)},
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

    with open(out / "backtest_forex_strict_report.json", "w") as f:
        json.dump(cj(report), f, indent=2)
    print(f"\n  Rapport: runtime/backtest_forex_strict_report.json")
    print(f"  Terminé en {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
