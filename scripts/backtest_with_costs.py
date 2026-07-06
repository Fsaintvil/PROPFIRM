"""
Backtest MOM20x3 WITH realistic costs (spread + commission + slippage)
vs clean backtest. Compares WR, PnL, PF degradation per symbol.

Usage:
    python scripts/backtest_with_costs.py
    python scripts/backtest_with_costs.py --tf H1
    python scripts/backtest_with_costs.py --symbols EURUSD,GBPUSD
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

from engine_simple.indicators import adx as calc_adx
from engine_simple.indicators import atr as calc_atr

# ─── Config ───────────────────────────────────────────
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

SYMBOL_MOMENTUM_PERIODS = {
    "USDCAD": 24, "USDCHF": 14, "EURUSD": 18, "GBPUSD": 20,
    "AUDUSD": 24, "NZDUSD": 22, "XAUUSD": 30,
}

TYPICAL_SPREAD_PIPS = {
    "EURUSD": 1.5, "GBPUSD": 1.5, "USDJPY": 1.5, "USDCAD": 1.5,
    "USDCHF": 1.5, "AUDUSD": 1.5, "NZDUSD": 1.5,
    "XAUUSD": 5.0, "USOIL.cash": 5.0,
    "US500.cash": 2.0, "JP225.cash": 2.0,
    "BTCUSD": 10.0, "ETHUSD": 10.0, "GBPJPY": 3.0, "EURJPY": 2.5,
}

DEFAULT_SPREAD_PIPS = 2.0
SLIPPAGE_PIPS = 1.0
COMMISSION_PER_100K = 7.0


def get_pip_info(symbol):
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    if symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash"):
        return 0.01, 1.0
    if symbol in ("USOIL.cash", "UKOIL.cash"):
        return 0.01, 1.0
    if symbol in ("BTCUSD", "ETHUSD"):
        return 0.01, 1.0
    return 0.0001, 10.0


def get_pip_value_per_lot(symbol):
    _, pv = get_pip_info(symbol)
    return pv


def get_contract_size(symbol):
    if symbol in ("XAUUSD", "XAGUSD"):
        return 100
    if symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash"):
        return 1
    if symbol in ("USOIL.cash", "UKOIL.cash"):
        return 100
    if symbol in ("BTCUSD", "ETHUSD"):
        return 1
    return 100_000


def get_momentum_period(symbol):
    return SYMBOL_MOMENTUM_PERIODS.get(symbol, 20)


def calc_spread_pips(symbol, df_row_spread=None):
    if df_row_spread is not None and df_row_spread > 0:
        return float(df_row_spread) / 10.0
    return TYPICAL_SPREAD_PIPS.get(symbol, DEFAULT_SPREAD_PIPS)


# ─── SimTrade with dual PnL ──────────────────────────

class SimTrade:
    __slots__ = ("symbol", "timeframe", "action", "entry", "sl", "tp",
                 "atr_val", "regime", "open_bar", "open_time", "direction",
                 "closed", "result", "profit_usd", "profit_pct",
                 "peak_price", "trailing_sl", "partial_closed",
                 "bars_held", "close_time", "close_price", "lot",
                 "_pip_size", "_pip_value", "_contract_size",
                 "cost_pips", "commission_usd", "spread_cost_pips",
                 "spread_from_data", "profit_usd_cost", "profit_pct_cost")

    def __init__(self, symbol, timeframe, action, entry, sl, tp,
                 atr_val, regime, bar_idx, bar_time, balance,
                 spread_pts=None):
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
        self.profit_usd_cost = 0.0
        self.profit_pct_cost = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.cost_pips = 0.0
        self.commission_usd = 0.0
        self.spread_cost_pips = 0.0
        self.spread_from_data = spread_pts if (spread_pts is not None and spread_pts > 0) else None

        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._contract_size = get_contract_size(symbol)
        self._calc_lot(entry, sl, balance)

    def _calc_lot(self, entry, sl, balance):
        price_dist = abs(entry - sl)
        if price_dist > 0:
            risk_usd = balance * RISK_PER_TRADE
            risk_in_pips = price_dist / self._pip_size
            if risk_in_pips > 0:
                self.lot = risk_usd / (risk_in_pips * self._pip_value)
        self.lot = max(0.01, min(MAX_LOT, self.lot))

    def _calc_costs(self):
        spread = TYPICAL_SPREAD_PIPS.get(self.symbol, DEFAULT_SPREAD_PIPS)
        if self.spread_from_data is not None and self.spread_from_data > 0:
            spread = self.spread_from_data / 10.0
        self.spread_cost_pips = spread
        self.cost_pips = spread + SLIPPAGE_PIPS
        notional = self.lot * self._contract_size * abs(self.entry)
        self.commission_usd = (notional / 100_000) * COMMISSION_PER_100K * 2

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
        pip_size = self._pip_size
        pip_value = self._pip_value
        usd_per_pip = self.lot * pip_value
        if self.direction == 0:
            pips = (self.close_price - self.entry) / pip_size
            self.profit_pct = (self.close_price - self.entry) / self.entry
        else:
            pips = (self.entry - self.close_price) / pip_size
            self.profit_pct = (self.entry - self.close_price) / self.entry
        self.profit_usd = pips * usd_per_pip
        self._calc_costs()
        pips_cost = pips - self.cost_pips
        self.profit_usd_cost = pips_cost * usd_per_pip - self.commission_usd
        if self.direction == 0:
            self.profit_pct_cost = self.profit_usd_cost / (self.entry * self.lot * self._contract_size) if self.entry > 0 else 0
        else:
            entry_notional = self.entry * self.lot * self._contract_size
            self.profit_pct_cost = self.profit_usd_cost / entry_notional if entry_notional > 0 else 0

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
            result=self.result,
            profit_usd=round(self.profit_usd, 2),
            profit_pct=round(self.profit_pct * 100, 3),
            profit_usd_cost=round(self.profit_usd_cost, 2),
            profit_pct_cost=round(self.profit_pct_cost * 100, 5),
            cost_pips=round(self.cost_pips, 1),
            spread_cost_pips=round(self.spread_cost_pips, 1),
            commission_usd=round(self.commission_usd, 2),
            bars_held=self.bars_held, partial_tp=self.partial_closed,
            lot=round(self.lot, 4),
            open_time=str(self.open_time)[:19] if self.open_time is not None else "",
            close_time=str(self.close_time)[:19] if self.close_time is not None else "",
        )


# ─── Precalc ─────────────────────────────────────────

def precalc_atr_and_adx(high, low, close, period=14):
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        atr_arr[i] = np.mean(tr[i - period:i])
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    pos_dm = np.where((up > down) & (up > 0), up, 0)
    neg_dm = np.where((down > up) & (down > 0), down, 0)
    tr_sm = np.full(len(close), np.nan)
    pos_sm = np.full(len(close), np.nan)
    neg_sm = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        tr_sm[i] = np.mean(tr[i - period:i])
        pos_sm[i] = np.mean(pos_dm[i - period:i])
        neg_sm[i] = np.mean(neg_dm[i - period:i])
    pos_di = 100 * pos_sm / tr_sm
    neg_di = 100 * neg_sm / tr_sm
    dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di)
    adx_arr = np.full(len(close), np.nan)
    for i in range(period * 2, len(close)):
        adx_arr[i] = np.mean(dx[i - period:i])
    return atr_arr, adx_arr


# ─── Backtest core ───────────────────────────────────

def backtest_symbol(symbol, timeframe, df):
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    spread_col = df["spread"].values.astype(float) if "spread" in df.columns else None
    n = len(close)
    atr_arr, adx_arr = precalc_atr_and_adx(high, low, close)
    period = get_momentum_period(symbol)

    all_trades = []
    open_trades = []
    bars_since_last = 999

    for i in range(MIN_BARS, n):
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

        current_atr = atr_arr[i]
        current_adx = adx_arr[i]
        if np.isnan(current_atr) or current_atr <= 0 or np.isnan(current_adx):
            continue

        mom = float(close[i] - close[i - period]) if i >= period else 0
        mom_abs = abs(mom)
        is_trending = current_adx >= 25
        thresh = THRESHOLD_TRENDING if is_trending else THRESHOLD_RANGING
        thresh_val = max(THRESHOLD_MIN, min(THRESHOLD_MAX, thresh)) * current_atr

        if mom_abs < thresh_val:
            continue

        if bars_since_last < 5:
            continue

        action = "BUY" if mom > 0 else "SELL"

        if any(t.action == action for t in open_trades):
            continue

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

        if is_trending:
            regime = "TREND_UP" if action == "BUY" else "TREND_DOWN"
        else:
            regime = "RANGING"

        spread_pts = float(spread_col[i]) if spread_col is not None else None

        trade = SimTrade(symbol, timeframe, action, close[i], sl_price, tp_price,
                         current_atr, regime, i, times[i], INITIAL_BALANCE,
                         spread_pts=spread_pts)
        all_trades.append(trade)
        open_trades.append(trade)
        bars_since_last = 0

    return all_trades


# ─── Metrics ─────────────────────────────────────────

def compute_metrics(closed, cost=False):
    if not closed:
        return {"n": 0}
    key = "profit_usd_cost" if cost else "profit_usd"
    wins = [t for t in closed if getattr(t, key) > 0]
    losses = [t for t in closed if getattr(t, key) <= 0]
    n = len(closed)
    n_wins = len(wins)
    wr = n_wins / n * 100 if n > 0 else 0
    total_pnl = sum(getattr(t, key) for t in closed)
    gross_profit = sum(max(0, getattr(t, key)) for t in closed)
    gross_loss = abs(sum(min(0, getattr(t, key)) for t in closed))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)

    peak = INITIAL_BALANCE
    dd_max = 0.0
    bal = INITIAL_BALANCE
    for t in closed:
        bal += getattr(t, key)
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
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


def avg_cost_metrics(closed):
    if not closed:
        return {}
    cost_pips = [t.cost_pips for t in closed]
    comm = [t.commission_usd for t in closed]
    cost_usd_list = [abs(t.profit_usd - t.profit_usd_cost) for t in closed]
    return {
        "avg_cost_pips": round(float(np.mean(cost_pips)), 2),
        "avg_commission_usd": round(float(np.mean(comm)), 2),
        "avg_total_cost_usd": round(float(np.mean(cost_usd_list)), 2),
        "median_cost_usd": round(float(np.median(cost_usd_list)), 2),
        "max_cost_usd": round(float(np.max(cost_usd_list)), 2),
        "total_costs_usd": round(sum(cost_usd_list), 2),
    }


# ─── Main ────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest MOM20x3 with costs")
    parser.add_argument("--tf", choices=["H1", "H4", "D1"], default="H1")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated list of symbols (default: all available)")
    args = parser.parse_args()

    data_dir = Path("data/historical")
    if not data_dir.exists():
        print(f"Data directory {data_dir} not found.")
        sys.exit(1)

    parquet_files = sorted(data_dir.glob(f"*_{args.tf}.parquet"))
    if not parquet_files:
        print(f"No {args.tf} parquet files found in {data_dir}")
        sys.exit(1)

    print(f"Found {len(parquet_files)} {args.tf} files")

    target_symbols = None
    if args.symbols:
        target_symbols = set(s.strip() for s in args.symbols.split(","))

    all_results = {}
    all_trades_dict = {}
    start_all = datetime.utcnow()

    for fpath in parquet_files:
        parts = fpath.stem.split("_")
        symbol = "_".join(parts[:-1])
        if target_symbols and symbol not in target_symbols:
            continue

        df = pd.read_parquet(fpath)
        if len(df) < MIN_BARS:
            print(f"  {symbol} ({args.tf}): SKIP ({len(df)} bars)")
            continue

        t0 = datetime.utcnow()
        trades = backtest_symbol(symbol, args.tf, df)
        elapsed = (datetime.utcnow() - t0).total_seconds()
        closed = [t for t in trades if t.closed]
        key = f"{symbol}_{args.tf}"

        if not closed:
            print(f"  {symbol}: 0 trades ({len(df)} bars, {elapsed:.1f}s)")
            all_results[key] = {"n": 0, "error": "no trades"}
            continue

        m_clean = compute_metrics(closed, cost=False)
        m_cost = compute_metrics(closed, cost=True)
        costs = avg_cost_metrics(closed)

        wr_drop = m_clean["win_rate"] - m_cost["win_rate"]
        pnl_drop = m_clean["total_pnl"] - m_cost["total_pnl"]

        print(f"  {symbol:12s} | {m_clean['n']:>5d} trades | "
              f"WR: {m_clean['win_rate']:5.1f}% -> {m_cost['win_rate']:5.1f}% "
              f"({wr_drop:+.1f}pp) | "
              f"PnL: ${m_clean['total_pnl']:>+8.2f} -> ${m_cost['total_pnl']:>+8.2f} "
              f"(${pnl_drop:+.2f}) | "
              f"PF: {m_clean['profit_factor']:.2f} -> {m_cost['profit_factor']:.2f} | "
              f"cost: {costs.get('avg_cost_pips', 0):.1f}pip ${costs.get('avg_total_cost_usd', 0):.1f} | "
              f"{elapsed:.1f}s")

        all_results[key] = {
            "clean": m_clean,
            "cost": m_cost,
            "costs": costs,
            "delta": {
                "win_rate_drop": round(wr_drop, 1),
                "pnl_drop": round(pnl_drop, 2),
                "pnl_drop_pct": round(pnl_drop / max(abs(m_clean["total_pnl"]), 1) * 100, 1),
                "pf_drop": round(m_clean["profit_factor"] - m_cost["profit_factor"], 2),
            },
        }
        all_trades_dict[key] = [t.to_dict() for t in closed]

    total_time = (datetime.utcnow() - start_all).total_seconds()

    # ─── Summary table ───────────────────────────────
    print("\n" + "=" * 95)
    print(f"  BACKTEST MOM20x3 — COST IMPACT REPORT ({args.tf})")
    print(f"  {datetime.utcnow().strftime('%d %B %Y %H:%M UTC')}")
    print("=" * 95)
    print(f"  {'Symbol':12s} {'Trades':>6s} {'WR_clean':>8s} {'WR_cost':>8s} "
          f"{'WR_drop':>8s} {'PnL_clean':>10s} {'PnL_cost':>10s} "
          f"{'PF_cl':>5s} {'PF_cost':>5s} {'Cost/pip':>8s}")
    print(f"  {'-' * 88}")

    valid = {k: v for k, v in all_results.items() if "clean" in v}
    sorted_keys = sorted(valid.keys(),
                         key=lambda k: valid[k]["clean"]["total_pnl"], reverse=True)

    total_clean_n = 0
    total_clean_pnl = 0.0
    total_cost_pnl = 0.0
    total_clean_wins = 0
    total_cost_wins = 0

    for key in sorted_keys:
        r = valid[key]
        c = r["clean"]
        co = r["cost"]
        d = r["delta"]
        costs = r["costs"]
        edge_cl = "✅" if c.get("significant") and c["win_rate"] > 50 else "❌"
        edge_co = "✅" if co.get("significant") and co["win_rate"] > 50 else "❌"
        print(f"  {key:12s} {c['n']:>6d} {c['win_rate']:>7.1f}%{edge_cl} "
              f"{co['win_rate']:>7.1f}%{edge_co} "
              f"{d['win_rate_drop']:>+7.1f}pp "
              f"${c['total_pnl']:>+9.2f} ${co['total_pnl']:>+9.2f} "
              f"{c['profit_factor']:>4.2f} {co['profit_factor']:>4.2f} "
              f"{costs.get('avg_cost_pips', 0):>6.1f}")
        total_clean_n += c["n"]
        total_clean_pnl += c["total_pnl"]
        total_cost_pnl += co["total_pnl"]
        total_clean_wins += c["wins"]
        total_cost_wins += co["wins"]

    total_clean_wr = total_clean_wins / max(total_clean_n, 1) * 100
    total_cost_wr = total_cost_wins / max(total_clean_n, 1) * 100
    total_wr_drop = total_clean_wr - total_cost_wr
    total_pnl_drop = total_clean_pnl - total_cost_pnl

    print(f"  {'-' * 88}")
    print(f"  {'TOTAL':12s} {total_clean_n:>6d} {total_clean_wr:>7.1f}% "
          f"{total_cost_wr:>7.1f}% {total_wr_drop:>+7.1f}pp "
          f"${total_clean_pnl:>+9.2f} ${total_cost_pnl:>+9.2f}")
    print(f"  {'PnL erosion':>12s} {'':6s} {'':8s} {'':8s} {'':8s} "
          f"${total_pnl_drop:>+9.2f} ({total_pnl_drop / max(abs(total_clean_pnl), 1) * 100:.1f}%)")
    print()

    # ─── Degradation ranking ─────────────────────────
    deg = [(k, valid[k]["delta"]["win_rate_drop"], valid[k]["delta"]["pnl_drop_pct"])
           for k in valid]
    deg.sort(key=lambda x: x[1], reverse=True)
    print("  Win rate degradation (clean -> cost, most affected first):")
    print(f"  {'Symbol':12s} {'WR drop':>8s} {'PnL erosion %':>14s}")
    print(f"  {'-' * 37}")
    for sym, wr_d, pnl_d in deg:
        print(f"  {sym:12s} {wr_d:>+6.1f}pp {pnl_d:>+13.1f}%")

    # ─── Worst cost symbols ──────────────────────────
    print("\n  Cost breakdown (avg per trade):")
    print(f"  {'Symbol':12s} {'Spread(pip)':>11s} {'Comm($)':>8s} {'Total($)':>9s} {'Med($)':>7s}")
    print(f"  {'-' * 50}")
    for key in sorted_keys:
        costs = valid[key]["costs"]
        print(f"  {key:12s} {costs.get('avg_cost_pips', 0):>9.1f} "
              f"${costs.get('avg_commission_usd', 0):>+6.2f} "
              f"${costs.get('avg_total_cost_usd', 0):>+7.2f} "
              f"${costs.get('median_cost_usd', 0):>+5.2f}")

    # ─── Save results ────────────────────────────────
    out_dir = Path("runtime")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Clean up numpy types for JSON
    def clean_json(obj):
        if isinstance(obj, dict):
            return {k: clean_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean_json(v) for v in obj]
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return obj

    report = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "timeframe": args.tf,
            "initial_balance": INITIAL_BALANCE,
            "risk_per_trade": RISK_PER_TRADE,
            "slippage_pips": SLIPPAGE_PIPS,
            "commission_per_100k": COMMISSION_PER_100K,
        },
        "summary": {
            "total_trades": total_clean_n,
            "total_clean_pnl": round(total_clean_pnl, 2),
            "total_cost_pnl": round(total_cost_pnl, 2),
            "total_pnl_erosion": round(total_pnl_drop, 2),
            "total_erosion_pct": round(total_pnl_drop / max(abs(total_clean_pnl), 1) * 100, 1),
            "total_clean_wr": round(total_clean_wr, 1),
            "total_cost_wr": round(total_cost_wr, 1),
            "total_wr_drop": round(total_wr_drop, 1),
        },
        "per_symbol": clean_json(valid),
    }

    report_path = out_dir / "backtest_with_costs_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved to {report_path}")

    # Save detailed trades
    trades_path = out_dir / "backtest_with_costs_trades.json"
    with open(trades_path, "w") as f:
        json.dump(clean_json(all_trades_dict), f, indent=2, default=str)
    total_trades = sum(len(v) for v in all_trades_dict.values())
    print(f"  {total_trades} trades saved to {trades_path}")
    print(f"  Completed in {total_time:.1f}s")


if __name__ == "__main__":
    main()
