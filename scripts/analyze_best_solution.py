"""Analyse complète de la meilleure solution trouvée par auto-improve.

Ce script génère un rapport détaillé avec:
- Résumé des hyperparamètres et métriques
- Explications SHAP des features importantes
- Visualisations P&L et performance
- Recommendations pour améliorer le modèle
"""
from __future__ import annotations

import json
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import lightgbm as lgb
import shap
from pathlib import Path


def load_best_config():
    """Charge la meilleure configuration depuis best.json"""
    best_file = Path("artifacts/auto_improve/best.json")
    if not best_file.exists():
        raise FileNotFoundError(
            "best.json not found. Run auto_improve_bot.py first."
        )

    with open(best_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_backtest_report():
    """Charge le rapport de backtest"""
    bt_file = Path("artifacts/auto_improve/backtest_report.json")
    if not bt_file.exists():
        raise FileNotFoundError("backtest_report.json not found.")

    with open(bt_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_model_and_data(best_config):
    """Charge le modèle et les données pour l'analyse"""
    model_file = Path("artifacts/auto_improve/best_lightgbm.txt")
    if not model_file.exists():
        raise FileNotFoundError("Model file not found.")

    # Charger le modèle
    model = lgb.Booster(model_file=str(model_file))

    # Recharger les données avec le même preprocessing
    data_path = Path("data/features_sample.csv")
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)

    horizon = best_config["horizon"]
    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()

    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values

    return model, X, y, df


def generate_shap_analysis(model, X):
    """Génère l'analyse SHAP et sauvegarde les résultats"""
    print("Génération de l'analyse SHAP...")

    # Créer l'explainer SHAP
    explainer = shap.TreeExplainer(model)

    # Calculer les valeurs SHAP (utiliser un échantillon si trop grand)
    sample_size = min(500, len(X))
    X_sample = X.sample(n=sample_size, random_state=42)
    shap_values = explainer.shap_values(X_sample)

    # Créer le dossier pour les figures
    shap_dir = Path("artifacts/auto_improve/shap")
    shap_dir.mkdir(exist_ok=True)

    # Summary plot
    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, X_sample, show=False)
    plt.tight_layout()
    plt.savefig(shap_dir / "summary_plot.png", dpi=150, bbox_inches="tight")
    plt.close()

    # Feature importance
    plt.figure(figsize=(10, 6))
    shap.summary_plot(shap_values, X_sample, plot_type="bar", show=False)
    plt.tight_layout()
    plt.savefig(
        shap_dir / "feature_importance.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

    # Sauvegarder les importances dans un CSV
    feature_importance = pd.DataFrame(
        {"feature": X.columns, "importance": np.abs(shap_values).mean(axis=0)}
    ).sort_values("importance", ascending=False)

    feature_importance.to_csv(shap_dir / "feature_importance.csv", index=False)

    return feature_importance


def generate_performance_plots(df, model, X, y):
    """Génère les graphiques de performance"""
    print("Génération des graphiques de performance...")

    # Prédictions
    predictions = model.predict(X.values)
    pred_labels = (predictions > 0.5).astype(int)

    plots_dir = Path("artifacts/auto_improve/plots")
    plots_dir.mkdir(exist_ok=True)

    # 1. Distribution des prédictions
    plt.figure(figsize=(10, 6))
    plt.hist(predictions, bins=50, alpha=0.7, edgecolor="black")
    plt.axvline(x=0.5, color="red", linestyle="--", label="Seuil de décision")
    plt.xlabel("Probabilité prédite")
    plt.ylabel("Fréquence")
    plt.title("Distribution des probabilités prédites")
    plt.legend()
    plt.tight_layout()
    plt.savefig(
        plots_dir / "prediction_distribution.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

    # 2. Matrice de confusion
    from sklearn.metrics import confusion_matrix

    cm = confusion_matrix(y, pred_labels)

    plt.figure(figsize=(8, 6))
    plt.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
    plt.title("Matrice de confusion")
    plt.colorbar()
    tick_marks = [0, 1]
    plt.xticks(tick_marks, ["Baisse", "Hausse"])
    plt.yticks(tick_marks, ["Baisse", "Hausse"])

    # Ajouter les valeurs dans les cellules
    for i in range(2):
        for j in range(2):
            plt.text(j, i, f"{cm[i, j]}", ha="center", va="center")

    plt.ylabel("Vrai label")
    plt.xlabel("Label prédit")
    plt.tight_layout()
    plt.savefig(
        plots_dir / "confusion_matrix.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

    # 3. Performance dans le temps (si possible)
    if len(df) > 100:
        # Diviser en périodes pour voir l'évolution
        window_size = len(df) // 10
        periods = []
        accuracies = []

        for i in range(0, len(df) - window_size, window_size):
            y_window = y[i:i + window_size]
            pred_window = pred_labels[i:i + window_size]
            acc = (y_window == pred_window).mean()
            periods.append(i + window_size // 2)
            accuracies.append(acc)

        plt.figure(figsize=(12, 6))
        plt.plot(periods, accuracies, marker="o")
        plt.axhline(y=0.5, color="red", linestyle="--", label="Hasard")
        plt.xlabel("Position dans les données")
        plt.ylabel("Accuracy")
        plt.title("Évolution de la performance dans le temps")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(
            plots_dir / "performance_evolution.png",
                dpi=150,
                    bbox_inches="tight",
                    )
        plt.close()


def generate_report(best_config, backtest_report, feature_importance):
    """Génère le rapport final en markdown"""

    # Créer la liste des top features
    top_features_list = []
    for i, (_, row) in enumerate(feature_importance.head(10).iterrows()):
        feature_line = f"{i+1}. {row['feature']}: {row['importance']:.4f}"
        top_features_list.append(feature_line)

    report_content = f"""# Rapport d'analyse - Meilleure solution

## Configuration optimale

**Horizon de prédiction:** {best_config['horizon']} périodes

**Hyperparamètres LightGBM:**
- num_leaves: {best_config['params']['num_leaves']}
- learning_rate: {best_config['params']['learning_rate']}

## Performance de validation croisée

**Accuracy moyenne:** {best_config['mean_accuracy']:.4f} ± """
    report_content += f"{best_config['std_accuracy']:.4f}"
    report_content += """

**Scores par fold:**
"""

    # Ajouter les scores des folds
    for i, score in enumerate(best_config["scores"]):
        report_content += f"- Fold {i+1}: {score:.4f}\n"

    report_content += """
## Performance du backtest

**Métriques financières:**
"""

    # Ajouter les métriques financières ligne par ligne
    total_ret = backtest_report["total_return"]
    avg_ret = backtest_report["avg_return_per_tick"]
    win_rate = backtest_report["win_rate"]
    sharpe = backtest_report["sharpe_annualized"]

    report_content += f"- Rendement total: {total_ret:.4f} "
    report_content += f"({total_ret*100:.2f}%)\n"
    report_content += f"- Rendement moyen par tick: {avg_ret:.6f}\n"
    report_content += f"- Taux de réussite: {win_rate:.4f} "
    report_content += f"({win_rate*100:.1f}%)\n"
    report_content += f"- Sharpe annualisé: {sharpe:.2f}\n"

    report_content += """

## Features les plus importantes

Les 10 features les plus importantes selon LightGBM (gain):

{chr(10).join(top_features_list)}

## Analyse et recommandations

### Points positifs
- Le modèle dépasse légèrement le hasard (accuracy > 0.5)
- La validation croisée temporelle montre une certaine robustesse
- Les features techniques semblent capturer des signaux utiles

### Points d'attention
- Le taux de réussite est relativement faible
  ({win_rate*100:.1f}%)
- La variance entre les folds CV est significative
- La Sharpe très élevée peut indiquer un problème d'échelle

### Recommandations d'amélioration

1. **Features engineering avancé:**
   - Ajouter des features de volatility clustering
   - Inclure des indicateurs de microstructure
   - Tester des features de sentiment ou macro

2. **Optimisation du modèle:**
   - Tester d'autres algorithmes (XGBoost, CatBoost)
   - Optimiser le seuil de décision (actuellement 0.5)
   - Implémenter un ensemble de modèles

3. **Amélioration du backtest:**
   - Inclure des coûts de transaction plus réalistes
   - Modéliser la latence d'exécution
   - Tester sur différentes périodes de marché

4. **Gestion des risques:**
   - Implémenter un sizing adaptatif
   - Ajouter des filtres de régime de marché
   - Développer des métriques de drawdown

## Fichiers générés

- `artifacts/auto_improve/shap/` - Analyses SHAP
- `artifacts/auto_improve/plots/` - Graphiques de performance
- `artifacts/auto_improve/best_lightgbm.txt` - Modèle entraîné
- `artifacts/auto_improve/grid_results.json` - Résultats complets

---
*Rapport généré le {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

    report_file = Path("artifacts/auto_improve/rapport_complet.md")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"Rapport sauvegardé dans: {report_file}")


def main():
    """Fonction principale d'analyse"""
    try:
        print("🔍 Analyse de la meilleure solution...")

        # Charger les configurations et résultats
        best_config = load_best_config()
        backtest_report = load_backtest_report()

        print(
            f"✅ Configuration chargée: horizon={best_config['horizon']}, "
            f"accuracy={best_config['mean_accuracy']:.4f}"
        )

        # Charger le modèle et les données
        model, X, y, df = load_model_and_data(best_config)
        print(
            f"✅ Modèle et données chargés: {len(X)} échantillons, "
            f"{len(X.columns)} features"
        )

        # Générer l'analyse SHAP
        feature_importance = generate_shap_analysis(model, X)
        print(
            f"✅ Analyse SHAP terminée: {len(feature_importance)} "
            f"features analysées"
        )

        # Générer les graphiques de performance
        generate_performance_plots(df, model, X, y)
        print("✅ Graphiques de performance générés")

        # Générer le rapport final
        generate_report(best_config, backtest_report, feature_importance)
        print("✅ Rapport complet généré")

        print("\n🎯 Analyse terminée! Consultez:")
        print("   - artifacts/auto_improve/rapport_complet.md")
        print("   - artifacts/auto_improve/shap/")
        print("   - artifacts/auto_improve/plots/")

    except Exception as e:
        print(f"❌ Erreur lors de l'analyse: {e}")
        raise


if __name__ == "__main__":
    main()
