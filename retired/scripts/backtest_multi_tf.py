"""
Backtest MOM20x3 on H1/H4/D1 for 15 symbols — OPTIMIZED VERSION.
Loads Parquet files from data/historical/, runs MOM20x3 signal,
simulates SL/TP ATR + trailing + partial TP.

Optimization: pre-calculates ATR/ADX arrays once, avoids O(n²).

Usage:
    python scripts/backtest_multi_tf.py
"""
import os
import sys
import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from math import sqrt, erf

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx

INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044
MIN_BARS = 60
BE_BUFFER_ATR = 0.80
TIMEOUT_BARS = {"H1": 120, "H4": 60, "D1": 30}
MAX_LOT = 1.0

THRESHOLD_TRENDING = 2.5
THRESHOLD_RANGING = 2.0
THRESHOLD_MAX = 2.5
THRESHOLD_MIN = 1.5
SL_ATR_TRENDING = 2.0
TP_ATR_TRENDING = 5.0
SL_ATR_RANGING = 1.5
TP_ATR_RANGING = 4.0

TRAILING_LEVELS = {
    "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
    "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
    "LOW_VOL": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
}


def get_pip_info(symbol):
    if symbol in ('XAUUSD', 'XAGUSD'):
        return 0.01, 1.0
    elif symbol in ('US500.cash', 'JP225.cash', 'US30.cash', 'NAS100.cash'):
        return 0.01, 1.0
    elif symbol in ('USOIL.cash', 'UKOIL.cash', 'BTCUSD', 'ETHUSD'):
        return 0.01, 1.0
    return 0.0001, 10.0


class SimTrade:
    __slots__ = ('symbol', 'timeframe', 'action', 'entry', 'sl', 'tp',
                 'atr_val', 'regime', 'open_bar', 'open_time', 'direction',
                 'closed', 'result', 'profit_usd', 'profit_pct',
                 'peak_price', 'trailing_sl', 'partial_closed',
                 'bars_held', 'close_time', 'close_price', 'lot',
                 '_pip_size', '_pip_value')

    def __init__(self, symbol, timeframe, action, entry, sl, tp,
                 atr_val, regime, bar_idx, bar_time, balance):
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

        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._calc_lot(entry, sl, balance)

    def _calc_lot(self, entry, sl, balance):
        price_dist = abs(entry - sl)
        if price_dist > 0:
            risk_usd = balance * RISK_PER_TRADE
            risk_in_pips = price_dist / self._pip_size
            if risk_in_pips > 0:
                self.lot = risk_usd / (risk_in_pips * self._pip_value)
        self.lot = max(0.01, min(MAX_LOT, self.lot))

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
        if self.direction == 0:
            profit_atr = (self.peak_price - self.entry) / atr_v
        else:
            profit_atr = (self.entry - self.peak_price) / atr_v
        if profit_atr <= 1.0:
            return
        lvls = TRAILING_LEVELS.get(self.regime, TRAILING_LEVELS["RANGING"])
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
        be = BE_BUFFER_ATR * atr_v
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
            symbol=self.symbol, timeframe=self.timeframe,
            action=self.action, regime=self.regime,
            entry=round(self.entry, 5), sl=round(self.sl, 5),
            tp=round(self.tp, 5), close_price=round(self.close_price, 5),
            result=self.result, profit_usd=round(self.profit_usd, 2),
            profit_pct=round(self.profit_pct * 100, 3),
            bars_held=self.bars_held, partial_tp=self.partial_closed,
            lot=round(self.lot, 4),
            open_time=str(self.open_time)[:19] if self.open_time is not None else "",
            close_time=str(self.close_time)[:19] if self.close_time is not None else "",
        )


def precalc_atr_and_adx(high, low, close, period=14):
    """Pre-calculate ATR and ADX arrays for all bars."""
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        atr_arr[i] = np.mean(tr[i - period:i])

    # ADX
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    pos_dm = np.where((up > down) & (up > 0), up, 0)
    neg_dm = np.where((down > up) & (down > 0), down, 0)

    tr_smoothed = np.full(len(close), np.nan)
    pos_smoothed = np.full(len(close), np.nan)
    neg_smoothed = np.full(len(close), np.nan)

    for i in range(period, len(close)):
        tr_smoothed[i] = np.mean(tr[i - period:i])
        pos_smoothed[i] = np.mean(pos_dm[i - period:i])
        neg_smoothed[i] = np.mean(neg_dm[i - period:i])

    pos_di = 100 * pos_smoothed / tr_smoothed
    neg_di = 100 * neg_smoothed / tr_smoothed
    dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di)

    adx_arr = np.full(len(close), np.nan)
    for i in range(period * 2, len(close)):
        adx_arr[i] = np.mean(dx[i - period:i])

    return atr_arr, adx_arr


def backtest_symbol_tf(symbol, timeframe, df):
    """Run MOM20x3 backtest on one symbol+timeframe combination."""
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    # Pre-calculate indicators once
    atr_arr, adx_arr = precalc_atr_and_adx(high, low, close)

    all_trades = []
    open_trades = []
    bars_since_last = 999

    for i in range(MIN_BARS, n):
        # Update open trades
        still_open = []
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

            if not t.closed:
                still_open.append(t)

        open_trades = still_open
        bars_since_last += 1

        # Generate signal (using pre-calculated ATR/ADX)
        current_atr = atr_arr[i]
        current_adx = adx_arr[i]
        if np.isnan(current_atr) or current_atr <= 0 or np.isnan(current_adx):
            continue

        # MOM20x3 logic (inlined for speed)
        mom = float(close[i] - close[i - 20]) if i >= 20 else 0
        mom_abs = abs(mom)
        is_trending = current_adx >= 25
        thresh = THRESHOLD_TRENDING if is_trending else THRESHOLD_RANGING
        thresh_val = max(THRESHOLD_MIN, min(THRESHOLD_MAX, thresh)) * current_atr

        if mom_abs < thresh_val:
            continue

        # Rate limit
        if bars_since_last < 5:
            continue

        action = "BUY" if mom > 0 else "SELL"

        # Direction conflict
        if any(t.action == action for t in open_trades):
            continue

        # SL/TP
        if is_trending:
            sl_atr, tp_atr = SL_ATR_TRENDING, TP_ATR_TRENDING
        else:
            sl_atr, tp_atr = SL_ATR_RANGING, TP_ATR_RANGING

        sl_dist = sl_atr * current_atr
        tp_dist = tp_atr * current_atr

        if action == "BUY":
            sl_price = close[i] - sl_dist
            tp_price = close[i] + tp_dist
        else:
            sl_price = close[i] + sl_dist
            tp_price = close[i] - tp_dist

        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr < 2.0:
            continue

        # Determine regime
        if is_trending:
            regime = "TREND_UP" if action == "BUY" else "TREND_DOWN"
        else:
            regime = "RANGING"

        trade = SimTrade(symbol, timeframe, action, close[i], sl_price, tp_price,
                         current_atr, regime, i, times[i], INITIAL_BALANCE)
        all_trades.append(trade)
        open_trades.append(trade)
        bars_since_last = 0

    return all_trades


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
    pf = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0)

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
        "n": n, "wins": n_wins, "losses": n - n_wins,
        "win_rate": round(wr, 1), "total_pnl": round(total_pnl, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "p_value": round(p, 4), "significant": p < 0.05,
    }


def main():
    data_dir = Path("data/historical")
    out_path = Path("runtime/trades_backtest.pkl")
    parquet_files = sorted(data_dir.glob("*.parquet"))
    print(f"Files found: {len(parquet_files)}\n")

    from time import time as now
    start_all = now()

    all_trades = {}
    total_bars = 0

    for fpath in parquet_files:
        parts = fpath.stem.split("_")
        tf = parts[-1]
        if tf not in ("H1", "H4", "D1"):
            continue
        symbol = "_".join(parts[:-1])

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
            print(f"  {symbol}_{tf}: {m['n']:>5d} trades | {m['win_rate']:5.1f}% WR | "
                  f"${m['total_pnl']:>+9.2f} PnL | {m['profit_factor']:.2f} PF | "
                  f"{elapsed:.1f}s")

    # Save as dicts for portability (avoid SimTrade unpickle issues)
    all_trades_dict = {}
    for key, tlist in all_trades.items():
        all_trades_dict[key] = [t.to_dict() for t in tlist]

    with open(out_path, "wb") as f:
        pickle.dump(all_trades_dict, f)
    total_trades = sum(len(v) for v in all_trades_dict.values())
    print(f"\nSaved {total_trades} trades to {out_path}")
    print(f"Processed {total_bars} bars in {now() - start_all:.0f}s")

    # Summary table
    print(f"\n{'='*80}")
    print(f"  BACKTEST MOM20x3 — SUMMARY (H1/H4/D1, 15 symbols)")
    print(f"{'='*80}")
    print(f"  {'Symbol':12s} {'TF':3s} {'Trades':>7s} {'WR':>7s} {'PnL':>10s} "
          f"{'PF':>6s} {'DD Max':>7s} {'Edge':>6s}")
    print(f"  {'-'*60}")

    total_n = 0
    total_pnl = 0.0
    for key in sorted(all_trades.keys()):
        trades = all_trades[key]
        if not trades:
            continue
        m = compute_metrics(trades)
        sym, tf = key.split("_")
        edge = "✅" if m.get("significant") and m["win_rate"] > 50 else "❌"
        dd = m.get("max_drawdown_pct", 0)
        print(f"  {sym:12s} {tf:3s} {m['n']:>7d} {m['win_rate']:>6.1f}% "
              f"${m['total_pnl']:>+8.2f} {m['profit_factor']:>5.2f} "
              f"{dd:>5.1f}% {edge:>6s}")
        total_n += m["n"]
        total_pnl += m["total_pnl"]

    print(f"  {'-'*60}")
    print(f"  {'TOTAL':12s}       {total_n:>7d}               ${total_pnl:>+9.2f}")


if __name__ == "__main__":
    main()
