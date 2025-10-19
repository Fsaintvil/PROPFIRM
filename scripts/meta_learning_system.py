#!/usr/bin/env python3
"""
Meta-Learning & AutoML System pour optimisation automatique des modèles.

Ce système:
- Teste automatiquement différentes architectures
  (LightGBM, XGBoost, CatBoost, RandomForest, Neural Networks)
- Optimise les hyperparamètres avec Bayesian Optimization
- Sélectionne automatiquement les meilleures features
- S'adapte aux changements de marché en temps réel
- Maintient un ensemble évolutif de modèles
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import warnings
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.feature_selection import SelectKBest, f_classif, RFE
from sklearn.model_selection import cross_val_score
from sklearn.metrics import accuracy_score

warnings.filterwarnings("ignore")

# Optimization avec fallback robuste
try:
    from skopt import gp_minimize
    from skopt.space import Real, Integer, Categorical
    from skopt.utils import use_named_args

    BAYESIAN_OPT_AVAILABLE = True
    print("✅ scikit-optimize disponible - optimisation bayésienne activée")
except ImportError as e:
    BAYESIAN_OPT_AVAILABLE = False
    print(f"⚠️  scikit-optimize non disponible: {e}")
    print("🔄 Optimisation par grid search basique activée")

    # Définir des classes fallback simples
    class Real:
        def __init__(self, low, high):
            self.low, self.high = low, high

        def rvs(self, random_state=None):
            np.random.seed(random_state)
            return [np.random.uniform(self.low, self.high)]

    class Integer:
        def __init__(self, low, high):
            self.low, self.high = low, high

        def rvs(self, random_state=None):
            np.random.seed(random_state)
            return [np.random.randint(self.low, self.high + 1)]

    class Categorical:
        def __init__(self, choices):
            self.choices = choices

        def rvs(self, random_state=None):
            np.random.seed(random_state)
            return [np.random.choice(self.choices)]

    print("⚠️  Skopt non disponible - Utilisation d'optimisation basique")

try:
    from catboost import CatBoostClassifier

    CATBOOST_AVAILABLE = True
except ImportError:
    CATBOOST_AVAILABLE = False


class MetaLearningTradingSystem:
    """Système de Meta-Learning pour trading automatique"""

    def __init__(self, max_models=10, performance_window=100):
        """
        Args:
            max_models: Nombre maximum de modèles dans l'ensemble évolutif
            performance_window: Fenêtre pour évaluer la performance récente
        """
        self.max_models = max_models
        self.performance_window = performance_window

        # Ensemble évolutif de modèles
        self.model_ensemble = []
        self.model_performances = []
        self.feature_importance_history = []

        # Métriques de performance
        self.performance_history = []
        self.optimization_history = []

        print("🧠 MetaLearningTradingSystem initialisé")
        print(f"  📊 Ensemble max: {max_models} modèles")
        print(f"  ⏰ Fenêtre perf: {performance_window} échantillons")

    def get_model_architectures(self):
        """Définir les différentes architectures de modèles à tester"""
        architectures = {
            "lightgbm_fast": {
                "model_class": LGBMClassifier,
                "param_space": {
                    "num_leaves": Integer(10, 50),
                    "learning_rate": Real(0.05, 0.3),
                    "n_estimators": Integer(50, 200),
                    "max_depth": Integer(3, 8),
                },
                "fixed_params": {"random_state": 42, "verbose": -1},
            },
            "lightgbm_deep": {
                "model_class": LGBMClassifier,
                "param_space": {
                    "num_leaves": Integer(50, 150),
                    "learning_rate": Real(0.01, 0.1),
                    "n_estimators": Integer(200, 800),
                    "max_depth": Integer(6, 15),
                },
                "fixed_params": {"random_state": 42, "verbose": -1},
            },
            "xgboost": {
                "model_class": XGBClassifier,
                "param_space": {
                    "max_depth": Integer(3, 10),
                    "learning_rate": Real(0.01, 0.3),
                    "n_estimators": Integer(50, 300),
                    "subsample": Real(0.7, 1.0),
                },
                "fixed_params": {"random_state": 42, "verbosity": 0},
            },
            "random_forest": {
                "model_class": RandomForestClassifier,
                "param_space": {
                    "n_estimators": Integer(50, 300),
                    "max_depth": Integer(5, 20),
                    "min_samples_split": Integer(2, 10),
                    "min_samples_leaf": Integer(1, 5),
                },
                "fixed_params": {"random_state": 42},
            },
            "extra_trees": {
                "model_class": ExtraTreesClassifier,
                "param_space": {
                    "n_estimators": Integer(50, 200),
                    "max_depth": Integer(5, 15),
                    "min_samples_split": Integer(2, 8),
                    "max_features": Categorical(["sqrt", "log2", 0.5, 0.8]),
                },
                "fixed_params": {"random_state": 42},
            },
            "neural_network": {
                "model_class": MLPClassifier,
                "param_space": {
                    "hidden_layer_sizes": Categorical(
                        [(50,), (100,), (50, 25), (100, 50), (100, 50, 25)]
                    ),
                    "learning_rate_init": Real(0.001, 0.1),
                    "alpha": Real(0.0001, 0.01),
                    "max_iter": Integer(200, 1000),
                },
                "fixed_params": {"random_state": 42, "solver": "adam"},
            },
        }

        # Ajouter CatBoost si disponible
        if CATBOOST_AVAILABLE:
            architectures["catboost"] = {
                "model_class": CatBoostClassifier,
                "param_space": {
                    "depth": Integer(4, 10),
                    "learning_rate": Real(0.01, 0.3),
                    "iterations": Integer(100, 500),
                },
                "fixed_params": {"random_state": 42, "verbose": False},
            }

        return architectures

    def optimize_hyperparameters(
        self, X, y, architecture_name, architecture_config, n_calls=20
    ):
        """Optimiser les hyperparamètres avec Bayesian Optimization"""

        if not BAYESIAN_OPT_AVAILABLE:
            # Fallback: test quelques combinaisons aléatoirement
            return self._random_hyperparameter_search(
                X, y, architecture_config
            )

        print(f"  🔧 Optimisation Bayésienne pour {architecture_name}...")

        # Définir la fonction objective
        space = list(architecture_config["param_space"].values())

        best_score = 0
        best_params = {}

        @use_named_args(architecture_config["param_space"])
        def objective(**params):
            nonlocal best_score, best_params

            try:
                # Créer le modèle avec les paramètres
                model_params = {
                    **architecture_config["fixed_params"],
                    **params,
                }
                model = architecture_config["model_class"](**model_params)

                # Cross-validation rapide
                cv_scores = cross_val_score(
                    model, X, y, cv=3, scoring="accuracy"
                )
                score = cv_scores.mean()

                # Garder les meilleurs paramètres
                if score > best_score:
                    best_score = score
                    best_params = params.copy()

                # Retourner l'inverse pour minimisation
                return -score

            except Exception as e:
                print(f"    Erreur params {params}: {e}")
                return 0  # Score neutre en cas d'erreur

        # Optimisation Bayésienne
        try:
            gp_minimize(
                func=objective,
                dimensions=space,
                n_calls=n_calls,
                random_state=42,
                acq_func="EI",
            )

            optimized_params = {
                **architecture_config["fixed_params"],
                **best_params,
            }

        except Exception as e:
            print(f"    Erreur optimisation: {e}")
            # Fallback aux paramètres par défaut
            optimized_params = architecture_config["fixed_params"]
            best_score = 0.5

        return optimized_params, best_score

    def _random_hyperparameter_search(
        self, X, y, architecture_config, n_trials=10
    ):
        """Recherche aléatoire d'hyperparamètres (fallback)"""
        print("  🎲 Recherche aléatoire d'hyperparamètres...")

        best_score = 0
        best_params = architecture_config["fixed_params"].copy()

        for trial in range(n_trials):
            try:
                # Générer des paramètres aléatoirement
                trial_params = architecture_config["fixed_params"].copy()

                for param_name, param_space in architecture_config[
                    "param_space"
                ].items():
                    if hasattr(param_space, "rvs"):
                        # Skopt space
                        trial_params[param_name] = param_space.rvs(
                            random_state=42 + trial
                        )[0]
                    else:
                        # Fallback manuel
                        if param_name == "num_leaves":
                            trial_params[param_name] = np.random.choice(
                                [15, 31, 63]
                            )
                        elif param_name == "learning_rate":
                            trial_params[param_name] = np.random.choice(
                                [0.05, 0.1, 0.2]
                            )
                        elif param_name == "n_estimators":
                            trial_params[param_name] = np.random.choice(
                                [50, 100, 200]
                            )

                # Tester les paramètres
                model = architecture_config["model_class"](**trial_params)
                cv_scores = cross_val_score(
                    model, X, y, cv=3, scoring="accuracy"
                )
                score = cv_scores.mean()

                if score > best_score:
                    best_score = score
                    best_params = trial_params.copy()

            except Exception:
                continue

        return best_params, best_score

    def feature_selection_pipeline(self, X, y, method="auto"):
        """Pipeline de sélection de features avancé"""
        print("  📊 Sélection automatique de features...")

        if len(X.columns) <= 10:
            return X  # Pas besoin de sélection si peu de features

        results = {}

        # 1. Filter method: SelectKBest
        try:
            k_best = min(20, len(X.columns) // 2)
            selector_kbest = SelectKBest(f_classif, k=k_best)
            X_kbest = selector_kbest.fit_transform(X, y)

            # Tester avec un modèle rapide
            quick_model = LGBMClassifier(num_leaves=15, verbose=-1)
            cv_score_kbest = cross_val_score(
                quick_model, X_kbest, y, cv=3
            ).mean()

            results["kbest"] = {
                "score": cv_score_kbest,
                "features": X.columns[selector_kbest.get_support()].tolist(),
            }

        except Exception as e:
            print(f"    Erreur SelectKBest: {e}")

        # 2. Wrapper method: RFE
        try:
            n_features_rfe = min(15, len(X.columns) // 3)
            estimator_rfe = LGBMClassifier(
                num_leaves=10, n_estimators=50, verbose=-1
            )
            selector_rfe = RFE(
                estimator_rfe, n_features_to_select=n_features_rfe
            )
            X_rfe = selector_rfe.fit_transform(X, y)

            cv_score_rfe = cross_val_score(quick_model, X_rfe, y, cv=3).mean()

            results["rfe"] = {
                "score": cv_score_rfe,
                "features": X.columns[selector_rfe.support_].tolist(),
            }

        except Exception as e:
            print(f"    Erreur RFE: {e}")

        # 3. Embedded method: Feature importance
        try:
            importance_model = LGBMClassifier(num_leaves=20, verbose=-1)
            importance_model.fit(X, y)

            feature_importance = pd.Series(
                importance_model.feature_importances_, index=X.columns
            ).sort_values(ascending=False)

            # Prendre les top features
            top_features = feature_importance.head(
                min(18, len(X.columns) // 2)
            ).index.tolist()
            X_importance = X[top_features]

            cv_score_importance = cross_val_score(
                quick_model, X_importance, y, cv=3
            ).mean()

            results["importance"] = {
                "score": cv_score_importance,
                "features": top_features,
            }

        except Exception as e:
            print(f"    Erreur Feature Importance: {e}")

        # Sélectionner la meilleure méthode
        if results:
            best_method = max(
                results.keys(), key=lambda k: results[k]["score"]
            )
            best_features = results[best_method]["features"]

            print(
                f"    ✅ Méthode {best_method}: {len(best_features)} features, "
                f"score={results[best_method]['score']:.4f}"
            )

            return X[best_features]
        else:
            print("    ⚠️  Échec sélection features - Conservation de toutes")
            return X

    def create_labels_advanced(self, df, method="multi_horizon"):
        """Création de labels avancés avec plusieurs horizons"""
        if method == "multi_horizon":
            # Combiner plusieurs horizons pour plus de robustesse
            returns_3 = df["close"].pct_change(3).shift(-3)
            returns_5 = df["close"].pct_change(5).shift(-5)
            returns_7 = df["close"].pct_change(7).shift(-7)

            # Signal si au moins 2 horizons sont positifs
            signals = np.array(
                [
                    (returns_3 > 0.001).astype(int),
                    (returns_5 > 0.002).astype(int),
                    (returns_7 > 0.003).astype(int),
                ]
            )

            # Majorité vote
            labels = (signals.sum(axis=0) >= 2).astype(int)

        else:
            # Méthode simple
            returns = df["close"].pct_change(5).shift(-5)
            labels = np.where(returns > 0.002, 1, 0)

        return labels

    def auto_model_discovery(self, X, y):
        """Découverte automatique du meilleur modèle"""
        print("🤖 Auto-découverte de modèles...")

        architectures = self.get_model_architectures()
        model_results = []

        for arch_name, arch_config in architectures.items():
            print(f"\n  🔍 Test architecture: {arch_name}")

            try:
                # Optimiser les hyperparamètres
                best_params, best_score = self.optimize_hyperparameters(
                    X, y, arch_name, arch_config, n_calls=15
                )

                # Créer le modèle final
                model = arch_config["model_class"](**best_params)

                # Évaluation complète
                cv_scores = cross_val_score(
                    model, X, y, cv=5, scoring="accuracy"
                )

                result = {
                    "architecture": arch_name,
                    "model": model,
                    "params": best_params,
                    "cv_accuracy_mean": cv_scores.mean(),
                    "cv_accuracy_std": cv_scores.std(),
                    "cv_scores": cv_scores.tolist(),
                }

                model_results.append(result)

                print(
                    f"    ✅ {arch_name}: {cv_scores.mean():.4f} "
                    f"± {cv_scores.std():.4f}"
                )

            except Exception as e:
                print(f"    ❌ {arch_name}: Erreur - {e}")
                continue

        # Trier par performance
        model_results.sort(key=lambda x: x["cv_accuracy_mean"], reverse=True)

        return model_results

    def evolutionary_ensemble_update(self, new_model_results):
        """Mise à jour évolutive de l'ensemble"""
        print("🧬 Mise à jour évolutive de l'ensemble...")

        # Ajouter les nouveaux modèles
        for result in new_model_results:
            self.model_ensemble.append(
                {
                    "model": result["model"],
                    "architecture": result["architecture"],
                    "params": result["params"],
                    "performance": result["cv_accuracy_mean"],
                    "timestamp": datetime.now(),
                }
            )

        # Trier par performance
        self.model_ensemble.sort(key=lambda x: x["performance"], reverse=True)

        # Garder seulement les meilleurs modèles
        if len(self.model_ensemble) > self.max_models:
            removed_models = self.model_ensemble[self.max_models:]
            self.model_ensemble = self.model_ensemble[: self.max_models]

            print(
                f"  🗑️  Suppression de {len(removed_models)} "
                f"modèles moins performants"
            )

        print(f"  📊 Ensemble actuel: {len(self.model_ensemble)} modèles")

        # Afficher le top 3
        for i, model_info in enumerate(self.model_ensemble[:3]):
            print(
                f"    {i+1}. {model_info['architecture']}: "
                f"{model_info['performance']:.4f}"
            )

    def ensemble_predict(self, X, method="weighted_average"):
        """Prédiction avec l'ensemble évolutif"""
        if not self.model_ensemble:
            raise ValueError("Aucun modèle dans l'ensemble")

        predictions = []
        weights = []

        for model_info in self.model_ensemble:
            try:
                model = model_info["model"]

                if hasattr(model, "predict_proba"):
                    pred_proba = model.predict_proba(X)[:, 1]
                else:
                    pred_proba = model.predict(X).astype(float)

                predictions.append(pred_proba)
                weights.append(model_info["performance"])

            except Exception as e:
                print(f"Erreur prédiction {model_info['architecture']}: {e}")
                continue

        if not predictions:
            raise ValueError("Aucune prédiction valide")

        # Combinaison pondérée
        predictions_array = np.array(predictions)
        weights_array = np.array(weights)
        weights_normalized = weights_array / weights_array.sum()

        ensemble_pred = np.average(
            predictions_array, axis=0, weights=weights_normalized
        )

        return ensemble_pred

    def adaptive_retraining(self, X, y, performance_threshold=0.55):
        """Réentraînement adaptatif basé sur la performance"""
        print("🔄 Évaluation du besoin de réentraînement...")

        if not self.model_ensemble:
            print("  ✅ Premier entraînement nécessaire")
            return True

        # Tester la performance actuelle sur les nouvelles données
        try:
            ensemble_pred = self.ensemble_predict(X)
            ensemble_pred_binary = (ensemble_pred >= 0.5).astype(int)

            current_accuracy = accuracy_score(y, ensemble_pred_binary)

            print(f"  📊 Performance actuelle: {current_accuracy:.4f}")

            if current_accuracy < performance_threshold:
                print(
                    f"  ⚠️  Performance dégradée < {performance_threshold} "
                    f"- Réentraînement nécessaire"
                )
                return True
            else:
                print(
                    f"  ✅ Performance satisfaisante "
                    f"> {performance_threshold}"
                )
                return False

        except Exception as e:
            print(
                f"  ⚠️  Erreur évaluation: {e} - "
                f"Réentraînement par sécurité"
            )
            return True

    def full_auto_optimization(self, df):
        """Pipeline complet d'auto-optimisation"""
        print("🚀 PIPELINE COMPLET AUTO-OPTIMISATION")
        print("=" * 50)

        # Charger features avancées si disponibles
        try:
            enhanced_df = pd.read_csv("data/features_enhanced.csv")
            if "Unnamed: 0" in enhanced_df.columns:
                enhanced_df = enhanced_df.set_index("Unnamed: 0")
            print("✅ Features avancées chargées")
            df = enhanced_df
        except Exception:
            print("⚠️  Utilisation des features de base")

        # Préparer les données
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        X_full = df[numeric_cols].fillna(method="ffill").fillna(0)
        y = self.create_labels_advanced(df)

        # Nettoyer
        valid_mask = ~np.isnan(y)
        X_full = X_full[valid_mask]
        y = y[valid_mask]

        print(
            f"📊 Données: {len(X_full)} échantillons, "
            f"{len(X_full.columns)} features"
        )

        # Sélection de features
        X_selected = self.feature_selection_pipeline(X_full, y)

        # Vérifier si réentraînement nécessaire
        need_retrain = self.adaptive_retraining(X_selected, y)

        if need_retrain:
            # Auto-découverte de modèles
            model_results = self.auto_model_discovery(X_selected, y)

            # Mise à jour évolutive de l'ensemble
            if model_results:
                self.evolutionary_ensemble_update(model_results)

                # Sauvegarder l'état
                self.save_system_state()

                return {
                    "retrained": True,
                    "best_models": model_results[:3],
                    "ensemble_size": len(self.model_ensemble),
                    "selected_features": X_selected.columns.tolist(),
                }
            else:
                print("❌ Aucun modèle valide trouvé")
                return {"retrained": False, "error": "no_valid_models"}
        else:
            return {
                "retrained": False,
                "reason": "performance_satisfactory",
                "ensemble_size": len(self.model_ensemble),
            }

    def save_system_state(self):
        """Sauvegarder l'état du système"""
        os.makedirs("artifacts/meta_learning", exist_ok=True)

        # Sauvegarder métadonnées (sans modèles sklearn)
        state_metadata = {
            "timestamp": datetime.now().isoformat(),
            "ensemble_size": len(self.model_ensemble),
            "ensemble_info": [
                {
                    "architecture": model["architecture"],
                    "performance": model["performance"],
                    "timestamp": model["timestamp"].isoformat(),
                }
                for model in self.model_ensemble
            ],
            "optimization_history": self.optimization_history,
            "performance_history": self.performance_history,
        }

        with open("artifacts/meta_learning/system_state.json", "w") as f:
            json.dump(state_metadata, f, indent=2, default=str)

        print("💾 État du système sauvegardé")


def main():
    """Test du système de Meta-Learning"""
    print("🧠 TEST SYSTÈME META-LEARNING")
    print("=" * 40)

    try:
        # Charger les données
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        # Créer le système
        meta_system = MetaLearningTradingSystem(max_models=5)

        # Lancer l'auto-optimisation
        result = meta_system.full_auto_optimization(df)

        print("\n🎯 RÉSULTATS AUTO-OPTIMISATION:")
        print("=" * 40)

        if result.get("retrained"):
            print("✅ Système réentraîné avec succès")
            print(f"📊 Ensemble: {result['ensemble_size']} modèles")
            print(
                f"🔧 Features: {len(result['selected_features'])} "
                f"sélectionnées"
            )

            print("\n🏆 TOP 3 MODÈLES:")
            for i, model_info in enumerate(result["best_models"]):
                print(
                    f"  {i+1}. {model_info['architecture']}: "
                    f"{model_info['cv_accuracy_mean']:.4f}"
                )

        else:
            reason = result.get("reason", "unknown")
            print(f"⏭️  Pas de réentraînement: {reason}")

        print("\n✅ Meta-Learning terminé")

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
