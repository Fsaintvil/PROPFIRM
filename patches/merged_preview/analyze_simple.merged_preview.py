# Merged preview for prefix: analyze
# Generated from 4 files

################################################################################
# FROM: scripts\analyze_best_solution.py
################################################################################
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


################################################################################
# FROM: scripts\analyze_decision_dump.py
################################################################################
import sys
import json
from collections import Counter

def analyze(path, sample_limit=10):
    total = 0
    per_symbol = Counter()
    enh_count = 0
    actions_counter = Counter()
    samples = []

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                # try eval-like fallback for some dumped reprs
                try:
                    rec = json.loads(line.replace("'", '"'))
                except Exception:
                    continue
            total += 1
            sym = rec.get('symbol', 'UNKNOWN')
            per_symbol[sym] += 1
            dec = rec.get('decision', {})
            if isinstance(dec, dict) and dec.get('enhancement_applied'):
                enh_count += 1
            action = dec.get('action') if isinstance(dec, dict) else None
            if action:
                actions_counter[action] += 1

            # collect samples where action != 'hold' or enhancement applied
            if (isinstance(dec, dict) and dec.get('enhancement_applied')) or (action and action.lower() != 'hold'):
                if len(samples) < sample_limit:
                    samples.append(rec)

    out = {
        'path': path,
        'total_lines': total,
        'per_symbol_counts': dict(per_symbol.most_common()),
        'enhancement_applied_count': enh_count,
        'action_counts': dict(actions_counter.most_common()),
        'samples': samples,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: analyze_decision_dump.py <path>')
        sys.exit(2)
    analyze(sys.argv[1])


################################################################################
# FROM: scripts\analyze_decision_dumps.py
################################################################################
#!/usr/bin/env python3
"""Analyser logs/decision_dumps.jsonl et proposer réglages.
Usage: python scripts/analyze_decision_dumps.py [path_to_jsonl]
"""
import sys
import json
from pathlib import Path
import statistics


def load_entries(path: Path):
    if not path.exists():
        print(f'No file: {path}')
        return []
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception as e:
                print('skip bad line:', e)
    return entries


def summarize(entries):
    rows = []
    for e in entries:
        d = e.get('decision', {})
        dm = d.get('decision_metrics') or {}
        conf = dm.get('confidence')
        thr = d.get('adaptive_threshold')
        if conf is None or thr is None:
            continue
        rows.append((conf, thr))

    if not rows:
        print('No usable entries')
        return

    diffs = [thr - conf for conf, thr in rows]
    positives = [d for d in diffs if d > 0]
    negatives = [d for d in diffs if d <= 0]

    print('Entries total:', len(rows))
    print('Would be accepted (conf >= thr):', len(negatives))
    print('Would be rejected (conf < thr):', len(positives))

    def pctile(xs, p):
        if not xs:
            return None
        k = max(0, min(len(xs)-1, int(len(xs)*p)))
        return sorted(xs)[k]

    print('Diffs (thr - conf) stats:')
    if diffs:
        print('  min:', min(diffs))
        print('  max:', max(diffs))
        print('  mean:', statistics.mean(diffs))
        print('  median:', statistics.median(diffs))
        print('  75pct:', pctile(diffs, 0.75))
        print('  90pct:', pctile(diffs, 0.90))

    # Recommender heuristics
    # If many rejections but small median gap, suggest small smoothing/clamp reduction
    if positives:
        med_gap = statistics.median(positives)
        mean_gap = statistics.mean(positives)
        print('\nRecommendation heuristics:')
        print(f'  median positive gap: {med_gap:.4f}, mean positive gap: {mean_gap:.4f}')
        # Suggest lowering base threshold by median gap/2 up to a cap
        suggested_lower = min(0.15, med_gap / 2)
        print(f'  Suggest lowering base_confidence_threshold by ≈ {suggested_lower:.3f} (or boosting smoothing by same)')
        # Also compute target threshold to accept X% of samples
        target_percent = 0.75
        # compute value v such that thr - conf <= v for target_percent of positives
        sorted_pos = sorted(positives)
        idx = int(len(sorted_pos) * target_percent) - 1
        idx = max(0, min(len(sorted_pos)-1, idx))
        target_v = sorted_pos[idx]
        print(f'  To accept ~{int(target_percent*100)}% of currently rejected samples, reduce threshold by ~{target_v:.3f}')
    else:
        print('\nNo positive gaps: current thresholds are permissive enough for all samples')


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('logs/decision_dumps.jsonl')
    entries = load_entries(path)
    summarize(entries)


if __name__ == '__main__':
    main()


################################################################################
# FROM: scripts\analyze_simple.py
################################################################################
"""Analyse simplifiée de la meilleure solution trouvée par auto-improve.

Ce script génère un rapport détaillé avec:
- Résumé des hyperparamètres et métriques
- Visualisations de performance de base
- Analyse des features importantes via LightGBM
- Recommendations pour améliorer le modèle
"""
from __future__ import annotations

import json
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path
from sklearn.metrics import confusion_matrix, classification_report


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


def analyze_feature_importance(model, X):
    """Analyse l'importance des features via LightGBM"""
    print("Analyse de l'importance des features...")

    # Obtenir les importances du modèle
    importance_gain = model.feature_importance(importance_type="gain")
    importance_split = model.feature_importance(importance_type="split")

    # Créer un DataFrame avec les importances
    feature_importance = pd.DataFrame(
        {
            "feature": X.columns,
            "importance_gain": importance_gain,
            "importance_split": importance_split,
        }
    ).sort_values("importance_gain", ascending=False)

    # Sauvegarder
    importance_dir = Path("artifacts/auto_improve/importance")
    importance_dir.mkdir(exist_ok=True)
    feature_importance.to_csv(
        importance_dir / "feature_importance.csv", index=False
    )

    # Graphique des top 15 features
    plt.figure(figsize=(12, 8))
    top_features = feature_importance.head(15)
    plt.barh(range(len(top_features)), top_features["importance_gain"])
    plt.yticks(range(len(top_features)), top_features["feature"])
    plt.xlabel("Importance (Gain)")
    plt.title("Top 15 Features les plus importantes")
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(
        importance_dir / "top_features.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

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
    plt.axvline(x=0.5, color="red", linestyle="--", label="Seuil")
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

    # 3. Performance dans le temps
    if len(df) > 100:
        window_size = max(50, len(df) // 20)
        periods = []
        accuracies = []

        for i in range(0, len(df) - window_size, window_size):
            y_window = y[i : i + window_size]
            pred_window = pred_labels[i : i + window_size]
            acc = (y_window == pred_window).mean()
            periods.append(i + window_size // 2)
            accuracies.append(acc)

        plt.figure(figsize=(12, 6))
        plt.plot(periods, accuracies, marker="o", linewidth=2)
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

    # 4. Distribution des erreurs
    correct_preds = predictions[y == pred_labels]
    wrong_preds = predictions[y != pred_labels]

    plt.figure(figsize=(10, 6))
    plt.hist(correct_preds, bins=30, alpha=0.7, label="Prédictions correctes")
    plt.hist(wrong_preds, bins=30, alpha=0.7, label="Prédictions incorrectes")
    plt.xlabel("Probabilité prédite")
    plt.ylabel("Fréquence")
    plt.title("Distribution des prédictions par résultat")
    plt.legend()
    plt.tight_layout()
    plt.savefig(plots_dir / "error_analysis.png", dpi=150, bbox_inches="tight")
    plt.close()


def generate_detailed_metrics(y, predictions, pred_labels):
    """Génère des métriques détaillées"""
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        f1_score,
        roc_auc_score,
    )

    metrics = {
        "accuracy": accuracy_score(y, pred_labels),
        "precision": precision_score(y, pred_labels),
        "recall": recall_score(y, pred_labels),
        "f1_score": f1_score(y, pred_labels),
        "roc_auc": roc_auc_score(y, predictions),
    }

    return metrics


def generate_report(best_config, backtest_report, feature_importance, metrics):
    """Génère le rapport final en markdown"""

    top_features_list = []
    for i, (_, row) in enumerate(feature_importance.head(10).iterrows()):
        top_features_list.append(
            f"{i+1}. {row['feature']}: {row['importance_gain']:.4f}"
        )

    report_content = f"""# Rapport d'analyse - Meilleure solution

## Configuration optimale

**Horizon de prédiction:** {best_config['horizon']} périodes

**Hyperparamètres LightGBM:**
- num_leaves: {best_config['params']['num_leaves']}
- learning_rate: {best_config['params']['learning_rate']}

## Performance de validation croisée

**Accuracy moyenne:** {best_config['mean_accuracy']:.4f} ± {best_config['std_accuracy']:.4f}

**Scores par fold:**
{chr(10).join([f"- Fold {i+1}: {score:.4f}" for i, score in enumerate(best_config['scores'])])}

## Métriques détaillées

**Métriques de classification:**
- Accuracy: {metrics['accuracy']:.4f}
- Precision: {metrics['precision']:.4f}
- Recall: {metrics['recall']:.4f}
- F1-Score: {metrics['f1_score']:.4f}
- ROC-AUC: {metrics['roc_auc']:.4f}

## Performance du backtest

**Métriques financières:**
- Rendement total: {backtest_report['total_return']:.4f} ({backtest_report['total_return']*100:.2f}%)
- Rendement moyen par tick: {backtest_report['avg_return_per_tick']:.6f}
- Taux de réussite: {backtest_report['win_rate']:.4f} ({backtest_report['win_rate']*100:.1f}%)
- Sharpe annualisé: {backtest_report['sharpe_annualized']:.2f}

## Features les plus importantes

Les 10 features les plus importantes selon LightGBM (gain):

{chr(10).join(top_features_list)}

## Analyse et recommandations

### Points positifs
- Le modèle dépasse le hasard (accuracy > 0.5)
- ROC-AUC de {metrics['roc_auc']:.3f} montre une capacité de discrimination
- Validation croisée temporelle robuste

### Points d'attention
- Taux de réussite modéré ({backtest_report['win_rate']*100:.1f}%)
- Variance entre folds CV significative
- Sharpe très élevée à vérifier

### Recommandations d'amélioration

1. **Optimisation du seuil:**
   - Tester différents seuils de décision
   - Optimiser selon métriques financières

2. **Engineering avancé:**
   - Features de volatilité
   - Indicateurs de régime de marché
   - Signaux multi-timeframes

3. **Modélisation:**
   - Ensemble de modèles
   - Régularisation adaptative
   - Optimisation bayésienne

4. **Validation:**
   - Walk-forward analysis
   - Tests de robustesse
   - Analyse des périodes de drawdown

## Fichiers générés

- `artifacts/auto_improve/importance/` - Analyse des features
- `artifacts/auto_improve/plots/` - Graphiques de performance
- `artifacts/auto_improve/best_lightgbm.txt` - Modèle entraîné

---
*Rapport généré le {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

    report_file = Path("artifacts/auto_improve/rapport_detaille.md")
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

        print(f"✅ Configuration chargée: horizon={best_config['horizon']}")

        # Charger le modèle et les données
        model, X, y, df = load_model_and_data(best_config)
        print(f"✅ Modèle et données chargés: {len(X)} échantillons")

        # Analyser l'importance des features
        feature_importance = analyze_feature_importance(model, X)
        print("✅ Analyse des features terminée")

        # Générer les métriques détaillées
        predictions = model.predict(X.values)
        pred_labels = (predictions > 0.5).astype(int)
        metrics = generate_detailed_metrics(y, predictions, pred_labels)
        print("✅ Métriques calculées")

        # Générer les graphiques de performance
        generate_performance_plots(df, model, X, y)
        print("✅ Graphiques générés")

        # Générer le rapport final
        generate_report(
            best_config, backtest_report, feature_importance, metrics
        )
        print("✅ Rapport détaillé généré")

        print("\n🎯 Analyse terminée! Consultez:")
        print("   - artifacts/auto_improve/rapport_detaille.md")
        print("   - artifacts/auto_improve/importance/")
        print("   - artifacts/auto_improve/plots/")

    except Exception as e:
        print(f"❌ Erreur lors de l'analyse: {e}")
        raise


if __name__ == "__main__":
    main()


# End of merged preview
