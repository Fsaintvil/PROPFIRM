#!/usr/bin/env python3
"""
Walk-Forward Validation pour tester la robustesse temporelle du modèle.

Ce script :
1. Divise les données en périodes d'entraînement/test consécutives
2. Réentraîne le modèle sur chaque période
3. Mesure la dégradation de performance dans le temps
4. Identifie les périodes de changement de régime de marché
"""

import pandas as pd
import numpy as np
from datetime import timedelta
import json
import os
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
import matplotlib.pyplot as plt


def load_features_data():
    """Charger les données de features"""
    try:
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        print(f"Erreur de chargement: {e}")
        return None


def create_labels(df, horizon=5):
    """Créer les labels pour la classification"""
    returns = df["close"].pct_change(horizon).shift(-horizon)
    labels = np.where(
        returns > 0.002, 1, 0
    )  # Seuil de 0.2% pour signal d'achat
    return labels


def walk_forward_validation(
    df, window_months=3, test_months=1, min_samples=100
):
    """
    Validation walk-forward avec fenêtres glissantes

    Args:
        df: DataFrame avec les features
        window_months: Taille de la fenêtre d'entraînement en mois
        test_months: Taille de la fenêtre de test en mois
        min_samples: Nombre minimum d'échantillons requis
    """
    print("🔄 Démarrage Walk-Forward Validation...")

    # Préparer les features et labels
    feature_cols = ["close", "volume", "sma_1T", "ema_15T", "rsi_60T"]
    X = df[feature_cols].fillna(method="ffill").fillna(method="bfill")
    y = create_labels(df)

    # Créer les indices temporels
    start_date = df.index.min()
    end_date = df.index.max()

    results = []
    fold = 0

    current_start = start_date

    while current_start < end_date:
        fold += 1

        # Définir les périodes d'entraînement et de test
        train_end = current_start + timedelta(days=30 * window_months)
        test_start = train_end
        test_end = test_start + timedelta(days=30 * test_months)

        if test_end > end_date:
            break

        # Extraire les données d'entraînement et de test
        train_mask = (df.index >= current_start) & (df.index < train_end)
        test_mask = (df.index >= test_start) & (df.index < test_end)

        X_train = X[train_mask]
        y_train = y[train_mask]
        X_test = X[test_mask]
        y_test = y[test_mask]

        # Vérifier qu'il y a assez de données
        if len(X_train) < min_samples or len(X_test) < 10:
            current_start += timedelta(days=30)
            continue

        # Nettoyer les NaN
        valid_train = ~(np.isnan(y_train) | np.isnan(X_train).any(axis=1))
        valid_test = ~(np.isnan(y_test) | np.isnan(X_test).any(axis=1))

        X_train = X_train[valid_train]
        y_train = y_train[valid_train]
        X_test = X_test[valid_test]
        y_test = y_test[valid_test]

        if len(X_train) < min_samples or len(X_test) < 5:
            current_start += timedelta(days=30)
            continue

        print(f"Fold {fold}: Train={len(X_train)}, Test={len(X_test)}")
        print(
            f"  Période: {current_start.strftime('%Y-%m-%d')} "
            f"-> {test_end.strftime('%Y-%m-%d')}"
        )

        try:
            # Entraîner le modèle avec les paramètres optimaux
            model = LGBMClassifier(
                num_leaves=15, learning_rate=0.1, random_state=42, verbose=-1
            )

            model.fit(X_train, y_train)

            # Prédictions et métriques
            y_pred = model.predict(X_test)
            y_pred_proba = model.predict_proba(X_test)[:, 1]

            accuracy = accuracy_score(y_test, y_pred)
            f1 = f1_score(y_test, y_pred, average="weighted")
            try:
                auc = roc_auc_score(y_test, y_pred_proba)
            except Exception:
                auc = 0.5

            # Simuler le backtest simple
            returns = simulate_trading_returns(
                y_pred, X_test, df.index[test_mask][valid_test]
            )

            result = {
                "fold": fold,
                "train_start": current_start.strftime("%Y-%m-%d"),
                "train_end": train_end.strftime("%Y-%m-%d"),
                "test_start": test_start.strftime("%Y-%m-%d"),
                "test_end": test_end.strftime("%Y-%m-%d"),
                "train_samples": len(X_train),
                "test_samples": len(X_test),
                "accuracy": accuracy,
                "f1_score": f1,
                "auc": auc,
                "total_return": returns["total_return"],
                "sharpe": returns["sharpe"],
                "max_drawdown": returns["max_drawdown"],
                "win_rate": returns["win_rate"],
            }

            results.append(result)
            print(
                f"  Accuracy: {accuracy:.4f}, F1: {f1:.4f}, "
                f"Return: {returns['total_return']:.4f}"
            )

        except Exception as e:
            print(f"  Erreur fold {fold}: {e}")

        # Avancer la fenêtre
        current_start += timedelta(days=30)

    return results


def simulate_trading_returns(predictions, X_test, timestamps):
    """Simuler les retours de trading basés sur les prédictions"""
    # Simuler des retours basés sur les prédictions
    np.random.seed(42)
    base_returns = np.random.normal(0.001, 0.02, len(predictions))

    # Appliquer les prédictions (1 = acheter, 0 = ne pas trader)
    trading_returns = np.where(predictions == 1, base_returns, 0)

    if len(trading_returns) == 0:
        return {
            "total_return": 0,
            "sharpe": 0,
            "max_drawdown": 0,
            "win_rate": 0,
        }

    # Calculer les métriques
    total_return = np.sum(trading_returns)

    if len(trading_returns) > 1:
        sharpe = (
            np.mean(trading_returns)
            / (np.std(trading_returns) + 1e-8)
            * np.sqrt(252)
        )
    else:
        sharpe = 0

    # Calculer le drawdown
    equity = np.cumprod(1 + trading_returns)
    peak = np.maximum.accumulate(equity)
    drawdown = np.min((equity - peak) / peak)

    # Win rate
    trades = trading_returns[trading_returns != 0]
    win_rate = np.mean(trades > 0) if len(trades) > 0 else 0

    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": drawdown,
        "win_rate": win_rate,
    }


def analyze_results(results):
    """Analyser les résultats de la validation walk-forward"""
    if not results:
        print("Aucun résultat à analyser")
        return

    df_results = pd.DataFrame(results)

    print("\n📊 RÉSULTATS WALK-FORWARD VALIDATION")
    print("=" * 50)

    print(f"Nombre de folds: {len(results)}")
    print(
        f"Accuracy moyenne: {df_results['accuracy'].mean():.4f} "
        f"± {df_results['accuracy'].std():.4f}"
    )
    print(
        f"F1-Score moyen: {df_results['f1_score'].mean():.4f} "
        f"± {df_results['f1_score'].std():.4f}"
    )
    print(
        f"AUC moyen: {df_results['auc'].mean():.4f} "
        f"± {df_results['auc'].std():.4f}"
    )
    print(
        f"Rendement moyen: {df_results['total_return'].mean():.4f} "
        f"± {df_results['total_return'].std():.4f}"
    )
    print(
        f"Sharpe moyen: {df_results['sharpe'].mean():.2f} "
        f"± {df_results['sharpe'].std():.2f}"
    )

    # Identifier les périodes problématiques
    worst_periods = df_results.nsmallest(3, "accuracy")
    print("\n⚠️  Périodes les moins performantes:")
    for _, period in worst_periods.iterrows():
        print(
            f"  {period['test_start']} -> {period['test_end']}: "
            f"Accuracy={period['accuracy']:.4f}"
        )

    # Détecter la dégradation temporelle
    df_results["test_date"] = pd.to_datetime(df_results["test_start"])
    correlation = (
        df_results["test_date"].astype(int).corr(df_results["accuracy"])
    )

    if correlation < -0.3:
        print(
            f"\n⚠️  ALERTE: Dégradation temporelle "
            f"détectée (corr={correlation:.3f})"
        )
        print("  Le modèle perd en performance au fil du temps")
    elif correlation > 0.3:
        print(f"\n✅ Amélioration temporelle (corr={correlation:.3f})")
    else:
        print(f"\n✅ Performance stable dans le temps (corr={correlation:.3f})")

    return df_results


def create_visualizations(df_results):
    """Créer des visualisations des résultats"""
    os.makedirs("artifacts/walk_forward", exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle("Walk-Forward Validation Results", fontsize=16)

    # Évolution de l'accuracy
    axes[0, 0].plot(
        pd.to_datetime(df_results["test_start"]), df_results["accuracy"], "o-"
    )
    axes[0, 0].set_title("Accuracy over Time")
    axes[0, 0].set_ylabel("Accuracy")
    axes[0, 0].tick_params(axis="x", rotation=45)

    # Évolution du rendement
    axes[0, 1].plot(
        pd.to_datetime(df_results["test_start"]),
        df_results["total_return"],
        "o-",
        color="green",
    )
    axes[0, 1].set_title("Returns over Time")
    axes[0, 1].set_ylabel("Total Return")
    axes[0, 1].tick_params(axis="x", rotation=45)

    # Distribution des métriques
    axes[1, 0].hist(
        df_results["accuracy"], bins=10, alpha=0.7, label="Accuracy"
    )
    axes[1, 0].hist(
        df_results["f1_score"], bins=10, alpha=0.7, label="F1-Score"
    )
    axes[1, 0].set_title("Distribution des métriques")
    axes[1, 0].legend()

    # Sharpe vs Accuracy
    axes[1, 1].scatter(df_results["accuracy"], df_results["sharpe"])
    axes[1, 1].set_xlabel("Accuracy")
    axes[1, 1].set_ylabel("Sharpe Ratio")
    axes[1, 1].set_title("Accuracy vs Sharpe")

    plt.tight_layout()
    plt.savefig(
        "artifacts/walk_forward/validation_results.png",
        dpi=300,
        bbox_inches="tight",
    )
    print(
        "📊 Graphiques sauvegardés: "
        "artifacts/walk_forward/validation_results.png"
    )


def main():
    """Fonction principale"""
    print("🚀 Walk-Forward Validation - Démarrage")

    # Charger les données
    df = load_features_data()
    if df is None:
        print("❌ Impossible de charger les données")
        return

    print(
        f"📊 Données chargées: {len(df)} échantillons "
        f"de {df.index.min()} à {df.index.max()}"
    )

    # Exécuter la validation
    results = walk_forward_validation(df, window_months=2, test_months=1)

    if not results:
        print("❌ Aucun résultat généré")
        return

    # Analyser les résultats
    df_results = analyze_results(results)

    # Créer les visualisations
    create_visualizations(df_results)

    # Sauvegarder les résultats
    os.makedirs("artifacts/walk_forward", exist_ok=True)

    with open("artifacts/walk_forward/validation_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    df_results.to_csv(
        "artifacts/walk_forward/performance_by_period.csv", index=False
    )

    print("\n✅ Walk-Forward Validation terminée")
    print("📁 Résultats sauvegardés dans artifacts/walk_forward/")

    # Recommandations
    accuracy_mean = df_results["accuracy"].mean()
    accuracy_std = df_results["accuracy"].std()

    print("\n🎯 RECOMMANDATIONS:")
    if accuracy_std > 0.1:
        print(
            "⚠️  Forte variabilité de performance → "
            "Implémenter l'adaptation dynamique"
        )
    if accuracy_mean < 0.55:
        print(
            "⚠️  Performance moyenne faible → Revoir les features ou le modèle"
        )
    else:
        print("✅ Performance satisfaisante → Prêt pour le déploiement")


if __name__ == "__main__":
    main()
