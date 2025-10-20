"""Time-series cross-validation (expanding window) for multiple horizons.

Saves per-fold metrics to artifacts/cv/<horizon>_fold<i>.json and a
summary JSON.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import accuracy_score


def run_cv(horizon: int, n_splits: int = 5):
    base = Path.cwd()
    df = pd.read_csv(
        base / "data" / "features_sample.csv", parse_dates=[0], index_col=0
    )
    # generate label if missing
    if "label" not in df.columns:
        df = df.copy()
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()

    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values

    n = len(df)
    fold_size = n // (n_splits + 1)
    results = []
    for i in range(1, n_splits + 1):
        train_end = fold_size * i
        val_start = train_end
        val_end = train_end + fold_size
        if val_end > n:
            val_end = n
        X_train = X.iloc[:train_end]
        y_train = y[:train_end]
        X_val = X.iloc[val_start:val_end]
        y_val = y[val_start:val_end]
        if len(y_val) == 0:
            continue
        dtrain = lgb.Dataset(X_train.values, label=y_train)
        params = {
            "objective": "binary",
                "metric": "binary_logloss",
                    "verbose": -1,
                    }
        model = lgb.train(params, dtrain, num_boost_round=50)
        preds = model.predict(X_val.values)
        pred_labels = (preds > 0.5).astype(int)
        acc = float(accuracy_score(y_val, pred_labels))
        results.append({"fold": i, "accuracy": acc, "n_val": len(y_val)})
        outdir = base / "artifacts" / "cv"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"h{horizon}_fold{i}.json").write_text(
            json.dumps(results[-1], indent=2), encoding="utf-8"
        )
    # summary
    summary = {
        "horizon": horizon,
            "n_splits": n_splits,
                "mean_accuracy": float(np.mean([r["accuracy"] for r in results]))
        if results
        else None,
            "std_accuracy": float(np.std([r["accuracy"] for r in results]))
        if results
        else None,
            "folds": results,
                }
    (base / "artifacts" / "cv" / f"h{horizon}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main():
    horizons = [1, 5, 15]
    summaries = []
    for h in horizons:
        print("Running CV for horizon", h)
        s = run_cv(h, n_splits=5)
        summaries.append(s)
    (Path.cwd() / "artifacts" / "cv" / "summary.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    print("CV done. Summaries written to artifacts/cv/")


if __name__ == "__main__":
    main()
