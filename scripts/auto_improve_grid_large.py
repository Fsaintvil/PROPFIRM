"""Larger grid search for LightGBM using time-series CV.

Runs an expanded grid across several LightGBM hyperparameters, selects
the best config by mean CV accuracy, retrains on all data and runs the
protected backtest. Results are saved under artifacts/auto_improve/.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
# accuracy_score removed; CV now scores by financial metric (Sharpe)


def time_series_cv_scores(X, y, params, num_boost_round=100, n_splits=5):
    n = len(X)
    fold_size = n // (n_splits + 1)
    scores = []
    for i in range(1, n_splits + 1):
        train_end = fold_size * i
        val_start = train_end
        val_end = min(train_end + fold_size, n)
        if val_start >= val_end:
            continue
        X_train = X.iloc[:train_end]
        y_train = y[:train_end]
        X_val = X.iloc[val_start:val_end]
        y_val = y[val_start:val_end]
        dtrain = lgb.Dataset(X_train.values, label=y_train)
        model = lgb.train(params, dtrain, num_boost_round=num_boost_round)
        preds = model.predict(X_val.values)
        # compute simple trading returns from preds on validation fold
        try:
            close = X_val["close"].values
            next_close = np.roll(close, -1)
            returns = (next_close - close) / (close + 1e-9)
            pos = (preds > 0.5).astype(int)
            strat = np.where(pos == 1, returns, 0.0)[:-1]
            if len(strat) == 0:
                fold_sharpe = 0.0
            else:
                fold_sharpe = float(
                    np.nanmean(strat) / (np.nanstd(strat) + 1e-9)
                    * np.sqrt(252 * 24)
                )
        except Exception:
            fold_sharpe = 0.0
        scores.append(fold_sharpe)
    return scores


def run_grid_search(horizons, grid, num_boost_round=100):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    results = []
    for horizon in horizons:
        print("Large grid search horizon", horizon)
        df_local = df.copy()
        if "label" not in df_local.columns:
            df_local["label"] = (
                df_local["close"].shift(-horizon) > df_local["close"]
            ).astype(int)
            df_local = df_local.dropna()
        X = df_local.drop(columns=["label"]).ffill().fillna(0)
        y = df_local["label"].values
        for params in grid:
            lgb_params = {
                "objective": "binary",
                    "metric": "binary_logloss",
                        "verbose": -1,
                        "num_leaves": params["num_leaves"],
                        "learning_rate": params["learning_rate"],
                        "max_depth": params.get("max_depth", -1),
                        "min_data_in_leaf": params.get("min_data_in_leaf", 20),
                        }
            scores = time_series_cv_scores(
                X, y, lgb_params, num_boost_round=num_boost_round
            )
            mean_score = float(np.mean(scores)) if scores else None
            std_score = float(np.std(scores)) if scores else None
            r = {
                "horizon": horizon,
                    "params": params,
                        "mean_accuracy": mean_score,
                        "std_accuracy": std_score,
                        "scores": scores,
                        }
            results.append(r)
    return results


def retrain_and_backtest(best_item):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    horizon = best_item["horizon"]
    params = best_item["params"]
    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()
    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values
    dtrain = lgb.Dataset(X.values, label=y)
    lgb_params = {
        "objective": "binary",
            "metric": "binary_logloss",
                "verbose": -1,
                "num_leaves": params["num_leaves"],
                "learning_rate": params["learning_rate"],
                "max_depth": params.get("max_depth", -1),
                "min_data_in_leaf": params.get("min_data_in_leaf", 20),
                }
    model = lgb.train(lgb_params, dtrain, num_boost_round=200)
    art = base / "artifacts" / "auto_improve"
    art.mkdir(parents=True, exist_ok=True)
    model.save_model(str(art / "best_lightgbm_large.txt"))
    # run backtest with conservative protections
    import subprocess

    subprocess.run(
        [
            "python",
                "scripts/backtest_poc.py",
                    "--transaction-cost",
                    "0.0001",
                    "--slippage",
                    "0.0002",
                    "--max-position-size",
                    "0.1",
                    "--stop-loss",
                    "0.02",
                    "--take-profit",
                    "0.04",
                    ],
                    check=True,
                )
    bt = base / "artifacts" / "backtest_report.json"
    if bt.exists():
        content = bt.read_text()
        (art / "backtest_report_large.json").write_text(
            content, encoding="utf-8"
        )
    return art


def main():
    horizons = [1, 5, 15]
    # larger grid but kept modest to keep runtime reasonable
    grid = []
    for num_leaves in (31, 63, 127):
        for lr in (0.1, 0.05, 0.01):
            for max_depth in (-1, 6):
                for min_leaf in (5, 20):
                    grid.append(
                        {
                            "num_leaves": num_leaves,
                                "learning_rate": lr,
                                    "max_depth": max_depth,
                                    "min_data_in_leaf": min_leaf,
                                    }
                    )

    results = run_grid_search(horizons, grid, num_boost_round=100)
    out = Path.cwd() / "artifacts" / "auto_improve"
    out.mkdir(parents=True, exist_ok=True)
    grid_file = out / "grid_results_large.json"
    grid_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    valid = [r for r in results if r["mean_accuracy"] is not None]
    best = max(valid, key=lambda r: r["mean_accuracy"]) if valid else None
    best_file = out / "best_large.json"
    best_file.write_text(json.dumps(best, indent=2), encoding="utf-8")
    if best:
        art = retrain_and_backtest(best)
        print("Large auto-improve finished. Artifacts in", art)
    else:
        print("No valid results from large grid search.")


if __name__ == "__main__":
    main()
