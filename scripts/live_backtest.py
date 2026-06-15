"""
Backtest sur données réelles MT5 — simule le pipeline complet :
  Signal → FTMO → Trade (SL/TP ATR avec trailing) → Résultat

Usage: python scripts/live_backtest.py
"""
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "WARNING"

import numpy as np

import config_simple as cfg
from engine_simple.ftmo_protector import BE_BUFFER_BY_REGIME, TRAILING_BY_REGIME
from engine_simple.mt5_connector import MT5Connector
from engine_simple.signals import STRATS, SignalGenerator

N_SYMBOLS = len(cfg.SYMBOLS)
MAX_BARS = 9999  # max disponible sur MT5
MIN_BARS = 200   # besoin de 200 bars pour calculer les indicateurs

def fetch_rates(conn, symbol, n_bars=MAX_BARS):
    rates = conn.get_rates(symbol, "H1", n_bars)
    if rates is None or len(rates) < MIN_BARS:
        return None
    return rates

class SimTrade:
    def __init__(self, symbol, action, entry, sl, tp, atr_val, regime, bar_idx, lot=0.1):
        self.symbol = symbol
        self.action = action
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.atr_val = atr_val
        self.regime = regime
        self.open_bar = bar_idx
        self.lot = lot
        self.direction = 0 if action == "BUY" else 1
        self.closed = False
        self.result = None
        self.profit_pct = 0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.best_profit_atr = 0
        self.peak_profit_atr = 0

    def check_sl_tp(self, high, low, close, bar_idx):
        if self.closed:
            return
        if self.direction == 0:  # BUY
            if low <= self.trailing_sl:
                self.closed = True
                exit_price = self.trailing_sl
                self.profit_pct = (exit_price - self.entry) / (self.entry or 1)
                self.result = "SL"
                return
            if high >= self.tp:
                self.closed = True
                exit_price = self.tp
                self.profit_pct = (exit_price - self.entry) / (self.entry or 1)
                self.result = "TP"
                return
            # Track peak for trailing
            if close > self.peak_price:
                self.peak_price = close
            if high > self.peak_price:
                self.peak_price = high
        else:  # SELL
            if high >= self.trailing_sl:
                self.closed = True
                exit_price = self.trailing_sl
                self.profit_pct = (self.entry - exit_price) / (self.entry or 1)
                self.result = "SL"
                return
            if low <= self.tp:
                self.closed = True
                exit_price = self.tp
                self.profit_pct = (self.entry - exit_price) / (self.entry or 1)
                self.result = "TP"
                return
            if close < self.peak_price:
                self.peak_price = close
            if low < self.peak_price:
                self.peak_price = low

    def update_trailing(self, high, low, close, atr_val):
        if self.closed or atr_val is None or atr_val <= 0:
            return
        if self.direction == 0:  # BUY
            profit_atr = (self.peak_price - self.entry) / atr_val
        else:
            profit_atr = (self.entry - self.peak_price) / atr_val
        self.best_profit_atr = max(self.best_profit_atr, profit_atr)
        levels = TRAILING_BY_REGIME.get(self.regime, TRAILING_BY_REGIME["RANGING"])
        if not levels or profit_atr <= levels[0][0]:
            return
        trail_dist = levels[-1][1]
        for thresh, dist in reversed(levels):
            if profit_atr > thresh:
                trail_dist = dist
                break
        trail_distance = trail_dist * atr_val
        if self.direction == 0:
            new_sl = self.peak_price - trail_distance
            if new_sl > self.trailing_sl:
                new_sl = self.trailing_sl if new_sl > self.trailing_sl else new_sl
            if new_sl <= self.trailing_sl:
                return
            self.trailing_sl = new_sl
        else:
            new_sl = self.peak_price + trail_distance
            if new_sl < self.trailing_sl:
                return
            self.trailing_sl = new_sl

    def check_partial_tp(self, high, low, atr_val):
        if self.closed or self.partial_closed:
            return
        if self.direction == 0:
            if low > self.entry:
                progress = (low - self.entry) / max(self.tp - self.entry, 0.00001)
            else:
                return
        else:
            if high < self.entry:
                progress = (self.entry - high) / max(self.entry - self.tp, 0.00001)
            else:
                return
        if progress < 0.60:
            return
        self.partial_closed = True
        # Set BE
        if atr_val and atr_val > 0:
            be_mult = BE_BUFFER_BY_REGIME.get(self.regime, 0.50)
            be_buffer = be_mult * atr_val
        else:
            be_buffer = 0.001
        if self.direction == 0:
            be_sl = self.entry + be_buffer
            if be_sl > self.trailing_sl:
                self.trailing_sl = be_sl
        else:
            be_sl = self.entry - be_buffer
            if be_sl < self.trailing_sl:
                self.trailing_sl = be_sl

    def to_dict(self):
        return {
            "symbol": self.symbol, "action": self.action, "regime": self.regime,
            "entry": round(self.entry, 5), "sl": round(self.sl, 5),
            "tp": round(self.tp, 5), "result": self.result,
            "profit_pct": round(self.profit_pct * 100, 2),
            "bars_held": "?", "partial_tp": self.partial_closed,
        }


def atr_value(hh, ll, cc, period=14):
    tr = np.maximum(hh[1:] - ll[1:], np.maximum(np.abs(hh[1:] - cc[:-1]), np.abs(ll[1:] - cc[:-1])))
    if len(tr) < period:
        return 0
    return float(np.mean(tr[-period:]))


def run_backtest():
    conn = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
    if not conn.connect():
        print("MT5 connection failed")
        return
    SignalGenerator(conn)
    signals_available = Path("runtime/last_rates").exists()
    if not signals_available:
        Path("runtime/last_rates").mkdir(parents=True, exist_ok=True)
    print(f"Symbols: {cfg.SYMBOLS}")
    print(f"Config: MAX_POSITIONS={cfg.MAX_POSITIONS}, MAX_TRADES_DAY={cfg.MAX_TRADES_PER_DAY}")
    print(f"        MIN_SCORE={cfg.MIN_SIGNAL_SCORE}, RR_MIN={cfg.MIN_RR_RATIO}")
    print(f"        SL RANGING={2.0}×ATR, TP RANGING={4.5}×ATR")
    print(f"        SL TREND={2.0}×ATR, TP TREND={5.0}×ATR")
    print()

    all_trades = []
    symbol_trades = defaultdict(list)
    hourly_balance = 200000.0
    balance_curve = [(0, hourly_balance)]
    peak_balance = hourly_balance
    max_dd = 0
    daily_trades = {}
    consistency_max_day = 0

    # Walk bars (skip first MIN_BARS for indicator warmup)
    for start_bar in range(MIN_BARS, MAX_BARS - 5):
        for symbol in cfg.SYMBOLS:
            rates = fetch_rates(conn, symbol, MAX_BARS)
            if rates is None:
                continue
            n = len(rates)
            if start_bar >= n:
                continue
            bar_idx = start_bar
            current_close = float(rates[bar_idx][4])
            current_high = float(rates[bar_idx][2])
            current_low = float(rates[bar_idx][3])
            bar_time = rates[bar_idx][0] if hasattr(rates[bar_idx], '__getitem__') else 0
            if hasattr(bar_time, 'timestamp') or isinstance(bar_time, (int, float)):
                bar_dt = datetime.fromtimestamp(bar_time)
            else:
                bar_dt = datetime.utcnow()
            day_key = bar_dt.date()

            # Check if we already have a trade open for this symbol
            existing_open = [t for t in all_trades if not t.closed and t.symbol == symbol]
            if existing_open:
                for t in existing_open:
                    hh_slice = np.array([float(r[2]) for r in rates[max(0, bar_idx-5):bar_idx+1]], dtype=float)
                    ll_slice = np.array([float(r[3]) for r in rates[max(0, bar_idx-5):bar_idx+1]], dtype=float)
                    cc_slice = np.array([float(r[4]) for r in rates[max(0, bar_idx-5):bar_idx+1]], dtype=float)
                    atr_v = atr_value(hh_slice, ll_slice, cc_slice)
                    t.check_partial_tp(current_high, current_low, atr_v)
                    t.check_sl_tp(current_high, current_low, current_close, bar_idx)
                    t.update_trailing(current_high, current_low, current_close, atr_v)
                    if t.closed:
                        pnl = t.profit_pct * hourly_balance
                        hourly_balance += pnl
                        if hourly_balance > peak_balance:
                            peak_balance = hourly_balance
                        dd = (peak_balance - hourly_balance) / peak_balance * 100
                        max_dd = max(max_dd, dd)
                        day_pnl = daily_trades.get(day_key, 0)
                        daily_trades[day_key] = day_pnl + pnl
                        total_pnl = hourly_balance - 200000
                        if total_pnl > 0 and day_pnl + pnl > 0:
                            day_pct = (day_pnl + pnl) / total_pnl * 100
                            consistency_max_day = max(consistency_max_day, day_pct)
            continue  # one symbol per cycle

    # Second pass: generate signals and simulate
    for start_bar in range(MIN_BARS, MAX_BARS - 5):
        for symbol in cfg.SYMBOLS:
            rates = fetch_rates(conn, symbol, MAX_BARS)
            if rates is None:
                continue
            n = len(rates)
            if start_bar >= n:
                continue
            bar_idx = start_bar
            current_close = float(rates[bar_idx][4])
            current_high = float(rates[bar_idx][2])
            current_low = float(rates[bar_idx][3])
            bar_time = rates[bar_idx][0] if hasattr(rates[bar_idx], '__getitem__') else 0
            if hasattr(bar_time, 'timestamp') or isinstance(bar_time, (int, float)):
                bar_dt = datetime.fromtimestamp(bar_time)
            else:
                bar_dt = datetime.utcnow()
            day_key = bar_dt.date()

            # Update existing trades
            existing_open = [t for t in all_trades if not t.closed and t.symbol == symbol]
            for t in existing_open:
                hh_slice = np.array([float(r[2]) for r in rates[max(0, bar_idx-5):bar_idx+1]], dtype=float)
                ll_slice = np.array([float(r[3]) for r in rates[max(0, bar_idx-5):bar_idx+1]], dtype=float)
                cc_slice = np.array([float(r[4]) for r in rates[max(0, bar_idx-5):bar_idx+1]], dtype=float)
                atr_v = atr_value(hh_slice, ll_slice, cc_slice)
                t.check_partial_tp(current_high, current_low, atr_v)
                t.check_sl_tp(current_high, current_low, current_close, bar_idx)
                t.update_trailing(current_high, current_low, current_close, atr_v)
                if t.closed:
                    pnl = t.profit_pct * hourly_balance
                    hourly_balance += pnl
                    balance_curve.append((bar_idx, hourly_balance))
                    if hourly_balance > peak_balance:
                        peak_balance = hourly_balance
                    dd = (peak_balance - hourly_balance) / peak_balance * 100
                    max_dd = max(max_dd, dd)
                    day_pnl = daily_trades.get(day_key, 0)
                    daily_trades[day_key] = day_pnl + pnl
                    total_pnl = hourly_balance - 200000
                    if total_pnl > 0:
                        day_pct = abs(day_pnl + pnl) / abs(total_pnl) * 100
                        consistency_max_day = max(consistency_max_day, day_pct)
                    t.bars_held = bar_idx - t.open_bar
                    symbol_trades[symbol].append(t)
            len([t for t in all_trades if not t.closed])

            # Daily trade limit
            trades_today = len([t for t in symbol_trades[symbol] if t.closed and
                               t.open_bar >= start_bar - 24])

            if trades_today >= cfg.MAX_TRADES_PER_DAY:
                continue
            symbol_trades_today = len([t for t in symbol_trades[symbol] if
                                       t.closed and (isinstance(t.open_bar, (int, float)) and
                                       t.open_bar >= start_bar - 24)])
            if symbol_trades_today >= 2:
                continue

            # Generate signal
            try:
                rates_dict = {}
                for s_cfg in STRATS.get(symbol, []):
                    tf = s_cfg["tf"]
                    tf_rates = fetch_rates(conn, symbol, max(300, s_cfg["period"] + 200))
                    if tf_rates is not None and len(tf_rates) >= max(s_cfg["period"], 30):
                        rates_dict[tf] = tf_rates
                if not rates_dict:
                    continue
                h1 = rates_dict.get("H1")
                if h1 is None or len(h1) < 50:
                    continue
                hh = np.array([r[2] for r in h1], dtype=float)
                ll = np.array([r[3] for r in h1], dtype=float)
                cc = np.array([r[4] for r in h1], dtype=float)
                from engine_simple.indicators import adx as calc_adx
                adx_val = calc_adx(hh, ll, cc)[0]
                if adx_val is None or np.isnan(adx_val):
                    continue
                is_ranging = adx_val < 20
                base_thresh = 2.5 if adx_val >= 20 else 2.0
                sym_thresh_mult = {"EURUSD": 1.25}.get(symbol, 1.0)
                base_thresh *= sym_thresh_mult
                base_thresh = max(1.2, min(3.0, base_thresh))
                atr_v = atr_value(hh, ll, cc)
                if atr_v <= 0:
                    continue
                cur_price = float(cc[-1])
                # Simplified MOM20x3 check (c[i] - c[i-20]) / ATR
                if len(cc) < 21:
                    continue
                mom = (float(cc[-1]) - float(cc[-21])) / atr_v
                if abs(mom) <= base_thresh * (1 + 0.1 * (adx_val / 25)):
                    continue
                action = "BUY" if mom > 0 else "SELL"
                if is_ranging:
                    sl_atr, tp_atr = 2.0, 4.5
                elif adx_val >= 25:
                    sl_atr, tp_atr = 2.0, 5.0
                else:
                    sl_atr, tp_atr = 2.0, 4.5
                if adx_val < 18:
                    continue
                regime = ("RANGING" if is_ranging else
                          "HIGH_VOL" if atr_v / cur_price * 100 > 0.8 else
                          "LOW_VOL" if atr_v / cur_price * 100 < 0.2 else
                          "TREND_UP" if action == "BUY" else "TREND_DOWN")
                entry = current_close
                direction = 0 if action == "BUY" else 1
                sl_dist = max(sl_atr * atr_v, 1.5 * atr_v)
                tp_dist = max(tp_atr * atr_v, 1.5 * atr_v)
                if direction == 0:
                    sl_price = entry - sl_dist
                    tp_price = entry + tp_dist
                else:
                    sl_price = entry + sl_dist
                    tp_price = entry - tp_dist
                rr = tp_dist / sl_dist if sl_dist > 0 else 0
                if rr < cfg.MIN_RR_RATIO:
                    continue
                trade = SimTrade(symbol, action, entry, sl_price, tp_price, atr_v, regime, bar_idx)
                all_trades.append(trade)
            except Exception:
                continue

    conn.disconnect()

    # Results
    completed = [t for t in all_trades if t.closed]
    wins = [t for t in completed if t.profit_pct > 0]
    losses = [t for t in completed if t.profit_pct <= 0]
    total_pnl = hourly_balance - 200000
    print("=" * 60)
    print("RESULTATS BACKTEST")
    print("=" * 60)
    print(f"  Trades: {len(completed)} ({len(wins)} wins / {len(losses)} loss)")
    print(f"  Win Rate: {len(wins)/max(len(completed),1)*100:.1f}%")
    if wins:
        avg_win = np.mean([t.profit_pct for t in wins]) * 100
        print(f"  Avg Win: {avg_win:.2f}%")
    if losses:
        avg_loss = np.mean([t.profit_pct for t in losses]) * 100
        print(f"  Avg Loss: {avg_loss:.2f}%")
    avg_rr = 0
    if wins and losses:
        avg_rr = abs(np.mean([t.profit_pct for t in wins]) / np.mean([t.profit_pct for t in losses])) if np.mean([t.profit_pct for t in losses]) != 0 else 0
        print(f"  Realized RR: {avg_rr:.2f}")
    print(f"  Total PnL: ${total_pnl:+.0f} ({total_pnl/200000*100:+.2f}%)")
    print(f"  Max DD: {max_dd:.1f}%")
    if completed:
        partials = [t for t in completed if t.partial_closed]
        print(f"  Partial TP: {len(partials)}")
    if daily_trades:
        max_day_pnl = max(daily_trades.values())
        print(f"  Best day: ${max_day_pnl:+.0f}")
        worst_day = min(daily_trades.values())
        print(f"  Worst day: ${worst_day:+.0f}")
        print(f"  Consistency max: {consistency_max_day:.1f}% (limit: 30%)")
    if wins and losses:
        expectancy = (len(wins)/len(completed) * avg_rr) - (len(losses)/len(completed))
        print(f"  Expectancy: {expectancy:.3f}")
    print()
    by_symbol = defaultdict(list)
    for t in completed:
        by_symbol[t.symbol].append(t)
    for sym, trades in sorted(by_symbol.items()):
        sym_w = [t for t in trades if t.profit_pct > 0]
        sym_pnl = sum(t.profit_pct for t in trades) * 200000
        print(f"  {sym}: {len(trades)} trades, {len(sym_w)/len(trades)*100:.0f}% WR, ${sym_pnl:+.0f}")
    print()

    # Trailing validation
    trailing_count = sum(1 for t in completed if t.trailing_sl != t.sl and t.result == "TP")
    sl_moved = sum(1 for t in completed if t.trailing_sl != t.sl)
    print(f"  [TRAILING] SL moved on {sl_moved} trades, {trailing_count} hit TP after trailing")
    print()
    print("=" * 60)
    print("FTMO ASSESSMENT: target=+$20,000 (10%), DD limit=10%, daily loss=2%")
    if total_pnl >= 20000:
        print("  PROFIT TARGET: ATTEINT ✓")
    else:
        needed = 20000 - total_pnl
        print(f"  Profit target: ${total_pnl:+.0f} / $20,000 (${needed:+.0f} needed)")
    if max_dd <= 10:
        print(f"  Max DD: {max_dd:.1f}% — OK (limit 10%)")
    else:
        print(f"  Max DD: {max_dd:.1f}% — FAIL > 10%")
    print("=" * 60)
    return completed


if __name__ == "__main__":
    print(f"MT5 FTMO Backtest — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Config: {cfg.SYMBOLS}")
    run_backtest()
