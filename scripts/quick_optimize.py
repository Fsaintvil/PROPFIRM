"""Optimisation rapide du seuil de décision.

Version simplifiée pour trouver rapidement le seuil optimal.
"""
import json
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path


def main():
    print("🔧 Optimisation rapide du seuil...")

    # Charger le modèle
    model_file = Path("artifacts/auto_improve/best_lightgbm.txt")
    model = lgb.Booster(model_file=str(model_file))

    # Charger les données
    with open("artifacts/auto_improve/best.json", "r") as f:
        best_config = json.load(f)

    df = pd.read_csv("data/features_sample.csv", parse_dates=[0], index_col=0)
    horizon = best_config["horizon"]

    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()

    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values

    # Prédictions
    predictions = model.predict(X.values)

    # Tester quelques seuils rapides
    thresholds = [0.4, 0.45, 0.5, 0.55, 0.6]
    results = []

    for threshold in thresholds:
        pred_labels = (predictions > threshold).astype(int)

        # Métriques simples
        accuracy = (y == pred_labels).mean()
        precision = ((pred_labels == 1) & (y == 1)).sum() / max(
            1, (pred_labels == 1).sum()
        )
        recall = ((pred_labels == 1) & (y == 1)).sum() / max(1, (y == 1).sum())

        results.append(
            {
                "threshold": threshold,
                    "accuracy": accuracy,
                        "precision": precision,
                        "recall": recall,
                        "num_predictions": (pred_labels == 1).sum(),
                        }
        )

        print(
            f"Seuil {threshold}: acc={accuracy:.3f}, prec={precision:.3f}, "
            f"recall={recall:.3f}, preds={results[-1]['num_predictions']}"
        )

    # Trouver le meilleur équilibre
    results_df = pd.DataFrame(results)

    # Score composite simple
    results_df["f1"] = (
        2
        * (results_df["precision"] * results_df["recall"])
        / (results_df["precision"] + results_df["recall"] + 1e-8)
    )
    best_idx = results_df["f1"].idxmax()
    best_threshold = results_df.loc[best_idx, "threshold"]

    # Sauvegarder le résultat
    opt_dir = Path("artifacts/auto_improve/optimization")
    opt_dir.mkdir(exist_ok=True)

    optimal_result = {
        "recommended_threshold": float(best_threshold),
            "original_threshold": 0.5,
                "improvement": {
            "accuracy": float(
                results_df.loc[best_idx, "accuracy"]
                - results_df[results_df["threshold"] == 0.5]["accuracy"].iloc[
                    0
                ]
            ),
                "f1_score": float(results_df.loc[best_idx, "f1"]),
                    },
                    "all_results": results_df.to_dict("records"),
                }

    with open(opt_dir / "quick_optimization.json", "w") as f:
        json.dump(optimal_result, f, indent=2)

    print(f"\n🎯 Seuil optimal trouvé: {best_threshold}")
    print(f"📈 F1-Score: {results_df.loc[best_idx, 'f1']:.3f}")
    print(f"📊 Accuracy: {results_df.loc[best_idx, 'accuracy']:.3f}")
    print(f"💾 Résultats sauvés dans: {opt_dir}/quick_optimization.json")


if __name__ == "__main__":
    main()
