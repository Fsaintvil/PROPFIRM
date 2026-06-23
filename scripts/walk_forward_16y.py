"""
Walk-Forward Validation — 5 folds temporels sur 16 ans.
Valide la robustesse du MOM20x3 en testant sur des périodes jamais vues.

Usage:
    python scripts/walk_forward_16y.py
    python scripts/walk_forward_16y.py --folds 5
    python scripts/walk_forward_16y.py --export
"""

import os
import sys
import json
import pickle
from collections import defaultdict
from pathlib import Path
from math import sqrt, erf
from time import time as now

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx

# ============================================================================
# CONFIGURATION
# ============================================================================
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.004
MIN_BARS = 60
MAX_LOT = 1.0
TIMEOUT_BARS = {"M15": 200, "H1": 120, "H4": 60, "D1": 30}

# Symbol calibration (from backtest_16y_full.py)
SYMBOL_CONFIG = {
    "XAUUSD": {
        "momentum_period": 24,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0,
        "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -18.0,
        "adx_slope_threshold_strong": -24.0,
        "pullback_band_trending": 0.8,
        "pullback_band_ranging": 0.5,
        "max_lot": 0.10,
        "risk_mult": 1.0,
        "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(0.80, 0.60), (2.00, 0.40), (3.00, 0.25), (5.00, 0.10)],
            "TREND_DOWN": [(0.80, 0.60), (2.00, 0.40), (3.00, 0.25), (5.00, 0.10)],
            "RANGING": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
            "HIGH_VOL": [(0.80, 0.80), (2.00, 0.55), (3.00, 0.40), (5.00, 0.20)],
            "LOW_VOL": [(0.80, 0.35), (2.00, 0.22), (3.00, 0.15), (5.00, 0.08)],
        },
        "be_buffer": 0.55,
    },
    "BTCUSD": {
        "momentum_period": 24,
        "sl_atr_trending": 3.0,
        "tp_atr_trending": 8.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 5.0,
        "threshold_trending": 2.0,
        "threshold_ranging": 1.5,
        "adx_slope_threshold": -6.0,
        "adx_slope_threshold_strong": -10.0,
        "pullback_band_trending": 0.8,
        "pullback_band_ranging": 0.5,
        "max_lot": 0.05,
        "risk_mult": 0.65,
        "first_lock_atr": 1.5,
        "trailing": {
            "TREND_UP": [(1.50, 1.00), (3.00, 0.70), (4.00, 0.50), (6.00, 0.30)],
            "TREND_DOWN": [(1.50, 1.00), (3.00, 0.70), (4.00, 0.50), (6.00, 0.30)],
            "RANGING": [(1.50, 0.75), (3.00, 0.55), (4.00, 0.40), (6.00, 0.20)],
            "HIGH_VOL": [(1.50, 1.20), (3.00, 0.90), (4.00, 0.65), (6.00, 0.35)],
            "LOW_VOL": [(1.50, 0.60), (3.00, 0.40), (4.00, 0.25), (6.00, 0.12)],
        },
        "be_buffer": 0.70,
    },
    "ETHUSD": {
        "momentum_period": 24,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0,
        "tp_atr_ranging": 4.0,
        "threshold_trending": 2.0,
        "threshold_ranging": 1.5,
        "adx_slope_threshold": -6.0,
        "adx_slope_threshold_strong": -10.0,
        "pullback_band_trending": 0.8,
        "pullback_band_ranging": 0.5,
        "max_lot": 0.05,
        "risk_mult": 0.50,
        "first_lock_atr": 1.2,
        "trailing": {
            "TREND_UP": [(1.20, 0.90), (2.50, 0.60), (3.50, 0.40), (5.50, 0.25)],
            "TREND_DOWN": [(1.20, 0.90), (2.50, 0.60), (3.50, 0.40), (5.50, 0.25)],
            "RANGING": [(1.20, 0.65), (2.50, 0.45), (3.50, 0.30), (5.50, 0.18)],
            "HIGH_VOL": [(1.20, 1.10), (2.50, 0.80), (3.50, 0.55), (5.50, 0.30)],
            "LOW_VOL": [(1.20, 0.50), (2.50, 0.35), (3.50, 0.20), (5.50, 0.10)],
        },
        "be_buffer": 0.65,
    },
    "US500.cash": {
        "momentum_period": 24,
        "sl_atr_trending": 2.0,
        "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5,
        "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0,
        "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "max_lot": 0.10,
        "risk_mult": 0.50,
        "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(0.80, 0.50), (2.00, 0.30), (3.00, 0.20), (5.00, 0.10)],
            "TREND_DOWN": [(0.80, 0.50), (2.00, 0.30), (3.00, 0.20), (5.00, 0.10)],
            "RANGING": [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
            "HIGH_VOL": [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
            "LOW_VOL": [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
        },
        "be_buffer": 0.60,
    },
    # EURUSD supprimé — WR 0% live, purgé Phase 0 (Juin 2026)
}

SYMBOLS = list(SYMBOL_CONFIG.keys())
TIMEFRAMES = ["H1", "H4", "D1"]


# ============================================================================
# PIP INFO
# ============================================================================
def get_pip_info(symbol):
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    elif symbol in ("US500.cash", "JP225.cash", "US30.cash", "NAS100.cash"):
        return 0.01, 1.0
    elif symbol in ("USOIL.cash", "UKOIL.cash", "BTCUSD", "ETHUSD"):
        return 0.01, 1.0
    return 0.0001, 10.0


# ============================================================================
# SIMTRADE CLASS (same as backtest_16y_full.py)
# ============================================================================
class SimTrade:
    __slots__ = (
        "symbol",
        "timeframe",
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
        "profit_pct",
        "peak_price",
        "trailing_sl",
        "partial_closed",
        "bars_held",
        "close_time",
        "close_price",
        "lot",
        "config",
        "_pip_size",
        "_pip_value",
    )

    def __init__(self, symbol, timeframe, action, entry, sl, tp, atr_val, regime, bar_idx, bar_time, balance, config):
        self.symbol = symbol
        self.timeframe = timeframe
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
        self.profit_pct = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.config = config
        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._calc_lot(entry, sl, balance)

    def _calc_lot(self, entry, sl, balance):
        price_dist = abs(entry - sl)
        if price_dist > 0:
            risk_usd = balance * RISK_PER_TRADE * self.config.get("risk_mult", 1.0)
            risk_in_pips = price_dist / self._pip_size
            if risk_in_pips > 0:
                self.lot = risk_usd / (risk_in_pips * self._pip_value)
        max_lot = self.config.get("max_lot", MAX_LOT)
        self.lot = max(0.01, min(max_lot, self.lot))

    def check_sl_tp(self, high, low, close, bar_idx, bar_time):
        if self.closed:
            return
        if self.direction == 0:
            if low <= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                self.closed = True
            elif high >= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                self.closed = True
        else:
            if high >= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                self.closed = True
            elif low <= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                self.closed = True
        if self.closed:
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            self._calc_pnl()

    def _calc_pnl(self):
        if self.direction == 0:
            self.profit_pct = (self.close_price - self.entry) / self.entry
        else:
            self.profit_pct = (self.entry - self.close_price) / self.entry
        pips = (self.close_price - self.entry) * (1 if self.direction == 0 else -1) / self._pip_size
        self.profit_usd = pips * self.lot * self._pip_value

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
        first_lock = self.config.get("first_lock_atr", 1.0)
        if self.direction == 0:
            profit_atr = (self.peak_price - self.entry) / atr_v
        else:
            profit_atr = (self.entry - self.peak_price) / atr_v
        if profit_atr <= first_lock:
            return
        trailing_levels = self.config.get("trailing", {})
        lvls = trailing_levels.get(self.regime, [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)])
        trail_dist = lvls[-1][1]
        for thresh, dist in reversed(lvls):
            if profit_atr > thresh:
                trail_dist = dist
                break
        dist = trail_dist * atr_v
        if self.direction == 0:
            new_sl = self.peak_price - dist
            if new_sl > self.trailing_sl:
                self.trailing_sl = new_sl
        else:
            new_sl = self.peak_price + dist
            if new_sl < self.trailing_sl:
                self.trailing_sl = new_sl

    def check_partial_tp(self, atr_v):
        if self.closed or self.partial_closed or atr_v <= 0:
            return
        if self.direction == 0:
            progress = (self.peak_price - self.entry) / max(self.tp - self.entry, 1e-10)
        else:
            progress = (self.entry - self.peak_price) / max(self.entry - self.tp, 1e-10)
        if progress < 0.60:
            return
        self.partial_closed = True
        be = self.config.get("be_buffer", 0.80) * atr_v
        if self.direction == 0:
            be_sl = self.entry + be
            if be_sl > self.trailing_sl:
                self.trailing_sl = be_sl
        else:
            be_sl = self.entry - be
            if be_sl < self.trailing_sl:
                self.trailing_sl = be_sl


# ============================================================================
# INDICATORS
# ============================================================================
def precalc_atr_and_adx(high, low, close, period=14):
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        atr_arr[i] = np.mean(tr[i - period : i])
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    pos_dm = np.where((up > down) & (up > 0), up, 0)
    neg_dm = np.where((down > up) & (down > 0), down, 0)
    tr_smoothed = np.full(len(close), np.nan)
    pos_smoothed = np.full(len(close), np.nan)
    neg_smoothed = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        tr_smoothed[i] = np.mean(tr[i - period : i])
        pos_smoothed[i] = np.mean(pos_dm[i - period : i])
        neg_smoothed[i] = np.mean(neg_dm[i - period : i])
    pos_di = 100 * pos_smoothed / tr_smoothed
    neg_di = 100 * neg_smoothed / tr_smoothed
    dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di)
    adx_arr = np.full(len(close), np.nan)
    for i in range(period * 2, len(close)):
        adx_arr[i] = np.mean(dx[i - period : i])
    return atr_arr, adx_arr


def calc_ema(arr, period):
    ema = np.full(len(arr), np.nan)
    if len(arr) < period:
        return ema
    ema[period - 1] = np.mean(arr[:period])
    mult = 2.0 / (period + 1)
    for i in range(period, len(arr)):
        ema[i] = arr[i] * mult + ema[i - 1] * (1 - mult)
    return ema


def detect_regime(adx_val, atr_pct):
    if atr_pct > 80:
        return "HIGH_VOL"
    if atr_pct < 20:
        return "LOW_VOL"
    if adx_val >= 22:
        return "TREND_UP"
    return "RANGING"


# ============================================================================
# BACKTEST ENGINE
# ============================================================================
def backtest_symbol_tf(symbol, timeframe, df, config):
    """Run MOM20x3 backtest on one symbol+timeframe combination."""
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    atr_arr, adx_arr = precalc_atr_and_adx(high, low, close)
    ema_short = calc_ema(close, config["momentum_period"])

    all_trades = []
    open_trades = []
    bars_since_last = 999

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0

        for t in open_trades:
            t.update_peak(high[i], low[i])
            atr_t = atr_v if atr_v > 0 else t.atr_val
            t.check_partial_tp(atr_t)
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            t.update_trailing(atr_t)
            if not t.closed:
                timeout = TIMEOUT_BARS.get(timeframe, 120)
                if i - t.open_bar > timeout:
                    t.closed = True
                    t.close_price = close[i]
                    t.close_time = times[i]
                    t.result = "TIMEOUT"
                    t.bars_held = i - t.open_bar
                    t._calc_pnl()

        open_trades = [t for t in open_trades if not t.closed]

        if atr_v <= 0 or np.isnan(adx_arr[i]):
            continue
        bars_since_last += 1
        if bars_since_last < 1:
            continue

        mom_period = config["momentum_period"]
        if i < mom_period:
            continue
        mom = close[i] - close[i - mom_period]
        if np.isnan(mom) or np.isinf(mom):
            continue

        adx_val = adx_arr[i]
        is_trending = adx_val >= 22
        if is_trending:
            thresh = config["threshold_trending"] * atr_v
        else:
            thresh = config["threshold_ranging"] * atr_v

        raw_score = abs(mom) / max(thresh, 1e-10)

        if i >= 5 and not np.isnan(adx_arr[i - 5]):
            adx_slope = adx_val - adx_arr[i - 5]
            slope_thresh = config["adx_slope_threshold"]
            if raw_score > 0.70:
                slope_thresh = config["adx_slope_threshold_strong"]
            if adx_slope < slope_thresh:
                continue

        if mom > thresh:
            action = "BUY"
        elif mom < -thresh:
            action = "SELL"
        else:
            continue

        sym_positions = [t for t in open_trades if t.symbol == symbol]
        if len(sym_positions) >= 2:
            continue

        atr_pct = (atr_v / close[i] * 100) if close[i] > 0 else 0
        regime = detect_regime(adx_val, atr_pct)
        if regime == "TREND_UP" and action == "SELL":
            regime = "TREND_DOWN"
        elif regime == "TREND_DOWN" and action == "BUY":
            regime = "TREND_UP"

        if is_trending:
            sl_dist = config["sl_atr_trending"] * atr_v
            tp_dist = config["tp_atr_trending"] * atr_v
        else:
            sl_dist = config["sl_atr_ranging"] * atr_v
            tp_dist = config["tp_atr_ranging"] * atr_v

        if action == "BUY":
            entry = close[i]
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            entry = close[i]
            sl = entry + sl_dist
            tp = entry - tp_dist

        if sl_dist > 0:
            rr = tp_dist / sl_dist
            if rr < 2.0:
                continue

        trade = SimTrade(symbol, timeframe, action, entry, sl, tp, atr_v, regime, i, times[i], INITIAL_BALANCE, config)
        open_trades.append(trade)
        all_trades.append(trade)
        bars_since_last = 0

    for t in open_trades:
        if not t.closed:
            t.closed = True
            t.close_price = close[-1]
            t.close_time = times[-1]
            t.result = "EOD"
            t.bars_held = n - 1 - t.open_bar
            t._calc_pnl()

    return all_trades


def compute_metrics(trades):
    closed = [t for t in trades if t.closed]
    if not closed:
        return {
            "n": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0,
            "p_value": 1.0,
            "significant": False,
        }
    n = len(closed)
    wins = [t for t in closed if t.profit_usd > 0]
    n_wins = len(wins)
    wr = n_wins / n * 100 if n > 0 else 0
    total_pnl = sum(t.profit_usd for t in closed)
    gross_profit = sum(max(0, t.profit_usd) for t in closed)
    gross_loss = abs(sum(min(0, t.profit_usd) for t in closed))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)
    peak = INITIAL_BALANCE
    dd_max = 0.0
    balance = INITIAL_BALANCE
    sorted_trades = sorted(closed, key=lambda x: (x.open_time is None, x.open_time or ""))
    for t in sorted_trades:
        balance += t.profit_usd
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    else:
        p = 1.0
    return {
        "n": n,
        "wins": n_wins,
        "losses": n - n_wins,
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "p_value": round(p, 4),
        "significant": p < 0.05,
    }


# ============================================================================
# WALK-FORWARD ENGINE
# ============================================================================
def create_folds(n_bars, n_folds=5, test_ratio=0.15):
    """Crée des folds temporels pour le walk-forward."""
    test_size = int(n_bars * test_ratio)
    min_train = n_bars - test_size * n_folds
    folds = []
    for i in range(n_folds):
        test_end = n_bars - (n_folds - 1 - i) * test_size
        test_start = test_end - test_size
        train_end = test_start
        if train_end < MIN_BARS:
            continue
        folds.append(
            {
                "train_start": 0,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "fold": i + 1,
            }
        )
    return folds


def run_walk_forward():
    """Exécute le walk-forward validation complet."""
    data_dir = Path("data/historical")
    out_dir = Path("runtime")
    out_dir.mkdir(exist_ok=True)

    print("=" * 100)
    print("  WALK-FORWARD VALIDATION — 5 FOLDS TEMPORELS")
    print("  MOM20x3 + Trailing ATR + Partial TP")
    print("=" * 100)

    start_all = now()
    all_results = {}
    total_folds = 0

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            fpath = data_dir / f"{sym}_{tf}.parquet"
            if not fpath.exists():
                continue

            df = pd.read_parquet(fpath)
            if len(df) < MIN_BARS + 100:
                print(f"  {sym}_{tf}: SKIP ({len(df)} bars)")
                continue

            config = SYMBOL_CONFIG[sym]
            n_bars = len(df)
            folds = create_folds(n_bars, n_folds=5, test_ratio=0.15)

            if not folds:
                print(f"  {sym}_{tf}: SKIP (not enough folds)")
                continue

            print(f"\n  ═══ {sym}_{tf} ({n_bars} bars, {len(folds)} folds) ═══")
            key = f"{sym}_{tf}"
            all_results[key] = {"folds": [], "oos_metrics": [], "is_metrics": []}

            for fold in folds:
                t0 = now()
                # In-sample (train)
                train_df = df.iloc[fold["train_start"] : fold["train_end"]].reset_index(drop=True)
                is_trades = backtest_symbol_tf(sym, tf, train_df, config)
                is_metrics = compute_metrics(is_trades)

                # Out-of-sample (test)
                test_df = df.iloc[fold["test_start"] : fold["test_end"]].reset_index(drop=True)
                oos_trades = backtest_symbol_tf(sym, tf, test_df, config)
                oos_metrics = compute_metrics(oos_trades)

                elapsed = now() - t0
                wr_drop = is_metrics["win_rate"] - oos_metrics["win_rate"]

                fold_result = {
                    "fold": fold["fold"],
                    "train_bars": fold["train_end"] - fold["train_start"],
                    "test_bars": fold["test_end"] - fold["test_start"],
                    "is_wr": is_metrics["win_rate"],
                    "is_pf": is_metrics["profit_factor"],
                    "is_pnl": is_metrics["total_pnl"],
                    "is_n": is_metrics["n"],
                    "oos_wr": oos_metrics["win_rate"],
                    "oos_pf": oos_metrics["profit_factor"],
                    "oos_pnl": oos_metrics["total_pnl"],
                    "oos_n": oos_metrics["n"],
                    "oos_dd": oos_metrics["max_drawdown_pct"],
                    "oos_p_value": oos_metrics["p_value"],
                    "oos_significant": oos_metrics["significant"],
                    "wr_drop": round(wr_drop, 1),
                    "elapsed_s": round(elapsed, 1),
                }
                all_results[key]["folds"].append(fold_result)
                all_results[key]["oos_metrics"].append(oos_metrics)
                all_results[key]["is_metrics"].append(is_metrics)

                status = "✅" if oos_metrics["win_rate"] >= 55 and wr_drop < 15 else "⚠️"
                print(
                    f"    Fold {fold['fold']}: IS WR {is_metrics['win_rate']:5.1f}% → "
                    f"OOS WR {oos_metrics['win_rate']:5.1f}% "
                    f"(drop {wr_drop:+.1f}%) PF {oos_metrics['profit_factor']:.2f} "
                    f"DD {oos_metrics['max_drawdown_pct']:5.1f}% "
                    f"{'✓' if oos_metrics['significant'] else '✗'} "
                    f"[{elapsed:.1f}s] {status}"
                )
                total_folds += 1

    # ═══ AGGREGATE ═══
    print(f"\n{'=' * 100}")
    print(f"  RÉSUMÉ WALK-FORWARD — {total_folds} FOLDS")
    print(f"{'=' * 100}")

    print(
        f"\n  {'Key':16s} {'IS WR':>7s} {'OOS WR':>7s} {'Drop':>7s} {'OOS PF':>7s} "
        f"{'OOS DD':>7s} {'Sig':>5s} {'Verdict':>10s}"
    )
    print(f"  {'-' * 80}")

    summary = {"pass": 0, "fail": 0, "marginal": 0}
    for key, data in sorted(all_results.items()):
        if not data["folds"]:
            continue
        avg_is_wr = np.mean([f["is_wr"] for f in data["folds"]])
        avg_oos_wr = np.mean([f["oos_wr"] for f in data["folds"]])
        avg_drop = avg_is_wr - avg_oos_wr
        avg_oos_pf = np.mean([f["oos_pf"] for f in data["folds"]])
        avg_oos_dd = np.mean([f["oos_dd"] for f in data["folds"]])
        any_sig = any(f["oos_significant"] for f in data["folds"])

        if avg_oos_wr >= 55 and avg_drop < 15 and avg_oos_pf >= 1.0:
            verdict = "✅ PASS"
            summary["pass"] += 1
        elif avg_oos_wr >= 50 and avg_drop < 20:
            verdict = "⚠️ MARGINAL"
            summary["marginal"] += 1
        else:
            verdict = "❌ FAIL"
            summary["fail"] += 1

        print(
            f"  {key:16s} {avg_is_wr:>6.1f}% {avg_oos_wr:>6.1f}% {avg_drop:>+6.1f}% "
            f"{avg_oos_pf:>6.2f} {avg_oos_dd:>6.1f}% "
            f"{'  ✓' if any_sig else '  ✗'} {verdict}"
        )

    print(f"\n  {'─' * 80}")
    print(f"  PASS: {summary['pass']} | MARGINAL: {summary['marginal']} | FAIL: {summary['fail']}")
    print(f"  Total folds: {total_folds} | Time: {now() - start_all:.0f}s")

    # ═══ VERDICT ═══
    print(f"\n{'═' * 100}")
    print(f"  VERDICT FINAL")
    print(f"{'═' * 100}")

    total_keys = len([k for k, v in all_results.items() if v["folds"]])
    if summary["pass"] > total_keys * 0.6:
        print(f"  ✅ WALK-FORWARD PASS — {summary['pass']}/{total_keys} symboles robustes")
        print(f"  → Le MOM20x3 a un edge statistique réel")
        print(f"  → On peut intégrer les modules et continuer")
    elif summary["fail"] > total_keys * 0.4:
        print(f"  ❌ WALK-FORWARD FAIL — {summary['fail']}/{total_keys} symboles overfités")
        print(f"  → Le MOM20x3 n'est pas robuste hors échantillon")
        print(f"  → Il faut ajuster les seuils ou changer de stratégie")
    else:
        print(f"  ⚠️ WALK-FORWARD MARGINAL — résultats mitigés")
        print(f"  → Certains symboles sont robustes, d'autres non")
        print(f"  → Envisager de désactiver les symboles faibles")

    print(f"{'═' * 100}")

    # Save results
    out_path = out_dir / "walk_forward_results.json"
    export = {
        "summary": summary,
        "total_folds": total_folds,
        "elapsed_s": round(now() - start_all, 1),
        "results": {},
    }
    for key, data in all_results.items():
        if data["folds"]:
            avg_is_wr = np.mean([f["is_wr"] for f in data["folds"]])
            avg_oos_wr = np.mean([f["oos_wr"] for f in data["folds"]])
            export["results"][key] = {
                "folds": data["folds"],
                "avg_is_wr": round(avg_is_wr, 1),
                "avg_oos_wr": round(avg_oos_wr, 1),
                "wr_drop": round(avg_is_wr - avg_oos_wr, 1),
            }
    with open(out_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  Saved results to {out_path}")

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    run_walk_forward()
