"""Vérification complète des backtests/instruments.

Fonctions:
- Pour chaque fichier sous
    `data/backtests/intraday/normalized/*.csv.gz` : lire l'index, afficher
    min/max et nombre de lignes.
- Lister les colonnes de `data/features_sample.csv` si présent.
- Si un modèle LightGBM `best_lightgbm*.txt` existe, charger et:
    - calculer prédictions sur `features_sample.csv`;
    - simuler un trading simple (seuil 0.5) et retourner métriques
        (total_return, win_rate, sharpe approximé).

Usage: exécuter depuis la racine du dépôt:
`python tools/verify_full_pipeline.py`
"""
from pathlib import Path
import pandas as pd
import numpy as np
import json
# pas d'import inutilisé

BASE = Path.cwd()
OUT = {}

# 1) Chercher fichiers backtests intraday normalisés
bt_dir = BASE / "data" / "backtests" / "intraday" / "normalized"
files = []
if bt_dir.exists():
    files = list(bt_dir.glob("*.csv.gz"))

instruments = {}
for f in files:
    try:
        # pandas peut lire gzip directement
        df = pd.read_csv(f, parse_dates=[0], index_col=0)
        idx = df.index
        instruments[f.name] = {
            "path": str(f),
            "n_rows": len(df),
            "start": str(idx.min()),
            "end": str(idx.max()),
        }
    except Exception as e:
        instruments[f.name] = {"path": str(f), "error": str(e)}

OUT["backtests_intraday_normalized"] = instruments

# 2) Lister features_sample.csv colonnes
fs = BASE / "data" / "features_sample.csv"
features_info = {}
if fs.exists():
    try:
        df = pd.read_csv(fs, parse_dates=[0], index_col=0)
        features_info["path"] = str(fs)
        features_info["n_rows"] = len(df)
        features_info["start"] = str(df.index.min())
        features_info["end"] = str(df.index.max())
        features_info["columns"] = list(df.columns)
    except Exception as e:
        features_info["error"] = str(e)
else:
    features_info["missing"] = True

OUT["features_sample"] = features_info

# 3) Charger modèle LightGBM si présent
ai_dir = BASE / "artifacts" / "auto_improve"
model_paths = []
if ai_dir.exists():
    model_paths = list(ai_dir.glob("best_lightgbm*.txt"))
model_metrics = {}
if model_paths:
    try:
        import lightgbm as lgb

        model_path = model_paths[0]
        booster = lgb.Booster(model_file=str(model_path))
        model_metrics["model_path"] = str(model_path)
        if not fs.exists():
            model_metrics["error"] = (
                "features_sample.csv missing - cannot score"
            )
        else:
            X = df.ffill().fillna(0)
            preds = booster.predict(X.values)
            # metrics
            # threshold 0.5
            pos = (preds > 0.5).astype(int)
            close = df["close"].values
            next_close = np.roll(close, -1)
            returns = (next_close - close) / (close + 1e-9)
            strat = np.where(pos == 1, returns, 0.0)[:-1]
            total_return = float(np.prod(1 + strat) - 1)
            win_rate = float((strat > 0).mean()) if len(strat) > 0 else 0.0
            if len(strat) > 0:
                sharpe = float(
                    np.nanmean(strat)
                    / (np.nanstd(strat) + 1e-9)
                    * np.sqrt(252 * 24)
                )
            else:
                sharpe = 0.0
            model_metrics.update({
                "n_predictions": len(preds),
                "total_return_on_features_sample": total_return,
                "win_rate_on_features_sample": win_rate,
                "sharpe_approx_on_features_sample": sharpe,
                "preds_mean": float(np.mean(preds)),
                "preds_std": float(np.std(preds)),
            })
    except Exception as e:
        model_metrics["error"] = str(e)
else:
    model_metrics["missing"] = True

OUT["model_check"] = model_metrics

# 4) Parcourir artifacts/backtest_report.json s'il existe
br = BASE / "artifacts" / "backtest_report.json"
if br.exists():
    try:
        with open(br, "r", encoding="utf-8") as f:
            OUT["artifacts_backtest_report"] = json.load(f)
    except Exception as e:
        OUT["artifacts_backtest_report"] = {"error": str(e)}
else:
    OUT["artifacts_backtest_report"] = {"missing": True}

# 5) Sauvegarder résultat
out_path = BASE / "artifacts" / "verify_full_pipeline_report.json"
out_path.parent.mkdir(exist_ok=True, parents=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(OUT, f, indent=2, ensure_ascii=False)

print(f"Report saved to {out_path}")
print(json.dumps(OUT, indent=2, ensure_ascii=False))
