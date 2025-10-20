#!/usr/bin/env python3
"""
Ensemble de modèles pour améliorer la robustesse et performance.

Combine LightGBM + XGBoost + CatBoost avec pondération adaptative.
"""

import pandas as pd
import numpy as np
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier

try:
    from catboost import CatBoostClassifier

    CATBOOST_AVAILABLE = True
except ImportError:
    CATBOOST_AVAILABLE = False
    print("CatBoost non disponible, utilisation de LightGBM + XGBoost")

from sklearn.ensemble import VotingClassifier
from sklearn.model_selection import cross_val_score
from sklearn.metrics import accuracy_score, f1_score
import json
import os
from datetime import datetime


def create_ensemble_models():
    """Créer les modèles individuels avec paramètres optimisés"""
    models = {}

    # LightGBM (modèle principal optimisé)
    models["lightgbm"] = LGBMClassifier(
        num_leaves=15, learning_rate=0.1, random_state=42, verbose=-1
    )

    # XGBoost (paramètres similaires)
    models["xgboost"] = XGBClassifier(
        max_depth=4,  # approximativement équivalent num_leaves=15
        learning_rate=0.1,
            random_state=42,
                verbosity=0,
                )

    # CatBoost si disponible
    if CATBOOST_AVAILABLE:
        models["catboost"] = CatBoostClassifier(
            depth=4, learning_rate=0.1, random_state=42, verbose=False
        )

    return models


def evaluate_individual_models(X, y, models):
    """Évaluer chaque modèle individuellement"""
    print("🔍 Évaluation des modèles individuels...")

    results = {}

    for name, model in models.items():
        print(f"  Évaluation {name}...")

        try:
            # Validation croisée temporelle (3 folds)
            cv_scores = cross_val_score(model, X, y, cv=3, scoring="accuracy")

            results[name] = {
                "cv_accuracy_mean": cv_scores.mean(),
                    "cv_accuracy_std": cv_scores.std(),
                        "cv_scores": cv_scores.tolist(),
                        }

            print(
                f"    {name}: {cv_scores.mean():.4f} "
                f"± {cv_scores.std():.4f}"
            )

        except Exception as e:
            print(f"    Erreur {name}: {e}")
            results[name] = {"error": str(e)}

    return results


def create_adaptive_ensemble(X_train, y_train, models, method="weighted"):
    """
    Créer un ensemble adaptatif

    Args:
        method: 'weighted', 'voting', 'stacking'
    """
    if method == "weighted":
        return create_weighted_ensemble(X_train, y_train, models)
    elif method == "voting":
        return create_voting_ensemble(models)
    else:
        raise ValueError(f"Méthode non supportée: {method}")


def create_weighted_ensemble(X_train, y_train, models):
    """Créer un ensemble avec pondération basée sur la performance"""
    print("🔧 Création de l'ensemble pondéré...")

    # Évaluer chaque modèle pour déterminer les poids
    weights = {}
    performances = {}

    for name, model in models.items():
        try:
            # Entraîner et évaluer sur un subset
            n_samples = min(len(X_train), 1000)  # Éviter surcharge
            idx = np.random.choice(len(X_train), n_samples, replace=False)

            X_subset = X_train.iloc[idx]
            y_subset = y_train[idx]

            # Split train/val pour évaluation
            split_idx = int(0.7 * len(X_subset))
            X_train_sub = X_subset.iloc[:split_idx]
            y_train_sub = y_subset[:split_idx]
            X_val_sub = X_subset.iloc[split_idx:]
            y_val_sub = y_subset[split_idx:]

            # Entraîner et prédire
            model.fit(X_train_sub, y_train_sub)
            y_pred = model.predict(X_val_sub)

            # Calculer performance
            accuracy = accuracy_score(y_val_sub, y_pred)
            f1 = f1_score(y_val_sub, y_pred, average="weighted")

            # Score composite
            composite_score = 0.7 * accuracy + 0.3 * f1

            weights[name] = composite_score
            performances[name] = {
                "accuracy": accuracy,
                    "f1": f1,
                        "composite": composite_score,
                        }

            print(f"  {name}: Accuracy={accuracy:.4f}, F1={f1:.4f}")

        except Exception as e:
            print(f"  Erreur {name}: {e}")
            weights[name] = 0.1  # Poids minimal en cas d'erreur
            performances[name] = {"error": str(e)}

    # Normaliser les poids
    total_weight = sum(weights.values())
    if total_weight > 0:
        weights = {k: v / total_weight for k, v in weights.items()}
    else:
        # Poids égaux si tous échouent
        weights = {k: 1.0 / len(models) for k in models.keys()}

    print(f"  Poids finaux: {weights}")

    return WeightedEnsemble(models, weights), performances


def create_voting_ensemble(models):
    """Créer un ensemble par vote majoritaire"""
    print("🗳️  Création de l'ensemble par vote...")

    model_list = [(name, model) for name, model in models.items()]

    voting_clf = VotingClassifier(
        estimators=model_list, voting="soft"  # Utiliser les probabilités
    )

    return voting_clf, {}


class WeightedEnsemble:
    """Ensemble personnalisé avec pondération adaptative"""

    def __init__(self, models, weights):
        self.models = models
        self.weights = weights
        self.fitted_models = {}

    def fit(self, X, y):
        """Entraîner tous les modèles"""
        print("🏋️  Entraînement de l'ensemble...")

        for name, model in self.models.items():
            try:
                print(f"  Entraînement {name}...")
                fitted_model = model.fit(X, y)
                self.fitted_models[name] = fitted_model
            except Exception as e:
                print(f"  Erreur entraînement {name}: {e}")

    def predict(self, X):
        """Prédiction pondérée"""
        if not self.fitted_models:
            raise ValueError("Modèle non entraîné")

        predictions = {}

        # Obtenir les prédictions de chaque modèle
        for name, model in self.fitted_models.items():
            try:
                pred = model.predict(X)
                predictions[name] = pred
            except Exception as e:
                print(f"Erreur prédiction {name}: {e}")
                # Prédiction par défaut
                predictions[name] = np.zeros(len(X))

        if not predictions:
            return np.zeros(len(X))

        # Pondération des prédictions
        final_pred = np.zeros(len(X))

        for name, pred in predictions.items():
            weight = self.weights.get(name, 0)
            final_pred += weight * pred

        # Seuillage binaire
        return (final_pred >= 0.5).astype(int)

    def predict_proba(self, X):
        """Prédiction de probabilités pondérées"""
        if not self.fitted_models:
            raise ValueError("Modèle non entraîné")

        probas = {}

        # Obtenir les probabilités de chaque modèle
        for name, model in self.fitted_models.items():
            try:
                if hasattr(model, "predict_proba"):
                    proba = model.predict_proba(X)[:, 1]  # Classe positive
                else:
                    # Fallback pour modèles sans predict_proba
                    pred = model.predict(X)
                    proba = pred.astype(float)

                probas[name] = proba
            except Exception as e:
                print(f"Erreur proba {name}: {e}")
                probas[name] = np.full(len(X), 0.5)

        if not probas:
            return np.column_stack(
                [np.full(len(X), 0.5), np.full(len(X), 0.5)]
            )

        # Pondération des probabilités
        final_proba = np.zeros(len(X))

        for name, proba in probas.items():
            weight = self.weights.get(name, 0)
            final_proba += weight * proba

        # Retourner format sklearn [proba_classe_0, proba_classe_1]
        return np.column_stack([1 - final_proba, final_proba])


def load_and_prepare_data():
    """Charger et préparer les données"""
    try:
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        # Features
        feature_cols = ["close", "volume", "sma_1T", "ema_15T", "rsi_60T"]
        X = df[feature_cols].fillna(method="ffill").fillna(method="bfill")

        # Labels
        returns = df["close"].pct_change(5).shift(-5)
        y = np.where(returns > 0.002, 1, 0)

        # Nettoyer les NaN
        valid_mask = ~(np.isnan(y) | np.isnan(X).any(axis=1))
        X = X[valid_mask]
        y = y[valid_mask]

        return X, y, df.index[valid_mask]

    except Exception as e:
        print(f"Erreur chargement données: {e}")
        return None, None, None


def evaluate_ensemble(ensemble, X_test, y_test):
    """Évaluer l'ensemble"""
    try:
        y_pred = ensemble.predict(X_test)
        y_proba = ensemble.predict_proba(X_test)[:, 1]

        accuracy = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred, average="weighted")

        return {
            "accuracy": accuracy,
                "f1_score": f1,
                    "predictions": y_pred.tolist(),
                    "probabilities": y_proba.tolist(),
                    }
    except Exception as e:
        print(f"Erreur évaluation ensemble: {e}")
        return {"error": str(e)}


def main():
    """Fonction principale"""
    print("🚀 Création de l'ensemble de modèles")
    print("=" * 50)

    # Charger les données
    X, y, timestamps = load_and_prepare_data()
    if X is None:
        print("❌ Impossible de charger les données")
        return

    print(f"📊 Données: {len(X)} échantillons, {X.shape[1]} features")

    # Créer les modèles
    models = create_ensemble_models()
    print(f"🤖 Modèles disponibles: {list(models.keys())}")

    # Split train/test temporel
    split_idx = int(0.8 * len(X))
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    print(f"📈 Train: {len(X_train)}, Test: {len(X_test)}")

    # Évaluer modèles individuels
    individual_results = evaluate_individual_models(X_train, y_train, models)

    # Créer l'ensemble pondéré
    ensemble, performances = create_adaptive_ensemble(
        X_train, y_train, models, method="weighted"
    )

    # Entraîner l'ensemble
    ensemble.fit(X_train, y_train)

    # Évaluer l'ensemble
    ensemble_results = evaluate_ensemble(ensemble, X_test, y_test)

    print("\n🎯 RÉSULTATS ENSEMBLE:")
    print(f"Accuracy: {ensemble_results.get('accuracy', 0):.4f}")
    print(f"F1-Score: {ensemble_results.get('f1_score', 0):.4f}")

    # Sauvegarder les résultats
    os.makedirs("artifacts/ensemble", exist_ok=True)

    results = {
        "timestamp": datetime.now().isoformat(),
            "individual_models": individual_results,
                "ensemble_performance": performances,
                "ensemble_evaluation": ensemble_results,
                "model_weights": getattr(ensemble, "weights", {}),
                }

    with open("artifacts/ensemble/ensemble_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print("\n✅ Ensemble créé et évalué")
    print("📁 Résultats: artifacts/ensemble/ensemble_results.json")

    # Recommandations
    best_individual = max(
        individual_results.items(),
            key=lambda x: x[1].get("cv_accuracy_mean", 0),
                )
    ensemble_acc = ensemble_results.get("accuracy", 0)
    best_individual_acc = best_individual[1].get("cv_accuracy_mean", 0)

    if ensemble_acc > best_individual_acc:
        improvement = (ensemble_acc - best_individual_acc) * 100
        print(
            f"🎉 Amélioration: +{improvement:.2f}% "
            f"vs meilleur modèle individuel"
        )
    else:
        print(
            "⚠️  Ensemble moins performant - "
            "Utiliser le meilleur modèle individuel"
        )


if __name__ == "__main__":
    main()
