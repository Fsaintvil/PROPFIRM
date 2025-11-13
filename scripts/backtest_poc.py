"""Simple backtest POC that applies the LightGBM POC model to features.

If `data/features_sample.csv` is missing the script synthesizes a dataset
and saves it for reproducibility. It then loads `artifacts/lightgbm_poc.txt`
and computes a naive long-only strategy based on predicted probability >0.5.
Saves `artifacts/backtest_report.json` with simple metrics.
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd


def ensure_features(path: Path) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
        return df
    # synthesize small sample
    idx = pd.date_range("2025-01-01", periods=500, freq="1min")
    df = pd.DataFrame(index=idx)
    df["close"] = np.cumsum(np.random.randn(len(idx)) * 0.1) + 1.2
    df["volume"] = np.random.randint(1, 100, size=len(idx))
    df["sma_1T"] = df["close"].rolling(5).mean()
    df["ema_15T"] = df["close"].ewm(span=10, adjust=False).mean()
    df["rsi_60T"] = 50 + np.random.randn(len(idx))
    df = df.dropna()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def load_model(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Model file not found: {path}")
    return lgb.Booster(model_file=str(path))


def run_backtest(
    df: pd.DataFrame,
        model,
            transaction_cost: float = 0.0,
            slippage: float = 0.0,
            max_position_size: float = 1.0,
            stop_loss: float = 0.015,
            take_profit: float = 0.03,
            ) -> dict:
    # prepare X features (drop close/volume if present)
    features = [c for c in df.columns if c not in ("label",)]
    X = df[features].ffill().fillna(0).values
    preds = model.predict(X)
    # position: 1 if pred>0.5 else 0
    pos = (preds > 0.5).astype(int)
    # compute returns as next_close/current_close - 1
    close = df["close"].values
    next_close = np.roll(close, -1)
    returns = (next_close - close) / (close + 1e-9)

    # apply stop-loss / take-profit by clipping returns when in position
    clipped = np.clip(returns, -stop_loss, take_profit)
    # strategy return is clipped when in position, else 0
    strategy_ret = np.where(pos == 1, clipped, 0.0)
    # apply entry and exit costs: entry when pos goes 0->1, exit when 1->0
    prev_pos = np.concatenate([[0], pos[:-1]])
    entries = (pos == 1) & (prev_pos == 0)
    exits = (pos == 0) & (prev_pos == 1)
    # subtract per-entry cost (transaction + slippage) on the entry tick
    strategy_ret = strategy_ret - entries * (transaction_cost + slippage)
    # subtract per-exit cost on the exit tick (we align by shifting exits)
    exit_costs = exits.astype(float) * (transaction_cost + slippage)
    # place exit costs on the preceding tick's return (where position was 1)
    exit_costs = np.concatenate([[0.0], exit_costs[:-1]])
    strategy_ret = strategy_ret - exit_costs
    # apply max position sizing as fraction
    strategy_ret = strategy_ret * max_position_size

    # drop the last tick (rolled next_close)
    strategy_ret = strategy_ret[:-1]

    # equity series and metrics
    equity_series = np.cumprod(1 + strategy_ret) if len(strategy_ret) > 0 else np.array([])
    total_return = float(equity_series[-1] - 1) if len(equity_series) > 0 else 0.0
    avg_ret = float(np.nanmean(strategy_ret)) if len(strategy_ret) > 0 else 0.0
    win_rate = float((strategy_ret > 0).mean()) if len(strategy_ret) > 0 else 0.0
    if len(strategy_ret) > 0:
        sharpe = float(
            np.nanmean(strategy_ret)
            / (np.nanstd(strategy_ret) + 1e-9)
            * np.sqrt(252 * 24 * 60)
        )
    else:
        sharpe = 0.0
    # max drawdown from equity series
    if len(equity_series) > 0:
        running_max = np.maximum.accumulate(equity_series)
        drawdown = equity_series - running_max
        max_drawdown = float(np.min(drawdown))
    else:
        max_drawdown = 0.0

    return {
        "total_return": total_return,
        "avg_return_per_tick": avg_ret,
        "win_rate": win_rate,
        "sharpe_annualized": sharpe,
        "max_drawdown": max_drawdown,
    }


def main():
    base = Path.cwd()
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--transaction-cost", type=float, default=0.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--max-position-size", type=float, default=1.0)
    parser.add_argument("--stop-loss", type=float, default=0.015)
    parser.add_argument("--take-profit", type=float, default=0.03)
    args = parser.parse_args()

    feat = base / "data" / "features_sample.csv"
    df = ensure_features(feat)
    model_path = base / "artifacts" / "lightgbm_poc.txt"
    if not model_path.exists():
        print("Model not found, training a new POC model first...")
        # import and run training script
        import subprocess

        subprocess.run(["python", "scripts/train_lightgbm.py"], check=True)
    model = load_model(model_path)
    report = run_backtest(
        df,
            model,
                transaction_cost=args.transaction_cost,
                slippage=args.slippage,
                max_position_size=args.max_position_size,
                stop_loss=args.stop_loss,
                take_profit=args.take_profit,
                )
    out_dir = base / "artifacts"
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "backtest_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("Backtest report saved to artifacts/backtest_report.json")


if __name__ == "__main__":
    main()
