#!/usr/bin/env python3
"""
Backtest complet 6 symboles (100K barres H1) + FTMO Portfolio.
Sauvegarde progressive des résultats.
"""

import logging
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from engine_simple.backtest_core import (
    BacktestEngine,
    BacktestConfig,
    DataLoader,
    FTMOPortfolioSimulator,
    FTMOConfig,
    FTMOVerdict,
)
from engine_simple.backtest_core.strategies import MOM20x3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bt_full_portfolio")
logging.getLogger("backtest_core").setLevel(logging.WARNING)
logging.getLogger("backtest_core.trade").setLevel(logging.WARNING)

# ─── Configuration ───────────────────────────────────────────────────────

SYMBOLS = ["GBPJPY", "XAUUSD", "BTCUSD"]
TIMEFRAME = "H1"
START_DATE = "2015-01-01"
END_DATE = "2025-12-31"
CAPITAL = 200_000
RISK_PER_TRADE = 0.0042  # 0.42% (PASS FTMO confirmé 2015-2025)
OUTPUT_DIR = Path("backtest/results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Backtest config
BT_CONFIG = BacktestConfig(
    initial_balance=CAPITAL,
    risk_per_trade=RISK_PER_TRADE,
    max_positions=3,
    max_positions_per_symbol=1,
    min_bars_between_trades=5,
    min_bars_warmup=80,
    timeout_bars={"H1": 120, "H4": 60, "D1": 30},
    latency_ms=100,
    requote_prob=0.02,
    enable_partial_fill=True,
    costs_config={"mode": "realistic"},
    trailing_levels={
        "RANGING": [[1.5, 0.50], [2.5, 0.30], [4.0, 0.18], [6.0, 0.08]],
        "TREND_UP": [[1.5, 0.60], [2.5, 0.35], [4.0, 0.20], [6.0, 0.10]],
        "TREND_DOWN": [[1.5, 0.60], [2.5, 0.35], [4.0, 0.20], [6.0, 0.10]],
        "HIGH_VOL": [[1.5, 0.70], [2.5, 0.45], [4.0, 0.28], [6.0, 0.15]],
        "LOW_VOL": [[1.0, 0.40], [2.0, 0.22], [3.0, 0.12], [5.0, 0.05]],
    },
    be_buffer_atr=0.60,
)


def run_symbol(symbol: str) -> dict | None:
    """Run backtest for a single symbol, return result dict."""
    logger.info(f"{'=' * 60}")
    logger.info(f"BACKTEST {symbol} {TIMEFRAME}")
    logger.info(f"{'=' * 60}")

    dl = DataLoader()
    data = dl.load(symbol=symbol, timeframe=TIMEFRAME, start=START_DATE, end=END_DATE)
    if data is None or (isinstance(data, pd.DataFrame) and data.empty):
        logger.warning(f"Pas de données pour {symbol}")
        return None

    logger.info(f"  Barres: {len(data)}")

    # Clean data
    if hasattr(dl, "clean"):
        data = dl.clean(data, symbol=symbol)

    # Add indicators
    if hasattr(dl, "add_indicators"):
        data = dl.add_indicators(data)

    # Run backtest
    strategy = MOM20x3()
    engine = BacktestEngine(BT_CONFIG)

    t0 = time.time()
    result = engine.run(symbol=symbol, data=data, timeframe=TIMEFRAME, strategy=strategy)
    elapsed = time.time() - t0

    if result and hasattr(result, "trades"):
        trades = [t for t in result.trades if hasattr(t, "closed") and t.closed]
        gross_profit = sum(getattr(t, "profit_usd_cost", 0) for t in trades)
        wins = sum(1 for t in trades if getattr(t, "profit_usd_cost", 0) > 0)
        logger.info(
            f"  ✅ {symbol}: {len(trades)} trades, "
            f"WR {wins / len(trades) * 100:.1f}%, "
            f"PnL ${gross_profit:+.2f}, "
            f"{elapsed:.0f}s"
        )

        # Save trades to pickle
        result_path = OUTPUT_DIR / f"bt_result_{symbol}.pkl"
        with open(result_path, "wb") as f:
            pickle.dump(result, f)
        logger.info(f"  Sauvegardé: {result_path}")

        # Save trades to CSV
        csv_path = OUTPUT_DIR / f"trades_{symbol}_{TIMEFRAME}_mom20x3_full.csv"
        rows = []
        for t in trades:
            rows.append(
                {
                    "symbol": t.symbol,
                    "action": t.action,
                    "entry": round(t.entry, 5),
                    "exit": round(t.close_price, 5),
                    "sl": round(t.sl, 5),
                    "tp": round(t.tp, 5),
                    "pnl_usd": round(t.profit_usd, 2),
                    "pnl_cost": round(t.profit_usd_cost, 2),
                    "lot": round(t.lot, 4),
                    "regime": t.regime,
                    "result": t.result,
                    "bars_held": t.bars_held,
                    "open_time": str(t.open_time) if t.open_time else "",
                    "close_time": str(t.close_time) if t.close_time else "",
                }
            )
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        logger.info(f"  CSV: {csv_path}")

        return {
            "symbol": symbol,
            "result": result,
            "trades": trades,
            "n_trades": len(trades),
            "win_rate": wins / len(trades) * 100,
            "pnl": gross_profit,
            "elapsed": elapsed,
        }
    else:
        logger.warning(f"  ❌ {symbol}: pas de résultat")
        return None


def evaluate_portfolio(symbol_results: dict) -> FTMOVerdict:
    """Evaluate FTMO portfolio from symbol results."""
    logger.info(f"\n{'=' * 60}")
    logger.info("SIMULATION FTMO PORTFOLIO")
    logger.info(f"{'=' * 60}")

    # Reconstruct: FTMOPortfolioSimulator needs BacktestResult objects with .trades
    ftmo_portfolio = FTMOPortfolioSimulator(
        config=FTMOConfig(account_size=CAPITAL),
        verbose=True,
    )

    # Build the dict {symbol: BacktestResult}
    bt_results = {}
    for sym, data in symbol_results.items():
        if data and data.get("result"):
            bt_results[sym] = data["result"]

    if not bt_results:
        logger.error("Aucun résultat pour le portfolio")
        return None

    verdict = ftmo_portfolio.evaluate_portfolio(
        symbol_results=bt_results,
        balance=CAPITAL,
    )

    print("\n" + FTMOPortfolioSimulator.portfolio_summary(verdict, bt_results))

    # Save verdict
    verdict_path = OUTPUT_DIR / "ftmo_portfolio_verdict.pkl"
    with open(verdict_path, "wb") as f:
        pickle.dump(verdict, f)
    logger.info(f"Verdict sauvegardé: {verdict_path}")

    return verdict


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backtest full portfolio")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol to run")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate FTMO from saved pickles")
    args = parser.parse_args()

    PROGRESS_FILE = OUTPUT_DIR / "bt_progress.json"

    if args.evaluate:
        # Load saved pickles and evaluate
        all_results = {}
        for sym in SYMBOLS:
            result_path = OUTPUT_DIR / f"bt_result_{sym}.pkl"
            if result_path.exists():
                with open(result_path, "rb") as f:
                    result = pickle.load(f)
                trades = [t for t in result.trades if hasattr(t, "closed") and t.closed]
                wins = sum(1 for t in trades if getattr(t, "profit_usd_cost", 0) > 0)
                pnl = sum(getattr(t, "profit_usd_cost", 0) for t in trades)
                all_results[sym] = {
                    "symbol": sym,
                    "result": result,
                    "n_trades": len(trades),
                    "win_rate": wins / len(trades) * 100 if trades else 0,
                    "pnl": pnl,
                }
                logger.info(f"Chargé {sym}: {len(trades)} trades, PnL ${pnl:+.2f}")
            else:
                logger.warning(f"Pickle introuvable: {result_path}")

        if all_results:
            evaluate_portfolio(all_results)
        else:
            logger.error("Aucun résultat à évaluer")
        sys.exit(0)

    if args.symbol:
        # Run single symbol
        symbols_to_run = [args.symbol]
    else:
        symbols_to_run = SYMBOLS

    logger.info(f"Backtest {len(symbols_to_run)} symboles {TIMEFRAME} | Risk: {RISK_PER_TRADE:.4f}")
    logger.info(f"Symboles: {', '.join(symbols_to_run)}")
    logger.info(f"Période: {START_DATE} → {END_DATE}")
    logger.info(f"Capital: ${CAPITAL:,}")
    logger.info("")

    # Load existing progress
    import json

    all_results = {}
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            all_results = json.load(f)
        logger.info(f"Progrès existant: {list(all_results.keys())}")

    total_t0 = time.time()

    for symbol in symbols_to_run:
        if symbol in all_results and all_results[symbol].get("done") and not args.symbol:
            logger.info(f"  ⏭️  {symbol} déjà fait, skip")
            continue

        try:
            result = run_symbol(symbol)
            if result:
                all_results[symbol] = {
                    "done": True,
                    "n_trades": result["n_trades"],
                    "win_rate": round(result["win_rate"], 1),
                    "pnl": round(result["pnl"], 2),
                    "elapsed": round(result["elapsed"], 0),
                }
                # Save progress
                with open(PROGRESS_FILE, "w") as f:
                    json.dump(all_results, f, indent=2)
        except Exception as e:
            logger.error(f"Erreur sur {symbol}: {e}", exc_info=True)

        logger.info("")

    total_elapsed = time.time() - total_t0

    # Summary
    logger.info(f"{'=' * 60}")
    logger.info("RÉSUMÉ DES BACKTESTS")
    logger.info(f"{'=' * 60}")
    for sym, data in all_results.items():
        if isinstance(data, dict) and data.get("done"):
            logger.info(
                f"  {sym:<10}: {data['n_trades']:>5} trades, "
                f"WR {data['win_rate']:.1f}%, PnL ${data['pnl']:>+9.2f}, "
                f"{data['elapsed']:.0f}s"
            )

    logger.info(f"\nTemps total: {total_elapsed:.0f}s ({total_elapsed / 60:.1f}min)")

    # FTMO Portfolio (only if all symbols done)
    if not args.symbol:
        # Load full results from pickles
        all_results_full = {}
        for sym in SYMBOLS:
            result_path = OUTPUT_DIR / f"bt_result_{sym}.pkl"
            if result_path.exists():
                with open(result_path, "rb") as f:
                    result = pickle.load(f)
                all_results_full[sym] = {"symbol": sym, "result": result}

        if len(all_results_full) == len(SYMBOLS):
            verdict = evaluate_portfolio(all_results_full)
            if verdict:
                logger.info(f"\nVerdict FTMO Portfolio: {'PASS ✅' if verdict.passed else 'FAIL ❌'}")
                logger.info(f"  PnL: ${verdict.total_pnl:+.2f} ({verdict.total_pnl_pct:.2f}%)")
                logger.info(f"  Max DD: {verdict.max_dd_pct:.2f}%")
                logger.info(f"  Max Daily Loss: {verdict.max_daily_loss_pct:.2f}%")
                logger.info(f"  Trading Days: {verdict.trading_days}")
        else:
            logger.info(
                f"{len(all_results_full)}/{len(SYMBOLS)} symboles prêts. Lancez --evaluate quand tous sont finis."
            )

    logger.info("\n✅ Terminé")
