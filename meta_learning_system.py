# Shim non invasif pour rendre importable `meta_learning_system` au niveau racine.
# Il réexporte tout depuis `scripts.meta_learning_system` si disponible.
"""Shim non invasif: expose MetaLearningTradingSystem minimal.

Ce fichier permet au moteur `live_trading_engine` d'importer
`meta_learning_system` sans erreur. Si un modèle LightGBM est
présent sous `artifacts/auto_improve/best_lightgbm*.txt`, il sera
chargé et utilisé pour fournir `ensemble_predict`.

Le but est strictement non invasif: ne modifie pas d'autres fichiers
et ne lance aucune opération réseau.
"""

import os
from pathlib import Path
import numpy as np

try:
    # Preferer l'implémentation complète si elle existe dans scripts/
    from scripts.meta_learning_system import MetaLearningTradingSystem as _FullMeta
    MetaLearningTradingSystem = _FullMeta
except Exception:
    # Fallback minimal qui charge un modèle LightGBM si disponible
    try:
        import lightgbm as lgb
    except Exception:
        lgb = None

    class MetaLearningTradingSystem:
        """Implémentation minimale utilisée pour les dry-runs.

        Fournit `model_ensemble` non vide si un modèle est chargé et
        `ensemble_predict(X)` qui retourne une probabilité (1D array).
        """

        def __init__(self, max_models=3, performance_window=100):
            self.max_models = max_models
            self.performance_window = performance_window
            self.model_ensemble = []
            self.model_performances = []

            # Chercher un modèle LightGBM dans artifacts
            art_dir = Path('artifacts') / 'auto_improve'
            model_paths = list(art_dir.glob('best_lightgbm*.txt')) if art_dir.exists() else []

            if lgb is not None and model_paths:
                # Charger le premier modèle disponible
                try:
                    self.booster = lgb.Booster(model_file=str(model_paths[0]))
                    # créer une entrée simple dans model_ensemble
                    self.model_ensemble = [
                        {
                            'model': self.booster,
                            'performance': 1.0,
                            'architecture': 'lightgbm_booster_file',
                        }
                    ]
                except Exception:
                    self.booster = None
            else:
                self.booster = None

        def ensemble_predict(self, X):
            """Retourne un vecteur de probabilités (shape=(n_samples,))."""
            if self.model_ensemble and self.model_ensemble[0].get('model') is not None:
                model = self.model_ensemble[0]['model']

                # Préparer X pour lightgbm.Booster.predict
                # Si X est un DataFrame, tenter de sélectionner/renommer
                # les colonnes pour correspondre aux features entraînées.
                try:
                    import pandas as _pd
                    if _pd and hasattr(X, 'head'):
                        df = X.copy()
                        # Liste des features attendues par le modèle
                        fn = model.feature_name() or []
                        n_feat = len(fn) if fn is not None else 0

                        # Mapping déterministe: si les colonnes live portent des
                        # noms connus, les utiliser en priorité. Sinon, prendre
                        # les premières colonnes numériques.
                        preferred_order = [
                            'close', 'volume', 'sma_1T', 'ema_15T', 'rsi_60T'
                        ]

                        num_df = df.select_dtypes(include=[float, int, 'int64', 'float64']).copy()

                        # Si le modèle n'expose pas ses noms, supposer 5 features
                        if n_feat == 0:
                            n_feat = 5

                        mapped_values = []
                        for i in range(n_feat):
                            # Priorité: colonne préférée si présente
                            source_col = None
                            if i < len(preferred_order) and preferred_order[i] in df.columns:
                                source_col = preferred_order[i]
                            else:
                                # Sinon prendre i-ème colonne numérique si disponible
                                if i < len(num_df.columns):
                                    source_col = num_df.columns[i]

                            if source_col is not None and source_col in num_df.columns:
                                # Prendre la dernière valeur (ligne la plus récente)
                                try:
                                    val = num_df[source_col].iloc[-1]
                                except Exception:
                                    val = 0.0
                            else:
                                val = 0.0

                            mapped_values.append(float(val))

                        arr = np.array(mapped_values).reshape(1, -1)
                    else:
                        arr = X.values if hasattr(X, 'values') else np.asarray(X)
                except Exception:
                    arr = X.values if hasattr(X, 'values') else np.asarray(X)

                try:
                    # Forcer la shape: tronquer ou padzer pour correspondre
                    # au nombre de features attendues par le modèle.
                    fn = model.feature_name()
                    n_feat = len(fn) if fn is not None else None

                    if n_feat is not None:
                        # Assurer une entrée de shape (n_samples, n_feat).
                        # Si la dimension des colonnes est différente,
                        # utiliser la dernière ligne numérique comme base
                        if arr.ndim == 1:
                            arr = arr.reshape(1, -1)

                        if arr.shape[1] != n_feat:
                            # Prendre la dernière ligne (si disponible)
                            try:
                                last = arr[-1, :]
                            except Exception:
                                last = arr.flatten()

                            # Tronquer ou compléter par des zéros
                            if last.size >= n_feat:
                                new_row = last[:n_feat]
                            else:
                                pad = np.zeros(n_feat - last.size)
                                new_row = np.concatenate([last, pad])

                            # Former un batch d'une seule ligne
                            arr = new_row.reshape(1, n_feat)

                    try:
                        preds = model.predict(arr, predict_disable_shape_check=True)
                    except TypeError:
                        # Certaines versions acceptent le flag différemment
                        preds = model.predict(arr)
                    return np.array(preds)
                except Exception as e:
                    # Log l'erreur en stdout pour debug
                    print('Erreur prédiction lightgbm_booster_file:', e)
                    # Retour neutre si échec
                    try:
                        nrows = arr.shape[0]
                        return np.zeros((nrows,))
                    except Exception:
                        return np.array([])

            # Aucun modèle: lever une erreur légère pour que l'appelant
            # choisisse le fallback (comme dans le code existant)
            raise ValueError('Aucun modèle dans MetaLearning ensemble')

    __all__ = ["MetaLearningTradingSystem"]
