"""
Backtest Signal Analysis — 3 actifs production
Analyse rapide des signaux MOM20x3 avec configuration production.
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


def analyze_symbol(sym):
    """Analyse rapide d'un symbole."""
    df = pd.read_parquet(f"data/historical/{sym}_H1.parquet")
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)

    total_bars = len(close)

    # Use last MAX_BARS
    start_idx = max(MIN_BARS, total_bars - MAX_BARS)
    close = close[start_idx:]
    high = high[start_idx:]
    low = low[start_idx:]

    sym_cfg = _get_symbol_config(sym)
    period = sym_cfg["momentum_period"]

    trades = []

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

        # Quick PnL (next 20 bars)
        exit_idx = min(i + 20, len(close) - 1)
        exit_price = close[exit_idx]

        if action == "BUY":
            pnl = (exit_price - close[i]) / close[i]
        else:
            pnl = (close[i] - exit_price) / close[i]

        trades.append({
            "action": action,
            "pnl_pct": pnl,
            "is_win": pnl > 0,
            "adx": adx_arr,
            "atr": current_atr,
            "score": 0.50 + min(1.0, mom_abs / (threshold_value * 2)) * 0.45,
        })

    return trades, total_bars


def print_stats(sym, trades, total_bars):
    """Affiche les statistiques."""
    if not trades:
        print("  Aucun trade")
        return

    wins = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]

    win_rate = len(wins) / len(trades) * 100
    avg_pnl = np.mean([t["pnl_pct"] for t in trades]) * 100
    total_pnl = sum(t["pnl_pct"] for t in trades) * 100
    avg_adx = np.mean([t["adx"] for t in trades])
    avg_atr = np.mean([t["atr"] for t in trades])
    avg_score = np.mean([t["score"] for t in trades])

    # Win/Loss avg
    avg_win = np.mean([t["pnl_pct"] for t in wins]) * 100 if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in losses]) * 100 if losses else 0

    # PF estimate
    gross_profit = sum(t["pnl_pct"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_pct"] for t in losses)) if losses else 1
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    # Direction
    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]
    buy_wr = sum(1 for t in buys if t["is_win"]) / len(buys) * 100 if buys else 0
    sell_wr = sum(1 for t in sells if t["is_win"]) / len(sells) * 100 if sells else 0

    print("  Total bars:     %d" % total_bars)
    print("  Signals:        %d" % len(trades))
    print("  Win Rate:       %.1f%%" % win_rate)
    print("  Avg PnL/trade:  %.3f%%" % avg_pnl)
    print("  Total PnL:      %.2f%%" % total_pnl)
    print("  Profit Factor:  %.2f" % pf)
    print("  Avg ADX:        %.1f" % avg_adx)
    print("  Avg ATR:        %.2f" % avg_atr)
    print("  Avg Score:      %.2f" % avg_score)
    print("  Avg Win:        +%.3f%%" % avg_win)
    print("  Avg Loss:       %.3f%%" % avg_loss)
    print("  BUY:  %d (WR %.1f%%)" % (len(buys), buy_wr))
    print("  SELL: %d (WR %.1f%%)" % (len(sells), sell_wr))


def main():
    print("=" * 70)
    print("  BACKTEST SIGNAL ANALYSIS — Configuration Production")
    print("  XAUUSD | BTCUSD | US500.cash")
    print("  Dernieres %d barres H1" % MAX_BARS)
    print("=" * 70)

    all_results = {}

    for sym in SYMBOLS:
        print("\n" + "=" * 60)
        print("  %s" % sym)
        print("=" * 60)

        start = time.time()
        trades, total_bars = analyze_symbol(sym)
        elapsed = time.time() - start

        print_stats(sym, trades, total_bars)
        print("  Analyse:        %.1fs" % elapsed)

        if trades:
            wins = sum(1 for t in trades if t["is_win"])
            all_results[sym] = {
                "trades": len(trades),
                "win_rate": wins / len(trades) * 100,
                "pnl": sum(t["pnl_pct"] for t in trades) * 100,
            }

    # Summary
    print("\n" + "=" * 70)
    print("  VERDICT PRODUCTION")
    print("=" * 70)

    ready = True
    for sym, stats in all_results.items():
        wr = stats["win_rate"]
        status = "OK" if wr > 55 else "ATTENTION"
        if status == "ATTENTION":
            ready = False
        print("\n  %s: %s" % (sym, status))
        print("    Trades: %d | WR: %.1f%% | PnL: %.2f%%" % (
            stats["trades"], stats["win_rate"], stats["pnl"]
        ))

    if ready:
        print("\n  TOUS LES ACTIFS VALIDES")
    else:
        print("\n  CERTAINS ACTIFS NECESSITENT UNE ATTENTION")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
