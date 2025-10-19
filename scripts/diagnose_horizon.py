"""Diagnostic tool for a given horizon.

Trains model with horizon=1, loads model, predicts on full dataset and
outputs metrics and a CSV with timestamp, close, label, pred_prob,
pred_label and a few features.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
)
import lightgbm as lgb


def run():
    base = Path.cwd()
    # train with horizon=1
    subprocess.run(
        ["python", "scripts/train_lightgbm.py", "--horizon", "1"],
        check=True,
    )
    # load features
    feat = base / "data" / "features_sample.csv"
    df = pd.read_csv(feat, parse_dates=[0], index_col=0)
    # load model
    model = lgb.Booster(
        model_file=str(base / "artifacts" / "lightgbm_poc.txt")
    )
    # prepare labels: if training created label only in-memory,
    # recreate it here
    if "label" not in df.columns:
        # horizon used for training was 1
        df = df.copy()
        df["label"] = (df["close"].shift(-1) > df["close"]).astype(int)
        df = df.dropna()

    # prepare X same as in train
    features = [c for c in df.columns if c != "label"]
    X = df[features].ffill().fillna(0).values
    preds = model.predict(X)
    y = df["label"].values
    pred_labels = (preds > 0.5).astype(int)
    metrics = {
        "accuracy": float(accuracy_score(y, pred_labels)),
        "precision": float(precision_score(y, pred_labels, zero_division=0)),
        "recall": float(recall_score(y, pred_labels, zero_division=0)),
        "f1": float(f1_score(y, pred_labels, zero_division=0)),
        "confusion_matrix": confusion_matrix(y, pred_labels).tolist(),
    }
    out_dir = base / "artifacts" / "diagnostics" / "h1"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    # write CSV with a few cols
    out_df = df[["close"]].copy()
    out_df["label"] = y
    out_df["pred_prob"] = preds
    out_df["pred_label"] = pred_labels
    # include small set of features if present
    for fname in ("sma_1T", "ema_15T", "fvg_gap_size", "mp_vpoc_price"):
        if fname in df.columns:
            out_df[fname] = df[fname]
    out_df.to_csv(out_dir / "preds.csv")
    print("Diagnostics written to", out_dir)


if __name__ == "__main__":
    run()
