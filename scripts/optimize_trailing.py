#!/usr/bin/env python3
"""
Optimisation rapide du trailing — test sur EURUSD uniquement.
Compare différentes configs de trailing : WR, RR, PnL, DD.
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from engine_simple.backtest_core import BacktestEngine, BacktestConfig, DataLoader
from engine_simple.backtest_core.strategies import MOM20x3

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("optimize")
logging.getLogger("backtest_core").setLevel(logging.WARNING)
logging.getLogger("backtest_core.trade").setLevel(logging.ERROR)

CAPITAL = 200_000
SYMBOL = "EURUSD"
TIMEFRAME = "H1"
START = "2015-01-01"
END = "2025-12-31"

# ─── Différentes configs de trailing à tester ──────────────────────────

TRAILING_CONFIGS = {
    "v2 (original)": {
        "trailing_levels": {
            "RANGING": [[0.7, 0.40], [1.5, 0.25], [3.0, 0.14], [5.0, 0.07]],
            "TREND_UP": [[0.7, 0.45], [1.5, 0.28], [3.0, 0.16], [5.0, 0.08]],
            "TREND_DOWN": [[0.7, 0.45], [1.5, 0.28], [3.0, 0.16], [5.0, 0.08]],
            "HIGH_VOL": [[0.8, 0.60], [1.5, 0.40], [3.0, 0.25], [5.0, 0.12]],
            "LOW_VOL": [[0.6, 0.25], [1.5, 0.14], [3.0, 0.08], [5.0, 0.04]],
        },
        "be_buffer_atr": 0.50,
        "guard": 0.3,
    },
    "v3 (ultra-serré)": {
        "trailing_levels": {
            "RANGING": [[0.5, 0.25], [1.0, 0.15], [2.0, 0.10], [4.0, 0.05]],
            "TREND_UP": [[0.5, 0.30], [1.0, 0.20], [2.0, 0.12], [4.0, 0.06]],
            "TREND_DOWN": [[0.5, 0.30], [1.0, 0.20], [2.0, 0.12], [4.0, 0.06]],
            "HIGH_VOL": [[0.6, 0.40], [1.2, 0.28], [2.5, 0.18], [5.0, 0.10]],
            "LOW_VOL": [[0.4, 0.18], [0.8, 0.10], [1.5, 0.06], [3.0, 0.03]],
        },
        "be_buffer_atr": 0.35,
        "guard": 0.3,
    },
    "v3.5 (équilibré)": {
        "trailing_levels": {
            "RANGING": [[0.7, 0.30], [1.5, 0.18], [3.0, 0.10], [5.0, 0.05]],
            "TREND_UP": [[0.7, 0.35], [1.5, 0.22], [3.0, 0.12], [5.0, 0.06]],
            "TREND_DOWN": [[0.7, 0.35], [1.5, 0.22], [3.0, 0.12], [5.0, 0.06]],
            "HIGH_VOL": [[0.8, 0.50], [1.5, 0.35], [3.0, 0.22], [5.0, 0.12]],
            "LOW_VOL": [[0.5, 0.20], [1.0, 0.12], [2.0, 0.07], [4.0, 0.03]],
        },
        "be_buffer_atr": 0.40,
        "guard": 0.3,
    },
    "v4 (RR max)": {
        "trailing_levels": {
            "RANGING": [[1.0, 0.35], [2.0, 0.22], [3.5, 0.14], [6.0, 0.07]],
            "TREND_UP": [[1.0, 0.45], [2.0, 0.28], [3.5, 0.16], [6.0, 0.08]],
            "TREND_DOWN": [[1.0, 0.45], [2.0, 0.28], [3.5, 0.16], [6.0, 0.08]],
            "HIGH_VOL": [[1.2, 0.60], [2.5, 0.40], [4.0, 0.25], [6.0, 0.12]],
            "LOW_VOL": [[0.8, 0.25], [1.5, 0.15], [3.0, 0.08], [5.0, 0.04]],
        },
        "be_buffer_atr": 0.50,
        "guard": 0.5,
    },
    "v5 (modéré+)": {
        "trailing_levels": {
            "RANGING": [[0.6, 0.28], [1.2, 0.16], [2.5, 0.10], [4.5, 0.05]],
            "TREND_UP": [[0.6, 0.32], [1.2, 0.20], [2.5, 0.12], [4.5, 0.06]],
            "TREND_DOWN": [[0.6, 0.32], [1.2, 0.20], [2.5, 0.12], [4.5, 0.06]],
            "HIGH_VOL": [[0.7, 0.45], [1.5, 0.32], [3.0, 0.20], [5.0, 0.10]],
            "LOW_VOL": [[0.4, 0.18], [1.0, 0.10], [2.0, 0.06], [3.5, 0.03]],
        },
        "be_buffer_atr": 0.40,
        "guard": 0.3,
    },
    "v6 (RR prioritaire)": {
        "trailing_levels": {
            "RANGING": [[1.5, 0.50], [2.5, 0.30], [4.0, 0.18], [6.0, 0.08]],
            "TREND_UP": [[1.5, 0.60], [2.5, 0.35], [4.0, 0.20], [6.0, 0.10]],
            "TREND_DOWN": [[1.5, 0.60], [2.5, 0.35], [4.0, 0.20], [6.0, 0.10]],
            "HIGH_VOL": [[1.5, 0.70], [2.5, 0.45], [4.0, 0.28], [6.0, 0.15]],
            "LOW_VOL": [[1.0, 0.40], [2.0, 0.22], [3.0, 0.12], [5.0, 0.05]],
        },
        "be_buffer_atr": 0.60,
        "guard": 1.0,
    },
    "v7 (RR extrême)": {
        "trailing_levels": {
            "RANGING": [[2.0, 0.60], [3.5, 0.40], [5.0, 0.25], [8.0, 0.12]],
            "TREND_UP": [[2.0, 0.70], [3.5, 0.45], [5.0, 0.28], [8.0, 0.14]],
            "TREND_DOWN": [[2.0, 0.70], [3.5, 0.45], [5.0, 0.28], [8.0, 0.14]],
            "HIGH_VOL": [[2.0, 0.80], [3.5, 0.55], [5.0, 0.35], [8.0, 0.18]],
            "LOW_VOL": [[1.5, 0.50], [2.5, 0.30], [4.0, 0.18], [6.0, 0.08]],
        },
        "be_buffer_atr": 0.80,
        "guard": 1.5,
    },
    "v8 (premier lock large)": {
        "trailing_levels": {
            "RANGING": [[0.8, 0.40], [1.5, 0.25], [3.0, 0.14], [5.0, 0.07]],
            "TREND_UP": [[0.8, 0.45], [1.5, 0.28], [3.0, 0.16], [5.0, 0.08]],
            "TREND_DOWN": [[0.8, 0.45], [1.5, 0.28], [3.0, 0.16], [5.0, 0.08]],
            "HIGH_VOL": [[1.0, 0.60], [2.0, 0.40], [3.5, 0.25], [5.5, 0.12]],
            "LOW_VOL": [[0.6, 0.30], [1.2, 0.18], [2.0, 0.10], [4.0, 0.05]],
        },
        "be_buffer_atr": 0.50,
        "guard": 0.6,
    },
}


def run_test(name: str, config: dict):
    """Run EURUSD backtest with given trailing config, return metrics."""
    dl = DataLoader()
    data = dl.load(symbol=SYMBOL, timeframe=TIMEFRAME, start=START, end=END)
    if data is None or data.empty:
        return None
    if hasattr(dl, "clean"):
        data = dl.clean(data, symbol=SYMBOL)
    if hasattr(dl, "add_indicators"):
        data = dl.add_indicators(data)

    # Trailing levels sont passés via BacktestConfig → engine → SimTrade
    strategy = MOM20x3()
    engine = BacktestEngine(
        BacktestConfig(
            initial_balance=CAPITAL,
            risk_per_trade=0.0066,
            trailing_levels=config["trailing_levels"],
            be_buffer_atr=config["be_buffer_atr"],
        )
    )

    t0 = time.time()
    result = engine.run(symbol=SYMBOL, data=data, timeframe=TIMEFRAME, strategy=strategy)
    elapsed = time.time() - t0

    if not result or not hasattr(result, "trades"):
        return None

    trades = [t for t in result.trades if t.closed]
    if not trades:
        return None

    pnls = [getattr(t, "profit_usd_cost", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / len(trades) * 100
    total_pnl = sum(pnls)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 1
    rr = avg_win / avg_loss if avg_loss > 0 else 0

    # Max DD (from trades)
    running = CAPITAL
    peak = CAPITAL
    max_dd = 0
    for t in trades:
        pnl = getattr(t, "profit_usd_cost", 0)
        running += pnl
        if running > peak:
            peak = running
        dd = (peak - running) / CAPITAL * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "name": name,
        "trades": len(trades),
        "wr": wr,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr": rr,
        "max_dd": max_dd,
        "elapsed": elapsed,
    }


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n  Optimisation trailing sur {SYMBOL} {TIMEFRAME}")
    print(f"  Risk: 0.66% | Capital: ${CAPITAL:,}")
    print(
        "\n  {:<20} | {:>7} | {:>6} | {:>10} | {:>8} | {:>8} | {:>5} | {:>6} | {:>6}".format(
            "Config", "Trades", "WR", "PnL", "Win $", "Loss $", "RR", "DD", "Temps"
        )
    )
    print(
        f"  {'-' * 20}-+-{'-' * 7}-+-{'-' * 6}-+-{'-' * 10}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 6}"
    )

    results = []
    for name, config in TRAILING_CONFIGS.items():
        result = run_test(name, config)
        if result:
            results.append(result)
            print(
                f"  {result['name']:<20} | {result['trades']:>7} | {result['wr']:>5.1f}% | ${result['total_pnl']:>+8,.0f} | ${result['avg_win']:>6.0f} | ${result['avg_loss']:>6.0f} | {result['rr']:>4.2f} | {result['max_dd']:>5.2f}% | {result['elapsed']:>4.0f}s"
            )

    if results:
        print(f"\n  {'=' * 90}")
        print(f"  Meilleur RR: {max(results, key=lambda r: r['rr'])['name']} (RR {max(r['rr'] for r in results):.2f})")
        print(f"  Meilleur WR: {max(results, key=lambda r: r['wr'])['name']} (WR {max(r['wr'] for r in results):.1f}%)")
        print(
            f"  Meilleur équilibre WR×RR: {max(results, key=lambda r: r['wr'] * r['rr'])['name']} ({max(r['wr'] * r['rr'] for r in results):.0f})"
        )
        print(
            f"  Meilleur PnL: {max(results, key=lambda r: r['total_pnl'])['name']} (${max(r['total_pnl'] for r in results):+,.0f})"
        )

        # Best balanced: WR × RR (product of win rate and risk-reward)
        best = max(results, key=lambda r: r["wr"] * r["rr"])
        print(f"\n  => Config recommandée: {best['name']}")
        print(
            f"     WR={best['wr']:.1f}%, RR={best['rr']:.2f}, PnL=${best['total_pnl']:+,.0f}, DD={best['max_dd']:.2f}%"
        )
