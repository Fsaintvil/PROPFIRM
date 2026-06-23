"""
Backtest MOM20x3 16 ans — 4 symboles actifs (XAUUSD, BTCUSD, ETHUSD, US500.cash)
avec trailing ATR, partial TP, et calibrage par symbole.

Utilise les données Parquet de data/historical/.
Inclut: trailing ATR multi-niveaux, partial TP à 60%, BE buffer, time-stop.

Usage:
    python scripts/backtest_16y_full.py
    python scripts/backtest_16y_full.py --symbols XAUUSD,BTCUSD
    python scripts/backtest_16y_full.py --csv  # export CSV
"""

import os
import sys
import json
import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from math import sqrt, erf

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx

# ============================================================================
# CONFIGURATION
# ============================================================================
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.004  # 0.4% per trade
MIN_BARS = 60
MAX_LOT = 1.0

# Timeouts par timeframe (bars)
TIMEOUT_BARS = {"M15": 200, "H1": 120, "H4": 60, "D1": 30}

# ============================================================================
# CALIBRATION PAR SYMOLE (from strategy.py + ftmo_config.py)
# ============================================================================
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
# SIMTRADE CLASS
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
        # First lock: profit > first_lock_atr × ATR
        first_lock = self.config.get("first_lock_atr", 1.0)
        if self.direction == 0:
            profit_atr = (self.peak_price - self.entry) / atr_v
        else:
            profit_atr = (self.entry - self.peak_price) / atr_v
        if profit_atr <= first_lock:
            return
        # Multi-level trailing
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

    def to_dict(self):
        return dict(
            symbol=self.symbol,
            timeframe=self.timeframe,
            action=self.action,
            regime=self.regime,
            entry=round(self.entry, 5),
            sl=round(self.sl, 5),
            tp=round(self.tp, 5),
            close_price=round(self.close_price, 5),
            result=self.result,
            profit_usd=round(self.profit_usd, 2),
            profit_pct=round(self.profit_pct * 100, 3),
            bars_held=self.bars_held,
            partial_tp=self.partial_closed,
            lot=round(self.lot, 4),
            open_time=str(self.open_time)[:19] if self.open_time is not None else "",
            close_time=str(self.close_time)[:19] if self.close_time is not None else "",
        )


# ============================================================================
# INDICATORS
# ============================================================================
def precalc_atr_and_adx(high, low, close, period=14):
    """Pre-calculate ATR and ADX arrays for all bars."""
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        atr_arr[i] = np.mean(tr[i - period : i])

    # ADX
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
    """Calculate EMA array."""
    ema = np.full(len(arr), np.nan)
    if len(arr) < period:
        return ema
    ema[period - 1] = np.mean(arr[:period])
    mult = 2.0 / (period + 1)
    for i in range(period, len(arr)):
        ema[i] = arr[i] * mult + ema[i - 1] * (1 - mult)
    return ema


def detect_regime(adx_val, atr_pct):
    """Detect market regime from ADX and ATR%."""
    if atr_pct > 80:
        return "HIGH_VOL"
    if atr_pct < 20:
        return "LOW_VOL"
    if adx_val >= 22:
        return "TREND_UP"  # Will be refined by direction
    return "RANGING"


# ============================================================================
# BACKTEST ENGINE
# ============================================================================
def backtest_symbol_tf(symbol, timeframe, df):
    """Run MOM20x3 backtest on one symbol+timeframe combination."""
    config = SYMBOL_CONFIG.get(symbol, SYMBOL_CONFIG["XAUUSD"])
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    # Pre-calculate indicators once
    atr_arr, adx_arr = precalc_atr_and_adx(high, low, close)

    # EMA for momentum
    ema_short = calc_ema(close, config["momentum_period"])
    ema_long = calc_ema(close, 20)

    all_trades = []
    open_trades = []
    bars_since_last = 999

    for i in range(MIN_BARS, n):
        # Update open trades
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

        # Remove closed trades
        open_trades = [t for t in open_trades if not t.closed]

        # Signal generation
        if atr_v <= 0 or np.isnan(adx_arr[i]):
            continue

        bars_since_last += 1
        if bars_since_last < 1:
            continue

        # MOM20x3: momentum = c[i] - c[i-20]
        mom_period = config["momentum_period"]
        if i < mom_period:
            continue
        mom = close[i] - close[i - mom_period]
        if np.isnan(mom) or np.isinf(mom):
            continue

        # Threshold
        adx_val = adx_arr[i]
        is_trending = adx_val >= 22
        if is_trending:
            thresh = config["threshold_trending"] * atr_v
        else:
            thresh = config["threshold_ranging"] * atr_v

        # Score
        raw_score = abs(mom) / max(thresh, 1e-10)

        # ADX slope filter
        if i >= 5 and not np.isnan(adx_arr[i - 5]):
            adx_slope = adx_val - adx_arr[i - 5]
            slope_thresh = config["adx_slope_threshold"]
            if raw_score > 0.70:
                slope_thresh = config["adx_slope_threshold_strong"]
            if adx_slope < slope_thresh:
                continue

        # Direction
        if mom > thresh:
            action = "BUY"
        elif mom < -thresh:
            action = "SELL"
        else:
            continue

        # Skip if already have position in this symbol
        sym_positions = [t for t in open_trades if t.symbol == symbol]
        if len(sym_positions) >= 2:
            continue

        # Regime
        atr_pct = (atr_v / close[i] * 100) if close[i] > 0 else 0
        regime = detect_regime(adx_val, atr_pct)
        if regime == "TREND_UP" and action == "SELL":
            regime = "TREND_DOWN"
        elif regime == "TREND_DOWN" and action == "BUY":
            regime = "TREND_UP"

        # SL/TP
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

        # RR check
        if sl_dist > 0:
            rr = tp_dist / sl_dist
            if rr < 2.0:
                continue

        # Create trade
        trade = SimTrade(symbol, timeframe, action, entry, sl, tp, atr_v, regime, i, times[i], INITIAL_BALANCE, config)
        open_trades.append(trade)
        all_trades.append(trade)
        bars_since_last = 0

    # Close remaining trades
    for t in open_trades:
        if not t.closed:
            t.closed = True
            t.close_price = close[-1]
            t.close_time = times[-1]
            t.result = "EOD"
            t.bars_held = n - 1 - t.open_bar
            t._calc_pnl()

    return all_trades


# ============================================================================
# METRICS
# ============================================================================
def compute_metrics(trades):
    closed = [t for t in trades if t.closed]
    if not closed:
        return {"n": 0}

    wins = [t for t in closed if t.profit_usd > 0]
    losses = [t for t in closed if t.profit_usd <= 0]
    n = len(closed)
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
# MAIN
# ============================================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest MOM20x3 16y — 5 symboles")
    parser.add_argument("--symbols", type=str, default=None, help="Symboles séparés par virgule (défaut: tous)")
    parser.add_argument("--csv", action="store_true", help="Export CSV")
    args = parser.parse_args()

    data_dir = Path("data/historical")
    out_dir = Path("runtime")
    out_dir.mkdir(exist_ok=True)

    # Filter symbols
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = list(SYMBOL_CONFIG.keys())

    parquet_files = sorted(data_dir.glob("*.parquet"))
    print(f"Files found: {len(parquet_files)}")
    print(f"Symbols: {symbols}\n")

    from time import time as now

    start_all = now()

    all_trades = {}
    total_bars = 0

    for fpath in parquet_files:
        parts = fpath.stem.split("_")
        tf = parts[-1]
        if tf not in ("M15", "H1", "H4", "D1"):
            continue
        symbol = "_".join(parts[:-1])

        if symbol not in symbols:
            continue

        df = pd.read_parquet(fpath)
        if len(df) < MIN_BARS:
            print(f"  {symbol}_{tf}: SKIP ({len(df)} bars)")
            continue

        t0 = now()
        trades = backtest_symbol_tf(symbol, tf, df)
        elapsed = now() - t0
        total_bars += len(df)

        closed = [t for t in trades if t.closed]
        key = f"{symbol}_{tf}"
        all_trades[key] = closed

        if not closed:
            print(f"  {symbol}_{tf}: 0 trades ({len(df)} bars, {elapsed:.1f}s)")
        else:
            m = compute_metrics(closed)
            print(
                f"  {symbol}_{tf}: {m['n']:>5d} trades | {m['win_rate']:5.1f}% WR | "
                f"${m['total_pnl']:>+9.2f} PnL | {m['profit_factor']:.2f} PF | "
                f"DD {m['max_drawdown_pct']:5.1f}% | {elapsed:.1f}s"
            )

    # Save trades
    out_path = out_dir / "backtest_16y_full.pkl"
    all_trades_dict = {}
    for key, tlist in all_trades.items():
        all_trades_dict[key] = [t.to_dict() for t in tlist]

    with open(out_path, "wb") as f:
        pickle.dump(all_trades_dict, f)
    total_trades = sum(len(v) for v in all_trades_dict.values())
    print(f"\nSaved {total_trades} trades to {out_path}")
    print(f"Processed {total_bars} bars in {now() - start_all:.0f}s")

    # Summary table
    print(f"\n{'=' * 90}")
    print(f"  BACKTEST MOM20x3 16Y — SUMMARY (5 symboles, trailing ATR + partial TP)")
    print(f"{'=' * 90}")
    print(f"  {'Symbol':12s} {'TF':3s} {'Trades':>7s} {'WR':>7s} {'PnL':>10s} {'PF':>6s} {'DD Max':>7s} {'Sig':>5s}")
    print(f"  {'-' * 65}")

    # Aggregate by symbol
    symbol_agg = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0, "dd": 0, "pf": 0, "sig": False})
    for key, trades in all_trades.items():
        if not trades:
            continue
        m = compute_metrics(trades)
        symbol = key.split("_")[0]
        tf = key.split("_")[1]
        print(
            f"  {key:12s} {m['n']:>7d} {m['win_rate']:>6.1f}% ${m['total_pnl']:>+9.2f} "
            f"{m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>6.1f}% "
            f"{'  ✓' if m['significant'] else '  ✗'}"
        )

        symbol_agg[symbol]["n"] += m["n"]
        symbol_agg[symbol]["pnl"] += m["total_pnl"]
        symbol_agg[symbol]["wins"] += m["wins"]
        symbol_agg[symbol]["dd"] = max(symbol_agg[symbol]["dd"], m["max_drawdown_pct"])
        symbol_agg[symbol]["sig"] = symbol_agg[symbol]["sig"] or m["significant"]

    print(f"  {'-' * 65}")
    for sym, agg in sorted(symbol_agg.items()):
        wr = agg["wins"] / agg["n"] * 100 if agg["n"] > 0 else 0
        print(
            f"  {sym:12s} {agg['n']:>7d} {wr:>6.1f}% ${agg['pnl']:>+9.2f} "
            f"{'':>5s} {agg['dd']:>6.1f}% {'  ✓' if agg['sig'] else '  ✗'}"
        )

    # Total
    total_n = sum(a["n"] for a in symbol_agg.values())
    total_pnl = sum(a["pnl"] for a in symbol_agg.values())
    total_wins = sum(a["wins"] for a in symbol_agg.values())
    total_wr = total_wins / total_n * 100 if total_n > 0 else 0
    print(f"  {'-' * 65}")
    print(f"  {'TOTAL':12s} {total_n:>7d} {total_wr:>6.1f}% ${total_pnl:>+9.2f}")

    # Export CSV
    if args.csv:
        csv_path = out_dir / "backtest_16y_full.csv"
        rows = []
        for key, trades in all_trades_dict.items():
            rows.extend(trades)
        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False)
        print(f"\nExported {len(rows)} trades to {csv_path}")

    # Save metrics JSON
    metrics_path = out_dir / "backtest_16y_metrics.json"
    metrics = {}
    for key, trades in all_trades.items():
        if trades:
            metrics[key] = compute_metrics(trades)
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Saved metrics to {metrics_path}")


if __name__ == "__main__":
    main()
