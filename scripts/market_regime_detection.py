#!/usr/bin/env python3
"""
Système de Détection Avancée des Régimes de Marché.

Ce système implémente :
- Hidden Markov Models (HMM) pour détecter automatiquement les régimes
- Détection de régimes Bull/Bear/Sideways avec transitions
- Adaptation automatique des stratégies selon le régime détecté
- Modèles de Regime Switching pour volatilité et tendance
- Prédiction probabiliste des changements de régime
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import warnings

# Machine Learning
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

warnings.filterwarnings("ignore")

# Hidden Markov Models avec fallback robuste
try:
    from hmmlearn import hmm

    HMM_AVAILABLE = True
    print("✅ hmmlearn disponible - HMM complet activé")
except ImportError as e:
    HMM_AVAILABLE = False
    print(f"⚠️  hmmlearn non disponible: {e}")
    print("🔄 Fallback vers clustering K-means pour régimes")
except Exception as e:
    HMM_AVAILABLE = False
    print(f"🔴 Erreur hmmlearn: {e}")
    print("🔄 Fallback vers clustering K-means pour régimes")


class MarketRegimeDetector:
    """Détecteur de régimes de marché avancé"""

    def __init__(self, n_regimes=3, regime_names=None):
        """
        Args:
            n_regimes: Nombre de régimes à détecter (défaut: 3)
            regime_names: Noms personnalisés des régimes
        """
        self.n_regimes = n_regimes
        self.regime_names = (
            regime_names or ["Bear", "Sideways", "Bull"][:n_regimes]
        )

        # Modèles
        self.hmm_model = None
        self.gmm_model = None
        self.scaler = StandardScaler()

        # État des régimes
        self.current_regime = None
        self.regime_history = []
        self.regime_probabilities = None
        self.transition_matrix = None

        # Métriques des régimes
        self.regime_characteristics = {}

        print("🎯 Détecteur de Régimes initialisé:")
        print(f"  📊 Régimes: {n_regimes} ({', '.join(self.regime_names)})")
        print(f"  🧠 HMM disponible: {'✅' if HMM_AVAILABLE else '❌'}")

    def extract_regime_features(self, df):
        """
        Extraire les features pour détecter les régimes

        Args:
            df: DataFrame avec colonnes de prix (adaptable)
        """
        features = pd.DataFrame(index=df.index)

        # Détecter les colonnes disponibles
        price_col = "close" if "close" in df.columns else df.columns[0]
        has_volume = "volume" in df.columns
        has_ohlc = all(
            col in df.columns for col in ["open", "high", "low", "close"]
        )

        print(
            f"  📊 Colonnes détectées: price={price_col}, "
            f"volume={has_volume}, OHLC={has_ohlc}"
        )

        # 1. Rendements et volatilité
        features["returns"] = df[price_col].pct_change()
        features["volatility"] = features["returns"].rolling(20).std()
        features["log_returns"] = np.log(
            df[price_col] / df[price_col].shift(1)
        )

        # 2. Tendance et momentum
        features["sma_5"] = df[price_col].rolling(5).mean()
        features["sma_20"] = df[price_col].rolling(20).mean()
        features["sma_50"] = df[price_col].rolling(50).mean()

        features["trend_short"] = (
            features["sma_5"] - features["sma_20"]
        ) / features["sma_20"]
        features["trend_long"] = (
            features["sma_20"] - features["sma_50"]
        ) / features["sma_50"]

        # 3. RSI pour momentum (utiliser RSI existant si disponible)
        if "rsi_60T" in df.columns:
            features["rsi"] = df["rsi_60T"]
        else:
            # Calculer RSI
            delta = df[price_col].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            features["rsi"] = 100 - (100 / (1 + rs))

        features["rsi_normalized"] = (features["rsi"] - 50) / 50

        # 4. Bollinger Bands pour volatilité
        sma_bb = df[price_col].rolling(20).mean()
        bb_std = df[price_col].rolling(20).std()
        features["bb_position"] = (df[price_col] - sma_bb) / (2 * bb_std)
        features["bb_squeeze"] = bb_std / sma_bb  # Mesure de compression

        # 5. Volume features (si disponible)
        if has_volume:
            features["volume_sma"] = df["volume"].rolling(20).mean()
            features["volume_ratio"] = df["volume"] / features["volume_sma"]
        else:
            features["volume_ratio"] = 1

        # 6. Range et volatilité (adaptation selon OHLC disponible)
        if has_ohlc:
            features["true_range"] = np.maximum(
                df["high"] - df["low"],
                np.maximum(
                    abs(df["high"] - df[price_col].shift(1)),
                    abs(df["low"] - df[price_col].shift(1)),
                ),
            )
        else:
            # Approximer avec la volatilité du prix de clôture
            features["true_range"] = abs(
                df[price_col] - df[price_col].shift(1)
            )

        features["atr"] = features["true_range"].rolling(14).mean()
        features["atr_normalized"] = features["atr"] / df[price_col]

        # 7. Skewness et Kurtosis sur fenêtre glissante
        window = 20
        features["returns_skew"] = features["returns"].rolling(window).skew()
        features["returns_kurtosis"] = (
            features["returns"].rolling(window).kurt()
        )

        # 8. Drawdown
        peak = df[price_col].expanding().max()
        features["drawdown"] = (df[price_col] - peak) / peak
        features["max_drawdown"] = features["drawdown"].rolling(50).min()

        # 9. Corrélation sérielle (autocorrélation)
        features["autocorr_1"] = (
            features["returns"]
            .rolling(20)
            .apply(lambda x: x.autocorr(lag=1) if len(x) > 1 else 0)
        )

        # 10. Régime momentum combiné
        features["regime_momentum"] = (
            features["trend_short"] * 0.4
            + features["trend_long"] * 0.3
            + features["rsi_normalized"] * 0.3
        )

        # 11. Utiliser les features existantes si disponibles
        if "ema_15T" in df.columns:
            features["ema_ratio"] = df["ema_15T"] / df[price_col] - 1

        # Nettoyer les données
        features = features.fillna(method="ffill").fillna(0)

        print(
            f"  ✅ {len(features.columns)} features "
            f"extraites pour détection régimes"
        )

        return features

    def fit_hmm_model(self, features):
        """
        Entraîner un modèle HMM pour détecter les régimes

        Args:
            features: DataFrame avec les features du marché
        """
        if not HMM_AVAILABLE:
            print("⚠️  HMM non disponible - Utilisation fallback")
            return self.fit_clustering_fallback(features)

        print("🧠 Entraînement modèle HMM...")

        # Sélectionner les features clés pour HMM
        key_features = [
            "returns",
            "volatility",
            "trend_short",
            "trend_long",
            "rsi_normalized",
            "bb_position",
            "regime_momentum",
        ]

        # Préparer les données
        X = features[key_features].fillna(0)
        X_scaled = self.scaler.fit_transform(X)

        # Entraîner le modèle HMM avec paramètres de convergence améliorés
        self.hmm_model = hmm.GaussianHMM(
            n_components=self.n_regimes,
            covariance_type="diag",  # Plus stable que "full"
            n_iter=50,  # Réduit pour éviter sur-ajustement
            tol=1e-3,  # Tolérance de convergence plus permissive
            random_state=42,
            verbose=False,  # Désactive les warnings de convergence
        )

        try:
            # Supprimer complètement les warnings de convergence HMM
            import warnings
            import logging

            # Désactiver tous les warnings hmmlearn
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            warnings.filterwarnings("ignore", module="hmmlearn")

            # Désactiver le logging de hmmlearn
            hmm_logger = logging.getLogger('hmmlearn')
            hmm_logger.setLevel(logging.ERROR)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.hmm_model.fit(X_scaled)

            # Prédire les régimes
            regimes = self.hmm_model.predict(X_scaled)
            regime_probs = self.hmm_model.predict_proba(X_scaled)

            # Stocker la matrice de transition
            self.transition_matrix = self.hmm_model.transmat_

            score = self.hmm_model.score(X_scaled)
            print(f"  ✅ HMM entraîné - Log-likelihood: {score:.2f}")

            return regimes, regime_probs, X_scaled

        except Exception as e:
            print(f"  ⚠️  Erreur HMM: {e} - Utilisation fallback")
            return self.fit_clustering_fallback(features)

    def fit_clustering_fallback(self, features):
        """
        Méthode fallback utilisant K-Means pour détecter les régimes

        Args:
            features: DataFrame avec les features du marché
        """
        print("🔄 Clustering K-Means pour détection régimes...")

        # Features simplifiées pour clustering
        cluster_features = [
            "returns",
            "volatility",
            "trend_short",
            "rsi_normalized",
            "regime_momentum",
        ]

        X = features[cluster_features].fillna(0)
        # If there are no samples, return safe empty structures as expected by tests
        if X.shape[0] == 0:
            regimes = np.array([], dtype=int)
            regime_probs = np.empty((0, self.n_regimes))
            return regimes, regime_probs, X.values

        X_scaled = self.scaler.fit_transform(X)

        # Ensure n_clusters is not larger than number of samples
        n_samples = X_scaled.shape[0]
        n_clusters = min(self.n_regimes, max(1, n_samples))

        # K-Means clustering
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        regimes = kmeans.fit_predict(X_scaled)

        # Créer des probabilités approximatives
        distances = kmeans.transform(X_scaled)
        # Convertir distances en probabilités (plus proche = plus probable)
        expd = np.exp(-distances)
        regime_probs = expd / expd.sum(axis=1, keepdims=True)

        # Créer une matrice de transition approximative
        self.transition_matrix = self._estimate_transition_matrix(regimes)

        # Silhouette score only when there is more than one label and enough samples
        unique_labels = len(np.unique(regimes))
        if 1 < unique_labels < n_samples:
            try:
                silhouette_sc = silhouette_score(X_scaled, regimes)
                print(f"  ✅ K-Means complété - Silhouette score: {silhouette_sc:.3f}")
            except Exception:
                # Non-fatal: continue without silhouette
                pass
        else:
            print("  ✅ K-Means complété - silhouette skipped (insufficient label variety)")

        return regimes, regime_probs, X_scaled

    def _estimate_transition_matrix(self, regimes):
        """Estimer la matrice de transition à partir des régimes détectés"""
        n_regimes = len(np.unique(regimes))
        transition_counts = np.zeros((n_regimes, n_regimes))

        for i in range(len(regimes) - 1):
            current_regime = regimes[i]
            next_regime = regimes[i + 1]
            transition_counts[current_regime, next_regime] += 1

        # Normaliser pour obtenir des probabilités
        transition_matrix = transition_counts / transition_counts.sum(
            axis=1, keepdims=True
        )

        # Gérer les divisions par zéro
        transition_matrix = np.nan_to_num(transition_matrix)

        return transition_matrix

    def label_regimes_intelligently(self, regimes, features):
        """
        Labeller intelligemment les régimes détectés

        Args:
            regimes: Array des régimes détectés
            features: DataFrame des features utilisées
        """
        print("🏷️  Labelling intelligent des régimes...")

        regime_stats = {}

        for regime_id in range(self.n_regimes):
            mask = regimes == regime_id
            regime_data = features[mask]

            if len(regime_data) == 0:
                continue

            stats = {
                "avg_return": regime_data["returns"].mean(),
                "volatility": regime_data["volatility"].mean(),
                "trend_strength": regime_data["regime_momentum"].mean(),
                "count": len(regime_data),
                "percentage": len(regime_data) / len(features) * 100,
            }

            regime_stats[regime_id] = stats

        # Trier les régimes par performance (rendement moyen)
        sorted_regimes = sorted(
            regime_stats.items(), key=lambda x: x[1]["avg_return"]
        )

        # Mapper vers les noms intelligents
        regime_mapping = {}
        if len(sorted_regimes) >= 3:
            # Bear (pire performance), Sideways (milieu), Bull (meilleure)
            regime_mapping[sorted_regimes[0][0]] = 0  # Bear
            regime_mapping[sorted_regimes[-1][0]] = 2  # Bull
            regime_mapping[sorted_regimes[1][0]] = 1  # Sideways
        else:
            # Mapping simple
            for i, (regime_id, _) in enumerate(sorted_regimes):
                regime_mapping[regime_id] = i

        # Réétiqueter les régimes
        remapped_regimes = np.array(
            [regime_mapping.get(r, r) for r in regimes]
        )

        # Calculer les caractéristiques finales
        for regime_id in range(self.n_regimes):
            mask = remapped_regimes == regime_id
            if np.any(mask):
                regime_data = features[mask]

                self.regime_characteristics[regime_id] = {
                    "name": self.regime_names[regime_id],
                    "avg_return": regime_data["returns"].mean(),
                    "volatility": regime_data["volatility"].mean(),
                    "trend_strength": regime_data[
                        "regime_momentum"
                    ].mean(),
                    "rsi_avg": regime_data["rsi_normalized"].mean(),
                    "count": len(regime_data),
                    "percentage": (
                        len(regime_data) / len(features) * 100
                    ),
                    "sharpe_approx": (
                        regime_data["returns"].mean()
                        / regime_data["volatility"].mean()
                        if regime_data["volatility"].mean() > 0
                        else 0
                    ),
                }

        print("  ✅ Caractéristiques des régimes:")
        for regime_id, chars in self.regime_characteristics.items():
            print(
                f"    {chars['name']}: {chars['percentage']:.1f}% du temps, "
                f"Return={chars['avg_return']*252*100:.1f}%, "
                f"Vol={chars['volatility']*np.sqrt(252)*100:.1f}%, "
                f"Sharpe≈{chars['sharpe_approx']:.2f}"
            )

        return remapped_regimes

    def detect_regimes(self, df):
        """
        Pipeline complet de détection des régimes

        Args:
            df: DataFrame avec données OHLCV
        """
        print("🔍 DÉTECTION COMPLÈTE DES RÉGIMES")
        print("=" * 40)

        # Early exit for empty inputs: return safe, empty structures
        if df is None or len(df) == 0:
            import numpy as _np

            empty_features = pd.DataFrame(index=pd.DatetimeIndex([]))
            return {
                "regimes": _np.array([], dtype=int),
                "probabilities": _np.empty((0, self.n_regimes)),
                "features": empty_features,
                "current_regime": None,
                "regime_characteristics": {},
            }

        # 1. Extraire les features
        features = self.extract_regime_features(df)

        # 2. Entraîner le modèle
        regimes, regime_probs, features_scaled = self.fit_hmm_model(features)

        # 3. Labeller intelligemment
        final_regimes = self.label_regimes_intelligently(regimes, features)

        # 4. Sauvegarder l'historique
        self.regime_history = pd.Series(
            final_regimes, index=features.index, name="regime"
        )
        self.regime_probabilities = pd.DataFrame(
            regime_probs,
            index=features.index,
            columns=[f"{name}_prob" for name in self.regime_names],
        )

        # 5. Régime actuel
        self.current_regime = final_regimes[-1]

        print("\n✅ Détection terminée:")
        print(f"  📊 Régime actuel: {self.regime_names[self.current_regime]}")
        print(
            f"  🎯 Confiance: {regime_probs[-1][self.current_regime]*100:.1f}%"
        )

        return {
            "regimes": final_regimes,
            "probabilities": regime_probs,
            "features": features,
            "current_regime": self.current_regime,
            "regime_characteristics": self.regime_characteristics,
        }

    def predict_regime_transition(self, current_features, horizon=5):
        """
        Prédire les probabilités de transition vers d'autres régimes

        Args:
            current_features: Features actuelles du marché
            horizon: Horizon de prédiction (nombre de périodes)
        """
        if self.transition_matrix is None:
            return None

        # État actuel
        current_state = np.zeros(self.n_regimes)
        current_state[self.current_regime] = 1

        # Prédiction par multiplication matricielle
        future_probabilities = {}
        state = current_state.copy()

        for h in range(1, horizon + 1):
            state = np.dot(state, self.transition_matrix)
            future_probabilities[f"t+{h}"] = {
                regime_name: prob
                for regime_name, prob in zip(self.regime_names, state)
            }

        print(f"📈 Prédictions transitions ({horizon} périodes):")
        for period, probs in future_probabilities.items():
            most_likely = max(probs.items(), key=lambda x: x[1])
            print(f"  {period}: {most_likely[0]} ({most_likely[1]*100:.1f}%)")

        return future_probabilities

    def get_regime_strategy_signals(self, current_regime_id):
        """
        Obtenir les signaux de trading adaptés au régime actuel

        Args:
            current_regime_id: ID du régime actuel
        """
        if current_regime_id not in self.regime_characteristics:
            return {"action": "hold", "confidence": 0}

        regime = self.regime_characteristics[current_regime_id]
        regime_name = regime["name"].lower()

        if regime_name == "bull":
            # Stratégie agressive en marché haussier
            return {
                "action": "long_bias",
                "confidence": 0.8,
                "position_size": 1.2,  # Effet de levier modéré
                "stop_loss": 0.02,  # Stop-loss serré
                "take_profit": 0.06,  # Take-profit élevé
                "strategy": "momentum_following",
            }

        elif regime_name == "bear":
            # Stratégie défensive en marché baissier
            return {
                "action": "short_bias",
                "confidence": 0.7,
                "position_size": 0.8,  # Position réduite
                "stop_loss": 0.015,  # Stop-loss très serré
                "take_profit": 0.04,  # Take-profit modéré
                "strategy": "mean_reversion",
            }

        else:  # sideways
            # Stratégie range-trading en marché latéral
            return {
                "action": "range_trading",
                "confidence": 0.6,
                "position_size": 1.0,
                "stop_loss": 0.01,  # Stop-loss très serré
                "take_profit": 0.02,  # Take-profit rapide
                "strategy": "mean_reversion",
            }

    def save_regime_model(self, filepath="artifacts/regime_detection"):
        """Sauvegarder le modèle de détection des régimes"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Sauvegarder les métadonnées
        model_data = {
            "timestamp": datetime.now().isoformat(),
            "n_regimes": self.n_regimes,
            "regime_names": self.regime_names,
            "current_regime": int(self.current_regime)
            if self.current_regime is not None
            else None,
            "regime_characteristics": {
                str(k): {
                    key: float(val)
                    if isinstance(val, (np.float64, np.float32))
                    else val
                    for key, val in v.items()
                }
                for k, v in self.regime_characteristics.items()
            },
            "transition_matrix": self.transition_matrix.tolist()
            if self.transition_matrix is not None
            else None,
        }

        with open(f"{filepath}_model.json", "w") as f:
            json.dump(model_data, f, indent=2, default=str)

        # Sauvegarder l'historique des régimes
        if hasattr(self, "regime_history") and self.regime_history is not None:
            self.regime_history.to_csv(f"{filepath}_history.csv")

        # Sauvegarder les probabilités
        if (
            hasattr(self, "regime_probabilities")
            and self.regime_probabilities is not None
        ):
            self.regime_probabilities.to_csv(f"{filepath}_probabilities.csv")

        print(f"💾 Modèle de régimes sauvegardé: {filepath}")


def main():
    """Test du système de détection des régimes"""
    print("🎯 TEST SYSTÈME DÉTECTION RÉGIMES")
    print("=" * 35)

    try:
        # Charger les données
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        # Créer le détecteur
        detector = MarketRegimeDetector(
            n_regimes=3, regime_names=["Bear", "Sideways", "Bull"]
        )

        # Détecter les régimes
        results = detector.detect_regimes(df)

        # Prédiction des transitions
        print("\n🔮 PRÉDICTIONS TRANSITIONS:")
        print("=" * 30)
        detector.predict_regime_transition(
            results["features"].iloc[-1], horizon=5
        )

        # Signaux de stratégie
        print("\n📊 SIGNAUX STRATÉGIE ADAPTATIVE:")
        print("=" * 35)
        strategy_signals = detector.get_regime_strategy_signals(
            detector.current_regime
        )

        print(f"  🎯 Action recommandée: {strategy_signals['action']}")
        print(f"  📊 Confiance: {strategy_signals['confidence']*100:.1f}%")
        print(f"  💰 Taille position: {strategy_signals['position_size']:.1f}x")
        print(f"  🛡️  Stop-loss: {strategy_signals['stop_loss']*100:.1f}%")
        print(f"  🎯 Take-profit: {strategy_signals['take_profit']*100:.1f}%")
        print(f"  📈 Stratégie: {strategy_signals['strategy']}")

        # Analyser la stabilité des régimes
        regime_series = pd.Series(results["regimes"], index=df.index)
        regime_changes = (regime_series != regime_series.shift(1)).sum()
        avg_regime_duration = (
            len(regime_series) / regime_changes
            if regime_changes > 0
            else len(regime_series)
        )

        print("\n📊 ANALYSE STABILITÉ:")
        print("=" * 20)
        print(f"  🔄 Changements de régimes: {regime_changes}")
        print(f"  ⏱️  Durée moyenne: {avg_regime_duration:.1f} périodes")

        # Sauvegarder
        detector.save_regime_model()

        print("\n✅ Détection de régimes terminée avec succès")

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
