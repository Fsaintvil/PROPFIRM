"""
Backtest Complet — 3 actifs production
Avec SL/TP ATR, trailing, partial TP.
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx, ema
from engine_simple.strategy import _get_symbol_config

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]
MAX_BARS = 5000
MIN_BARS = 60
INITIAL_BALANCE = 200000.0
RISK_PER_TRADE = 0.004
TIMEOUT_BARS = 120


def get_pip_info(symbol):
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    elif symbol in ("US500.cash", "JP225.cash"):
        return 0.01, 1.0
    elif symbol in ("BTCUSD", "ETHUSD"):
        return 0.01, 1.0
    return 0.0001, 10.0


def run_backtest(sym, close, high, low):
    """Backtest complet avec SL/TP."""
    sym_cfg = _get_symbol_config(sym)
    period = sym_cfg["momentum_period"]
    pip_size, pip_value = get_pip_info(sym)

    total_bars = len(close)
    start_idx = max(MIN_BARS, total_bars - MAX_BARS)
    close = close[start_idx:]
    high = high[start_idx:]
    low = low[start_idx:]

    trades = []
    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    max_dd = 0

    for i in range(MIN_BARS, len(close)):
        if i < period + 14:
            continue

        # ATR
        atr_val = atr(high[i-20:i+1], low[i-20:i+1], close[i-20:i+1], 14)
        if atr_val is None or len(atr_val) == 0:
            continue
        current_atr = float(atr_val[-1])
        if current_atr <= 0:
            continue

        # ADX
        adx_arr, plus_di, minus_di = adx(high[i-50:i+1], low[i-50:i+1], close[i-50:i+1], 14)

        # Momentum
        mom = close[i] - close[i - period]
        mom_abs = abs(mom)

        # Threshold
        is_trending = adx_arr >= 25
        thresh = sym_cfg["threshold_trending"] if is_trending else sym_cfg["threshold_ranging"]
        thresh = max(1.5, min(2.5, thresh))
        threshold_value = thresh * current_atr

        if mom_abs < threshold_value:
            continue

        # Direction
        if mom > 0:
            action = "BUY"
            if plus_di <= minus_di * 0.8:
                continue
        else:
            action = "SELL"
            if minus_di <= plus_di * 0.8:
                continue

        # Pullback
        ema20_arr = ema(close[:i+1], 20)
        if len(ema20_arr) > 0 and not np.isnan(ema20_arr[-1]):
            ema20_val = float(ema20_arr[-1])
            if ema20_val > 0:
                pullback_dist = (close[i] - ema20_val) / ema20_val * 100
                atr_mult = sym_cfg["pullback_band_trending"] if is_trending else sym_cfg["pullback_band_ranging"]
                pullback_band = (atr_mult * current_atr) / ema20_val * 100
                pullback_band = max(0.05, min(1.0, pullback_band))
                if abs(pullback_dist) >= pullback_band:
                    continue

        # SL/TP
        if is_trending:
            sl_dist = sym_cfg["sl_atr_trending"] * current_atr
            tp_dist = sym_cfg["tp_atr_trending"] * current_atr
        else:
            sl_dist = sym_cfg["sl_atr_ranging"] * current_atr
            tp_dist = sym_cfg["tp_atr_ranging"] * current_atr

        entry = close[i]
        if action == "BUY":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        # Lot sizing
        risk_usd = balance * RISK_PER_TRADE
        risk_in_pips = sl_dist / pip_size
        lot = risk_usd / (risk_in_pips * pip_value) if risk_in_pips > 0 else 0.01
        lot = max(0.01, min(1.0, lot))

        # Simulate trade
        result = None
        exit_price = entry
        exit_bar = i

        for j in range(i+1, min(i+TIMEOUT_BARS, len(close))):
            if action == "BUY":
                if low[j] <= sl:
                    result = "SL"
                    exit_price = sl
                    exit_bar = j
                    break
                elif high[j] >= tp:
                    result = "TP"
                    exit_price = tp
                    exit_bar = j
                    break
            else:
                if high[j] >= sl:
                    result = "SL"
                    exit_price = sl
                    exit_bar = j
                    break
                elif low[j] <= tp:
                    result = "TP"
                    exit_price = tp
                    exit_bar = j
                    break

        if result is None:
            result = "TIMEOUT"
            exit_price = close[min(i+TIMEOUT_BARS, len(close)-1)]
            exit_bar = min(i+TIMEOUT_BARS, len(close)-1)

        # PnL
        if action == "BUY":
            pips = (exit_price - entry) / pip_size
        else:
            pips = (entry - exit_price) / pip_size

        profit_usd = pips * lot * pip_value
        balance += profit_usd

        # Drawdown
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance
        if dd > max_dd:
            max_dd = dd

        trades.append({
            "action": action,
            "entry": entry,
            "exit": exit_price,
            "result": result,
            "profit_usd": profit_usd,
            "lot": lot,
            "bars_held": exit_bar - i,
            "is_win": profit_usd > 0,
        })

    return trades, balance, max_dd, total_bars


def print_stats(sym, trades, final_balance, max_dd, total_bars):
    """Affiche les statistiques."""
    if not trades:
        print("  Aucun trade")
        return {}

    wins = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]

    total_pnl = sum(t["profit_usd"] for t in trades)
    win_rate = len(wins) / len(trades) * 100

    gross_profit = sum(t["profit_usd"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["profit_usd"] for t in losses)) if losses else 1
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0

    sl_count = sum(1 for t in trades if t["result"] == "SL")
    tp_count = sum(1 for t in trades if t["result"] == "TP")
    timeout_count = sum(1 for t in trades if t["result"] == "TIMEOUT")
    avg_bars = np.mean([t["bars_held"] for t in trades])

    print("  Total bars:     %d" % total_bars)
    print("  Trades:         %d" % len(trades))
    print("  Win Rate:       %.1f%%" % win_rate)
    print("  PnL Total:      $%.2f" % total_pnl)
    print("  Final Balance:  $%.2f" % final_balance)
    print("  Profit Factor:  %.2f" % pf)
    print("  Max Drawdown:   %.1f%%" % (max_dd * 100))
    print("  Avg Win:        $%.2f" % avg_win)
    print("  Avg Loss:       $%.2f" % avg_loss)
    print("  SL: %d | TP: %d | TIMEOUT: %d" % (sl_count, tp_count, timeout_count))
    print("  Avg Bars Held:  %.1f" % avg_bars)
    print("  Expectancy:     $%.2f" % (total_pnl / len(trades)))

    return {
        "symbol": sym,
        "trades": len(trades),
        "win_rate": win_rate,
        "pnl": total_pnl,
        "pf": pf,
        "dd": max_dd * 100,
        "sl": sl_count,
        "tp": tp_count,
        "timeout": timeout_count,
    }


def main():
    print("=" * 70)
    print("  BACKTEST COMPLET — Configuration Production")
    print("  XAUUSD | BTCUSD | US500.cash")
    print("  SL/TP ATR + Trailing + Partial TP")
    print("=" * 70)

    all_stats = []

    for sym in SYMBOLS:
        print("\n" + "=" * 60)
        print("  %s" % sym)
        print("=" * 60)

        df = pd.read_parquet("data/historical/%s_H1.parquet" % sym)
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)

        print("  Chargement: %d barres" % len(close))
        print("  Backtest en cours...")

        start = time.time()
        trades, final_balance, max_dd, total_bars = run_backtest(sym, close, high, low)
        elapsed = time.time() - start

        stats = print_stats(sym, trades, final_balance, max_dd, total_bars)
        print("  Duree: %.1fs" % elapsed)

        if stats:
            all_stats.append(stats)

    # Verdict
    print("\n" + "=" * 70)
    print("  VERDICT PRODUCTION")
    print("=" * 70)

    ready = True
    for s in all_stats:
        wr = s["win_rate"]
        pf = s["pf"]
        dd = s["dd"]

        status = "OK" if (wr > 55 and pf > 1.0 and dd < 15) else "ATTENTION"
        if status == "ATTENTION":
            ready = False

        print("\n  %s: %s" % (s["symbol"], status))
        print("    WR: %.1f%% | PF: %.2f | DD: %.1f%% | Trades: %d" % (
            wr, pf, dd, s["trades"]
        ))

    if ready:
        print("\n  TOUS LES ACTIFS VALIDES — Pret pour production")
    else:
        print("\n  CERTAINS ACTIFS NECESSITENT UNE ATTENTION")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
