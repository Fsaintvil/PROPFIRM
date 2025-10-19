"""POC training script for LightGBM using MTF features.

This script is minimal: it loads a CSV `data/features_sample.csv` if present,
or synthesizes data for a smoke run. It trains a small LightGBM model and
saves metrics to `artifacts/train_metrics.json`.
"""
from __future__ import annotations

import json
from pathlib import Path
import argparse

import lightgbm as lgb
import numpy as np
import pandas as pd


def load_features(path: Path, horizon: int = 5) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
        # If user CSV doesn't include a label, create one from future close
        # Horizon is configurable via CLI --horizon
        if "label" not in df.columns:
            if "close" not in df.columns:
                raise ValueError(
                    "features CSV missing both 'label' and 'close' columns; "
                    "cannot generate target"
                )
            df = df.copy()
            df["future_close"] = df["close"].shift(-horizon)
            df["label"] = (df["future_close"] > df["close"]).astype(int)
            df = df.drop(columns=["future_close"])
        return df
    # synthesize small sample
    idx = pd.date_range("2025-01-01", periods=500, freq="1T")
    df = pd.DataFrame(index=idx)
    df["close"] = np.cumsum(np.random.randn(len(idx)) * 0.1) + 1.2
    df["volume"] = np.random.randint(1, 100, size=len(idx))
    # add a few features
    df["sma_1T"] = df["close"].rolling(5).mean()
    df["ema_15T"] = df["close"].ewm(span=10, adjust=False).mean()
    df["rsi_60T"] = 50 + np.random.randn(len(idx))
    # binary label: 1 if next close higher than current
    df["label"] = (df["close"].shift(-1) > df["close"]).astype(int)
    df = df.dropna()
    return df


def train(df: pd.DataFrame):
    features = [c for c in df.columns if c != "label"]
    X = df[features].values
    y = df["label"].values
    train_size = int(len(df) * 0.8)
    X_train, X_val = X[:train_size], X[train_size:]
    y_train, y_val = y[:train_size], y[train_size:]

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)
    params = {"objective": "binary", "metric": "binary_logloss", "verbose": -1}
    model = lgb.train(
        params,
        dtrain,
        num_boost_round=50,
        valid_sets=[dval],
    )
    # simple metric
    preds = model.predict(X_val)
    acc = ((preds > 0.5) == y_val).mean()
    return model, {"accuracy": float(acc)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--horizon",
        type=int,
        default=5,
        help="horizon in ticks to build label",
    )
    args = parser.parse_args()
    base = Path.cwd()
    path = base / "data" / "features_sample.csv"
    df = load_features(path, horizon=args.horizon)
    model, metrics = train(df)
    out_dir = base / "artifacts"
    out_dir.mkdir(exist_ok=True)
    model.save_model(str(out_dir / "lightgbm_poc.txt"))
    with open(out_dir / "train_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)


if __name__ == "__main__":
    main()
