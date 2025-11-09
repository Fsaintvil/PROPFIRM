"""Auto-improve the bot using LightGBM grid search with time-series CV.

Saves best config, CV results, retrained model and backtest under
`artifacts/auto_improve/`.
"""
from __future__ import annotations

import json
from pathlib import Path

# itertools not needed yet
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import accuracy_score


def time_series_cv_scores(X, y, params, num_boost_round=50, n_splits=5):
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
        pred_labels = (preds > 0.5).astype(int)
        acc = float(accuracy_score(y_val, pred_labels))
        scores.append(acc)
    return scores


def run_grid_search(horizons, grid, num_boost_round=50):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    results = []
    for horizon in horizons:
        print("Grid search horizon", horizon)
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
    }
    model = lgb.train(lgb_params, dtrain, num_boost_round=100)
    art = base / "artifacts" / "auto_improve"
    art.mkdir(parents=True, exist_ok=True)
    model.save_model(str(art / "best_lightgbm.txt"))
    # run backtest using existing script with conservative params
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
    # copy backtest report
    bt = base / "artifacts" / "backtest_report.json"
    if bt.exists():
        art.joinpath("backtest_report.json").write_text(
            bt.read_text(), encoding="utf-8"
        )
    return art


def main():
    horizons = [1, 5, 15]
    # small grid
    grid = [
        {"num_leaves": 15, "learning_rate": 0.1},
        {"num_leaves": 31, "learning_rate": 0.05},
        {"num_leaves": 63, "learning_rate": 0.01},
    ]
    results = run_grid_search(horizons, grid, num_boost_round=50)
    out = Path.cwd() / "artifacts" / "auto_improve"
    out.mkdir(parents=True, exist_ok=True)
    (out / "grid_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    # pick best by mean_accuracy
    valid = [r for r in results if r["mean_accuracy"] is not None]
    best = max(valid, key=lambda r: r["mean_accuracy"]) if valid else None
    (out / "best.json").write_text(
        json.dumps(best, indent=2), encoding="utf-8"
    )
    if best:
        art = retrain_and_backtest(best)
        print("Auto-improve finished. Artifacts in", art)
    else:
        print("No valid results from grid search.")


if __name__ == "__main__":
    main()
