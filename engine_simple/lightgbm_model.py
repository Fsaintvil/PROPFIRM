"""LightGBM Model — prédiction +1 ATR avant -1 ATR.

Utilise les features de feature_pipeline.py pour prédire si un trade sera
gagnant (profit > 0) sur les N prochaines bougies.

Le modèle est entraîné sur les 158 964 trades backtest, puis validé
sur les trades réels. En production, il vote en parallèle de MOM20x3.

Flux:
  1. scripts/train_lightgbm.py  →  entraîne et sauvegarde runtime/lgb_model.txt
  2. main.py / signal_pipeline  →  compute_features() → model.predict()
  3. Résultat : proba_gagnant + confiance → ajuste score/risque
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("lightgbm_model")

# ─── Feature columns (ordre d'entrée du modèle) ──────────────────────────────
# Ces 20 features sont les plus importants pour la prédiction
# (basé sur la littérature quant + analyse des 158K trades backtest)
FEATURE_COLUMNS: list[str] = [
    # 1. Price Action
    "return_10",
    "return_20",
    "dist_ema20",
    "dist_ema50",
    "dist_ema200",
    "slope_ema20",
    "slope_ema50",
    "range_position",
    "breakout_score",
    # 2. Volatilité
    "atr_percentile",
    "realized_vol_10",
    "realized_vol_ratio",
    "vol_expansion",
    # 3. Volume
    "rvol",
    "vwap_distance",
    "cmf",
    "obv_slope",
    # 5. Liquidité / Structure
    "trend_force",
    "range_compression",
    # 7. Temps
    "session_london_ny_overlap",
]

MODEL_PATH = "runtime/lgb_model.txt"
MODEL_META_PATH = "runtime/lgb_model_meta.json"


class LightGBMModel:
    """Wrapper pour le modèle LightGBM.

    Utilisation:
        model = LightGBMModel()
        model.load()                          # charge depuis disque
        proba = model.predict(features_dict)  # proba de succès [0, 1]
        model.train(X, y)                     # entraînement
        model.save()                          # sauvegarde
    """

    def __init__(self):
        self._model: Any = None
        self._feature_importance: dict[str, float] = {}
        self._metadata: dict[str, Any] = {
            "version": "1.0",
            "train_date": "",
            "train_samples": 0,
            "val_accuracy": 0.0,
            "val_auc": 0.0,
            "features": FEATURE_COLUMNS,
        }
        # Seuil minimum de confiance pour envoyer un signal
        self.min_confidence = 0.52  # légèrement > 50% (edge positif)

    @property
    def available(self) -> bool:
        """Le modèle est-il chargé et prêt ?"""
        return self._model is not None

    def load(self, path: str | None = None) -> bool:
        """Charge le modèle LightGBM depuis un fichier.

        Returns:
            True si chargé avec succès, False sinon.
        """
        model_path = path or MODEL_PATH
        meta_path = MODEL_META_PATH

        if not os.path.exists(model_path):
            logger.warning(f"[LGB] Modèle non trouvé: {model_path}")
            return False

        try:
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=model_path)
            logger.info(f"[LGB] Modèle chargé: {model_path}")

            # Charger les métadonnées
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
                self._metadata.update(meta)
                self.min_confidence = meta.get("min_confidence", 0.52)
                logger.info(
                    f"[LGB] Metadata: {meta.get('train_samples', 0)} trades, val_acc={meta.get('val_accuracy', 0):.1%}"
                )

            # Feature importance
            try:
                importance = self._model.feature_importance(importance_type="gain")
                for i, name in enumerate(self._metadata.get("features", FEATURE_COLUMNS)):
                    if i < len(importance):
                        self._feature_importance[name] = float(importance[i])
                # Normaliser
                total = sum(self._feature_importance.values())
                if total > 0:
                    for k in self._feature_importance:
                        self._feature_importance[k] /= total
            except Exception:
                pass

            return True
        except Exception as e:
            logger.warning(f"[LGB] Erreur chargement modèle: {e}")
            self._model = None
            return False

    def save(self, path: str | None = None) -> bool:
        """Sauvegarde le modèle LightGBM."""
        if self._model is None:
            logger.warning("[LGB] Rien à sauvegarder (modèle non entraîné)")
            return False

        model_path = path or MODEL_PATH
        meta_path = MODEL_META_PATH

        try:
            Path(model_path).parent.mkdir(parents=True, exist_ok=True)
            self._model.save_model(model_path)
            logger.info(f"[LGB] Modèle sauvegardé: {model_path}")

            # Métadonnées
            self._metadata["features"] = FEATURE_COLUMNS
            with open(meta_path, "w") as f:
                json.dump(self._metadata, f, indent=2, default=str)
            logger.info(f"[LGB] Metadata sauvegardée: {meta_path}")

            return True
        except Exception as e:
            logger.warning(f"[LGB] Erreur sauvegarde modèle: {e}")
            return False

    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        """Prédit la probabilité de succès d'un trade.

        Args:
            features: Dict des features (depuis feature_pipeline.compute_all_features)

        Returns:
            dict avec:
                - probability: proba de gain [0, 1]
                - confidence: confiance dans la prédiction
                - action: "BUY"/"SELL"/"HOLD" (HOLD si < min_confidence)
                - direction: proba directionnelle (si disponible)
                - feature_importance: top 5 features contributives
        """
        result = {
            "probability": 0.5,
            "confidence": 0.0,
            "action": "HOLD",
            "direction": 0.5,
            "top_features": {},
        }

        if self._model is None:
            return result

        try:
            # Construire le vecteur de features dans l'ordre
            X = np.zeros((1, len(FEATURE_COLUMNS)))
            for i, col in enumerate(FEATURE_COLUMNS):
                X[0, i] = features.get(col, 0.0)

            # Prédiction
            proba = self._model.predict(X)[0]
            result["probability"] = round(float(proba), 4)

            # Confiance : distance à 0.5
            confidence = abs(proba - 0.5) * 2  # 0.0-1.0
            result["confidence"] = round(float(confidence), 4)

            # Action
            if proba >= self.min_confidence:
                result["action"] = "BUY"
            elif proba <= 1 - self.min_confidence:
                result["action"] = "SELL"
            else:
                result["action"] = "HOLD"

            # Direction : proba transformée en score directionnel
            result["direction"] = round(float(proba), 4)

            # Top features contributives
            if self._feature_importance:
                top = sorted(self._feature_importance.items(), key=lambda x: -x[1])[:5]
                result["top_features"] = {k: round(v, 3) for k, v in top}

        except Exception as e:
            logger.warning(f"[LGB] Erreur prédiction: {e}")

        return result

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        X_val: np.ndarray | None = None,
        y_val: np.ndarray | None = None,
        feature_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Entraîne le modèle LightGBM.

        Args:
            X: Matrice d'entraînement (n_samples, n_features)
            y: Labels (0 = perdant, 1 = gagnant)
            X_val: Matrice de validation (optionnelle)
            y_val: Labels de validation (optionnelle)
            feature_names: Noms des features (optionnel)

        Returns:
            dict avec les métriques d'entraînement
        """
        try:
            import lightgbm as lgb

            # Paramètres optimisés pour le trading directionnel
            params = {
                "objective": "binary",
                "metric": "binary_logloss",
                "boosting_type": "gbdt",
                "num_leaves": 31,
                "max_depth": 6,
                "learning_rate": 0.05,
                "n_estimators": 500,
                "min_child_samples": 20,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "reg_alpha": 0.1,
                "reg_lambda": 0.1,
                "class_weight": "balanced",
                "random_state": 42,
                "verbosity": -1,
            }

            train_data = lgb.Dataset(X, label=y, feature_name=feature_names or FEATURE_COLUMNS)

            if X_val is not None and y_val is not None:
                val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
                self._model = lgb.train(
                    params,
                    train_data,
                    valid_sets=[val_data],
                    callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
                )
                # Métriques de validation
                from sklearn.metrics import accuracy_score, roc_auc_score

                y_pred = (self._model.predict(X_val) > 0.5).astype(int)
                val_acc = accuracy_score(y_val, y_pred)
                try:
                    val_auc = roc_auc_score(y_val, self._model.predict(X_val))
                except Exception:
                    val_auc = 0.0
            else:
                self._model = lgb.train(params, train_data)
                val_acc = 0.0
                val_auc = 0.0

            # Métadonnées
            self._metadata.update(
                {
                    "train_date": str(np.datetime64("now")),
                    "train_samples": len(y),
                    "val_accuracy": round(float(val_acc), 4),
                    "val_auc": round(float(val_auc), 4),
                    "features": feature_names or FEATURE_COLUMNS,
                }
            )

            # Feature importance
            try:
                importance = self._model.feature_importance(importance_type="gain")
                fnames = feature_names or FEATURE_COLUMNS
                for i, name in enumerate(fnames):
                    if i < len(importance):
                        self._feature_importance[name] = float(importance[i])
                total = sum(self._feature_importance.values())
                if total > 0:
                    for k in self._feature_importance:
                        self._feature_importance[k] /= total
            except Exception:
                pass

            logger.info(
                f"[LGB] Entraînement terminé: {len(y)} échantillons, val_acc={val_acc:.1%}, val_auc={val_auc:.3f}"
            )

            return self._metadata

        except ImportError:
            logger.error("[LGB] lightgbm non installé. pip install lightgbm")
            raise
        except Exception as e:
            logger.error(f"[LGB] Erreur entraînement: {e}")
            raise

    def get_feature_importance(self, top_n: int = 10) -> list[tuple[str, float]]:
        """Retourne le top N features par importance."""
        if not self._feature_importance:
            return []
        return sorted(self._feature_importance.items(), key=lambda x: -x[1])[:top_n]

    def summary(self) -> dict[str, Any]:
        """Résumé du modèle."""
        return {
            "available": self.available,
            "version": self._metadata.get("version", ""),
            "train_samples": self._metadata.get("train_samples", 0),
            "val_accuracy": self._metadata.get("val_accuracy", 0),
            "val_auc": self._metadata.get("val_auc", 0),
            "min_confidence": self.min_confidence,
            "feature_count": len(FEATURE_COLUMNS),
            "top_features": self.get_feature_importance(5),
        }


def extract_features_for_model(
    features: dict[str, float],
    fill_missing: bool = True,
) -> np.ndarray:
    """Extrait un vecteur numpy des features dans l'ordre du modèle.

    Args:
        features: Dict complet des features (depuis compute_all_features)
        fill_missing: Si True, remplit les valeurs manquantes avec 0

    Returns:
        np.ndarray de forme (1, n_features)
    """
    X = np.zeros((1, len(FEATURE_COLUMNS)))
    for i, col in enumerate(FEATURE_COLUMNS):
        val = features.get(col)
        if val is not None and np.isfinite(val):
            X[0, i] = val
        elif fill_missing:
            X[0, i] = 0.0
    return X
