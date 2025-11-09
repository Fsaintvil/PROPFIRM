# Merged preview for prefix: market
# Generated from 2 files

################################################################################
# FROM: scripts\market_regime_detection.py
################################################################################
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

import json
import os
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

# Machine Learning
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler, RobustScaler

warnings.filterwarnings("ignore")

# Hidden Markov Models avec fallback robuste
try:
    from hmmlearn import hmm

    HMM_AVAILABLE = True
    print("✅ hmmlearn disponible - HMM complet activé")
except ImportError:
    HMM_AVAILABLE = False
    print("🔄 Fallback vers clustering K-means pour régimes (hmmlearn absent)")
except Exception:
    HMM_AVAILABLE = False
    print("🔄 Fallback vers clustering K-means pour régimes (erreur import hmmlearn)")


class MarketRegimeDetector:
    """Détecteur de régimes de marché avancé"""

    def __init__(self, n_regimes=3, regime_names=None):
        self.n_regimes = n_regimes
        self.regime_names = regime_names or ["Bear", "Sideways", "Bull"][:n_regimes]

        self.hmm_model = None
        self.scaler = StandardScaler()

        self.current_regime = None
        self.regime_history = []
        self.regime_probabilities = None
        self.transition_matrix = None
        self.regime_characteristics = {}

        print("🎯 Détecteur de Régimes initialisé")

    def extract_regime_features(self, df):
        """Extraire les features pour détecter les régimes

        Args:
            df: DataFrame avec colonnes de prix (adaptable)
        """
        features = pd.DataFrame(index=df.index)

        # Détecter les colonnes disponibles
        price_col = "close" if "close" in df.columns else df.columns[0]
        has_volume = "volume" in df.columns
        has_ohlc = all(col in df.columns for col in ["open", "high", "low", "close"])

        print(f"📊 Extraction features régime: price_col={price_col}")

        # 1. Rendements et volatilité
        features["returns"] = pd.to_numeric(df[price_col], errors="coerce").pct_change()
        features["volatility"] = features["returns"].rolling(20).std()
        features["log_returns"] = np.log(pd.to_numeric(df[price_col], errors="coerce") / pd.to_numeric(df[price_col], errors="coerce").shift(1))

        # 2. Tendance et momentum
        features["sma_5"] = df[price_col].rolling(5).mean()
        features["sma_20"] = df[price_col].rolling(20).mean()
        features["sma_50"] = df[price_col].rolling(50).mean()

        features["trend_short"] = (features["sma_5"] - features["sma_20"]) / features["sma_20"]
        features["trend_long"] = (features["sma_20"] - features["sma_50"]) / features["sma_50"]

        # 3. RSI pour momentum (utiliser RSI existant si disponible)
        if "rsi_60T" in df.columns:
            features["rsi"] = df["rsi_60T"]
        else:
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
        features["bb_squeeze"] = bb_std / sma_bb

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
            features["true_range"] = abs(df[price_col] - df[price_col].shift(1))

        features["atr"] = features["true_range"].rolling(14).mean()
        features["atr_normalized"] = features["atr"] / df[price_col]

        # 7. Skewness et Kurtosis sur fenêtre glissante
        window = 20
        features["returns_skew"] = features["returns"].rolling(window).skew()
        features["returns_kurtosis"] = features["returns"].rolling(window).kurt()

        # 8. Drawdown
        peak = df[price_col].expanding().max()
        features["drawdown"] = (df[price_col] - peak) / peak
        features["max_drawdown"] = features["drawdown"].rolling(50).min()

        # 9. Corrélation sérielle
        features["autocorr_1"] = features["returns"].rolling(20).apply(lambda x: x.autocorr(lag=1) if len(x) > 1 else 0)

        # 10. Régime momentum combiné
        features["regime_momentum"] = (
            features["trend_short"] * 0.4
            + features["trend_long"] * 0.3
            + features["rsi_normalized"] * 0.3
        )

        # 11. EMA ratio si présent
        if "ema_15T" in df.columns:
            features["ema_ratio"] = df["ema_15T"] / df[price_col] - 1

        # Nettoyer les données: forward-fill puis median impute conservateur
        features = features.fillna(method="ffill")
        for c in features.columns:
            if features[c].isna().any():
                med = features[c].median()
                if np.isnan(med):
                    med = 0.0
                features[c] = features[c].fillna(med)

        print(f"  ✅ {len(features.columns)} features extraites pour détection régimes")

        return features

    def _validate_regime_input(self, df, features):
        """
        Validation légère et conservatrice des entrées pour éviter d'entraîner
        un HMM sur des données manifestement corrompues.

        Renvoie (ok: bool, report: dict).
        """
        report = {}
        try:
            if "returns" not in features.columns:
                report["error"] = "no_returns"
                return False, report

            rets = features["returns"]
            n = len(rets)
            n_na = int(rets.isna().sum())
            pct_na = n_na / n if n > 0 else 1.0
            abs_gt_max = int((rets.abs() > float(os.getenv("REGIME_MAX_RETURN", "0.5"))).sum())
            max_abs = float(rets.abs().max(skipna=True) if n > 0 else np.nan)

            report.update({"n": n, "n_na": n_na, "pct_na": pct_na, "abs_gt_max": abs_gt_max, "max_abs": max_abs})

            if pct_na > 0.2:
                report["reason"] = "too_many_missing_returns"
                return False, report

            if abs_gt_max > max(10, int(0.05 * n)):
                report["reason"] = "many_extreme_returns"
                return False, report

            if not np.isnan(max_abs) and max_abs > 50:
                report["reason"] = "abs_return_implausible"
                return False, report

            report["reason"] = "ok"
            return True, report

        except Exception as e:
            report["exception"] = str(e)
            return False, report

    def fit_hmm_model(self, features):
        if not HMM_AVAILABLE:
            print("⚠️  HMM non disponible - Utilisation fallback")
            return self.fit_clustering_fallback(features)

        print("🧠 Entraînement modèle HMM...")

        key_features = [
            "returns",
            "volatility",
            "trend_short",
            "trend_long",
            "rsi_normalized",
            "bb_position",
            "regime_momentum",
        ]

        X = features[key_features].fillna(0)
        # Use RobustScaler when safe-clean requested, otherwise use instance scaler
        try:
            use_robust = os.getenv("REGIME_SAFE_CLEAN", "0") == "1"
        except Exception:
            use_robust = False

        if use_robust:
            scaler = RobustScaler()
        else:
            scaler = self.scaler

        X_scaled = scaler.fit_transform(X)
        # keep scaler for possible reuse
        self.scaler = scaler

        # Paramètres HMM simples et robustes
        try:
            model = hmm.GaussianHMM(
                n_components=self.n_regimes,
                covariance_type="diag",
                n_iter=100,
                random_state=42,
            )

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model.fit(X_scaled)

            regimes = model.predict(X_scaled)
            regime_probs = model.predict_proba(X_scaled)
            self.transition_matrix = model.transmat_
            self.hmm_model = model

            score = None
            try:
                score = model.score(X_scaled)
            except Exception:
                score = None

            print(f"  ✅ HMM entraîné - score: {score}")
            return regimes, regime_probs, X_scaled

        except Exception as e:
            print(f"  ⚠️  Erreur HMM: {e} - Utilisation fallback")
            return self.fit_clustering_fallback(features)

    def fit_clustering_fallback(self, features):
        print("🔄 Clustering K-Means pour détection régimes...")

        cluster_features = ["returns", "volatility", "trend_short", "rsi_normalized", "regime_momentum"]
        X = features[cluster_features].fillna(0)
        try:
            use_robust = os.getenv("REGIME_SAFE_CLEAN", "0") == "1"
        except Exception:
            use_robust = False

        if use_robust:
            scaler = RobustScaler()
        else:
            scaler = self.scaler

        X_scaled = scaler.fit_transform(X)
        self.scaler = scaler

        kmeans = KMeans(n_clusters=self.n_regimes, random_state=42, n_init=10)
        regimes = kmeans.fit_predict(X_scaled)

        distances = kmeans.transform(X_scaled)
        regime_probs = np.exp(-distances) / np.exp(-distances).sum(axis=1, keepdims=True)

        self.transition_matrix = self._estimate_transition_matrix(regimes)

        try:
            silhouette_sc = silhouette_score(X_scaled, regimes)
            print(f"  ✅ K-Means complété - Silhouette score: {silhouette_sc:.3f}")
        except Exception:
            pass

        return regimes, regime_probs, X_scaled

    def _estimate_transition_matrix(self, regimes):
        n_regimes = len(np.unique(regimes))
        transition_counts = np.zeros((n_regimes, n_regimes))
        for i in range(len(regimes) - 1):
            current_regime = regimes[i]
            next_regime = regimes[i + 1]
            transition_counts[current_regime, next_regime] += 1
        transition_matrix = transition_counts / transition_counts.sum(axis=1, keepdims=True)
        transition_matrix = np.nan_to_num(transition_matrix)
        return transition_matrix

    def label_regimes_intelligently(self, regimes, features):
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

        sorted_regimes = sorted(regime_stats.items(), key=lambda x: x[1]["avg_return"]) if regime_stats else []

        regime_mapping = {}
        if len(sorted_regimes) >= 3:
            regime_mapping[sorted_regimes[0][0]] = 0
            regime_mapping[sorted_regimes[-1][0]] = 2
            regime_mapping[sorted_regimes[1][0]] = 1
        else:
            for i, (regime_id, _) in enumerate(sorted_regimes):
                regime_mapping[regime_id] = i

        remapped_regimes = np.array([regime_mapping.get(r, r) for r in regimes])

        for regime_id in range(self.n_regimes):
            mask = remapped_regimes == regime_id
            if np.any(mask):
                regime_data = features[mask]
                self.regime_characteristics[regime_id] = {
                    "name": self.regime_names[regime_id],
                    "avg_return": regime_data["returns"].mean(),
                    "volatility": regime_data["volatility"].mean(),
                    "trend_strength": regime_data["regime_momentum"].mean(),
                    "rsi_avg": regime_data.get("rsi_normalized", pd.Series()).mean() if "rsi_normalized" in regime_data else 0,
                    "count": len(regime_data),
                    "percentage": len(regime_data) / len(features) * 100,
                    "sharpe_approx": (regime_data["returns"].mean() / regime_data["volatility"].mean()) if regime_data["volatility"].mean() > 0 else 0,
                }

        return remapped_regimes

    def detect_regimes(self, df):
        print("🔍 DÉTECTION COMPLÈTE DES RÉGIMES")
        print("=" * 40)

        features = self.extract_regime_features(df)

        # Optional: validate raw input BEFORE any safe-clean (opt-in only, non intrusive)
        if os.getenv("REGIME_VALIDATE_RAW", "0") == "1":
            try:
                raw_ok, raw_report = self._validate_regime_input(df, features)
                if not raw_ok:
                    print("⚠️  Validation RAW échouée (REGIME_VALIDATE_RAW=1) - diagnostic only (no fallback)")
                    print(f"  ➜ raw_report: {raw_report}")
                    # Optionnel: écrire un rapport de diagnostic brut si demandé (safe)
                    try:
                        if os.getenv("REGIME_VALIDATE_DUMP", "0") == "1":
                            dump_dir = os.path.join("artifacts", "diagnostics")
                            os.makedirs(dump_dir, exist_ok=True)
                            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                            outp = {
                                "timestamp_utc": ts,
                                "raw_validation_report": raw_report,
                                # include a tiny sample to help ops triage (first/last 3 returns)
                                "sample_returns_head": features["returns"].head(3).tolist(),
                                "sample_returns_tail": features["returns"].tail(3).tolist(),
                            }
                            fname = os.path.join(dump_dir, f"last_validation_raw_{ts}.json")
                            with open(fname, "w") as _f:
                                json.dump(outp, _f, indent=2, default=str)
                            print(f"  💾 Rapport raw de validation déposé: {fname}")
                            # Emit a compact JSON event line for log collectors/monitoring (opt-in via REGIME_VALIDATE_DUMP)
                            try:
                                log_evt = {
                                    "evt": "regime_validation_raw_fail",
                                    "timestamp_utc": ts,
                                    "reason": raw_report.get("reason"),
                                    "abs_gt_max": raw_report.get("abs_gt_max"),
                                }
                                # compact one-line JSON to be parsable by log agents
                                print(json.dumps(log_evt, separators=(",", ":"), default=str))
                            except Exception:
                                # never fail detection because of logging
                                pass
                    except Exception:
                        # Never fail detection because of a log write
                        pass
            except Exception:
                # Very defensive: do not break detection for validation errors
                pass

        # Optional conservative cleaning (opt-in, safe)
        if os.getenv("REGIME_SAFE_CLEAN", "0") == "1":
            try:
                print("🧼 REGIME_SAFE_CLEAN activé — application d'un nettoyage conservateur des features")
                # Clip chaque feature aux percentiles 1% / 99% pour limiter outliers extrêmes
                for col in features.columns:
                    if pd.api.types.is_numeric_dtype(features[col]):
                        lo = features[col].quantile(0.01)
                        hi = features[col].quantile(0.99)
                        if pd.notna(lo) and pd.notna(hi) and hi > lo:
                            features[col] = features[col].clip(lower=lo, upper=hi)

                # En plus, appliquer un cap explicite sur les rendements si défini
                try:
                    max_r = float(os.getenv("REGIME_MAX_RETURN", "0.5"))
                    features["returns"] = features["returns"].clip(lower=-max_r, upper=max_r)
                except Exception:
                    pass
            except Exception:
                # Ne pas échouer la détection pour un problème de nettoyage
                pass

        # Optional: validate input before training HMM (disabled by default)
        if os.getenv("REGIME_VALIDATE_INPUT", "0") == "1":
            ok, report = self._validate_regime_input(df, features)
            if not ok:
                print("⚠️  Validation d'entrée échouée (REGIME_VALIDATE_INPUT=1) - fallback vers K-Means")
                print(f"  ➜ rapport_validation: {report}")
                # Optionnel: écrire un rapport de diagnostic si demandé (safe, hors comportement par défaut)
                try:
                    if os.getenv("REGIME_VALIDATE_DUMP", "0") == "1":
                        dump_dir = os.path.join("artifacts", "diagnostics")
                        os.makedirs(dump_dir, exist_ok=True)
                        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                        outp = {
                            "timestamp_utc": ts,
                            "validation_report": report,
                        }
                        fname = os.path.join(dump_dir, f"regime_validation_{ts}.json")
                        with open(fname, "w") as _f:
                            json.dump(outp, _f, indent=2, default=str)
                        print(f"  💾 Rapport de validation déposé: {fname}")
                except Exception:
                    # Ne pas échouer la détection pour un problème de log
                    pass
                regimes, regime_probs, features_scaled = self.fit_clustering_fallback(features)
            else:
                regimes, regime_probs, features_scaled = self.fit_hmm_model(features)
        else:
            regimes, regime_probs, features_scaled = self.fit_hmm_model(features)

        final_regimes = self.label_regimes_intelligently(regimes, features)

        # Sauvegarder l'historique
        try:
            self.regime_history = pd.Series(final_regimes, index=features.index, name="regime")
            self.regime_probabilities = pd.DataFrame(regime_probs, index=features.index, columns=[f"{name}_prob" for name in self.regime_names])
        except Exception:
            self.regime_history = pd.Series(final_regimes)
            self.regime_probabilities = pd.DataFrame(regime_probs)

        try:
            self.current_regime = int(final_regimes[-1])
        except Exception:
            self.current_regime = int(final_regimes[0]) if len(final_regimes) > 0 else 0

        print("\n✅ Détection terminée:")
        try:
            print(f"  📊 Régime actuel: {self.regime_names[self.current_regime]}")
            last_probs = regime_probs[-1] if hasattr(regime_probs, "__len__") and len(regime_probs) > 0 else None
            if last_probs is not None and len(last_probs) > self.current_regime:
                print(f"  🎯 Confiance: {last_probs[self.current_regime]*100:.1f}%")
        except Exception:
            pass

        return {
            "regimes": final_regimes,
            "probabilities": regime_probs,
            "features": features,
            "current_regime": self.current_regime,
            "regime_characteristics": self.regime_characteristics,
        }

    def predict_regime_transition(self, current_features, horizon=5):
        if self.transition_matrix is None:
            return None

        current_state = np.zeros(self.n_regimes)
        current_state[self.current_regime] = 1

        future_probabilities = {}
        state = current_state.copy()

        for h in range(1, horizon + 1):
            state = np.dot(state, self.transition_matrix)
            future_probabilities[f"t+{h}"] = {regime_name: prob for regime_name, prob in zip(self.regime_names, state)}

        for period, probs in future_probabilities.items():
            most_likely = max(probs.items(), key=lambda x: x[1])
            print(f"  {period}: {most_likely[0]} ({most_likely[1]*100:.1f}%)")

        return future_probabilities

    def get_regime_strategy_signals(self, current_regime_id):
        if current_regime_id not in self.regime_characteristics:
            return {"action": "hold", "confidence": 0}

        regime = self.regime_characteristics[current_regime_id]
        regime_name = regime["name"].lower()

        if regime_name == "bull":
            return {"action": "long_bias", "confidence": 0.8, "position_size": 1.2, "stop_loss": 0.02, "take_profit": 0.06, "strategy": "momentum_following"}
        elif regime_name == "bear":
            return {"action": "short_bias", "confidence": 0.7, "position_size": 0.8, "stop_loss": 0.015, "take_profit": 0.04, "strategy": "mean_reversion"}
        else:
            return {"action": "range_trading", "confidence": 0.6, "position_size": 1.0, "stop_loss": 0.01, "take_profit": 0.02, "strategy": "mean_reversion"}

    def save_regime_model(self, filepath="artifacts/regime_detection"):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        model_data = {
            "timestamp": datetime.now().isoformat(),
            "n_regimes": self.n_regimes,
            "regime_names": self.regime_names,
            "current_regime": int(self.current_regime) if self.current_regime is not None else None,
            "regime_characteristics": {str(k): v for k, v in self.regime_characteristics.items()},
            "transition_matrix": self.transition_matrix.tolist() if self.transition_matrix is not None else None,
        }

        with open(f"{filepath}_model.json", "w") as f:
            json.dump(model_data, f, indent=2, default=str)

        if hasattr(self, "regime_history") and self.regime_history is not None:
            try:
                self.regime_history.to_csv(f"{filepath}_history.csv")
            except Exception:
                pass

        if hasattr(self, "regime_probabilities") and self.regime_probabilities is not None:
            try:
                self.regime_probabilities.to_csv(f"{filepath}_probabilities.csv")
            except Exception:
                pass

        print(f"💾 Modèle de régimes sauvegardé: {filepath}")


def main():
    print("🎯 TEST SYSTÈME DÉTECTION RÉGIMES")
    print("=" * 35)

    try:
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        detector = MarketRegimeDetector(n_regimes=3, regime_names=["Bear", "Sideways", "Bull"])
        results = detector.detect_regimes(df)

        print("\n🔮 PRÉDICTIONS TRANSITIONS:")
        detector.predict_regime_transition(results["features"].iloc[-1], horizon=5)

        print("\n📊 SIGNAUX STRATÉGIE ADAPTATIVE:")
        strategy_signals = detector.get_regime_strategy_signals(detector.current_regime)
        print(f"  🎯 Action recommandée: {strategy_signals['action']}")
        print(f"  📊 Confiance: {strategy_signals['confidence']*100:.1f}%")

        detector.save_regime_model()

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()


################################################################################
# FROM: scripts\market_regime_detection_safe.py
################################################################################
#!/usr/bin/env python3
"""
Safe copy of market_regime_detection for diagnostics and testing.
Does not replace production file. Use only for offline validation.
"""
import os
import json
from datetime import datetime

import numpy as np
import pandas as pd

from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.metrics import silhouette_score

try:
    from hmmlearn import hmm
    HMM_AVAILABLE = True
except Exception:
    HMM_AVAILABLE = False


class MarketRegimeDetectorSafe:
    """Version safe & hardening pour tests offline."""

    def __init__(self, n_regimes=3, regime_names=None):
        self.n_regimes = n_regimes
        self.regime_names = (regime_names or ["Bear", "Sideways", "Bull"])[:n_regimes]
        self.hmm_model = None
        self.scaler = StandardScaler()
        self.current_regime = None
        self.regime_history = []
        self.regime_probabilities = None
        self.transition_matrix = None
        self.regime_characteristics = {}

    def extract_regime_features(self, df):
        features = pd.DataFrame(index=df.index)

        # detect price column (defensive)
        has_volume = "volume" in df.columns
        price_col = None
        if "close" in df.columns:
            price_col = "close"
        else:
            close_like = [c for c in df.columns if "close" in c.lower()]
            if close_like:
                price_col = close_like[0]
            else:
                for c in df.columns:
                    try:
                        s = pd.to_numeric(df[c], errors="coerce")
                        n = len(s.dropna())
                        if n == 0:
                            continue
                        positive_rate = (s > 0).sum() / n
                        if positive_rate > 0.95:
                            price_col = c
                            break
                    except Exception:
                        continue

        def _price_plausible(series):
            try:
                s = pd.to_numeric(series, errors="coerce").dropna()
                if len(s) == 0:
                    return False
                if (s > 0).sum() / len(s) < 0.95:
                    return False
                if float(np.nanmedian(s)) <= 0:
                    return False
                return True
            except Exception:
                return False

        if price_col is None or not _price_plausible(df[price_col]):
            price_col = None
            close_like = [c for c in df.columns if "close" in c.lower() and _price_plausible(df[c])]
            if close_like:
                price_col = close_like[0]
            else:
                for c in df.columns:
                    if _price_plausible(df[c]):
                        price_col = c
                        break

        if price_col is None:
            raise ValueError("Aucune colonne de prix valide trouvée (safe detector)")

        has_ohlc = all(col in df.columns for col in ["open", "high", "low", "close"]) if df is not None else False

        price_series = pd.to_numeric(df[price_col], errors="coerce")
        price_series = price_series.where(price_series > 0, np.nan)

        raw_returns = price_series.pct_change()
        max_ret = float(os.getenv("REGIME_MAX_RETURN", "0.5"))
        raw_returns = raw_returns.where(raw_returns.abs() <= max_ret, np.nan)

        features["returns"] = raw_returns
        features["volatility"] = features["returns"].rolling(20).std()
        features["log_returns"] = np.log(price_series / price_series.shift(1))

        features["sma_5"] = df[price_col].rolling(5).mean()
        features["sma_20"] = df[price_col].rolling(20).mean()
        features["sma_50"] = df[price_col].rolling(50).mean()

        features["trend_short"] = (features["sma_5"] - features["sma_20"]) / features["sma_20"]
        features["trend_long"] = (features["sma_20"] - features["sma_50"]) / features["sma_50"]

        if "rsi_60T" in df.columns:
            features["rsi"] = df["rsi_60T"]
        else:
            delta = df[price_col].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            features["rsi"] = 100 - (100 / (1 + rs))

        features["rsi_normalized"] = (features["rsi"] - 50) / 50

        sma_bb = df[price_col].rolling(20).mean()
        bb_std = df[price_col].rolling(20).std()
        features["bb_position"] = (df[price_col] - sma_bb) / (2 * bb_std)
        features["bb_squeeze"] = bb_std / sma_bb

        if has_volume:
            features["volume_sma"] = df["volume"].rolling(20).mean()
            features["volume_ratio"] = df["volume"] / features["volume_sma"]
        else:
            features["volume_ratio"] = 1

        if has_ohlc:
            features["true_range"] = np.maximum(
                df["high"] - df["low"],
                np.maximum(
                    abs(df["high"] - df[price_col].shift(1)),
                    abs(df["low"] - df[price_col].shift(1)),
                ),
            )
        else:
            features["true_range"] = abs(df[price_col] - df[price_col].shift(1))

        features["atr"] = features["true_range"].rolling(14).mean()
        features["atr_normalized"] = features["atr"] / df[price_col]

        window = 20
        features["returns_skew"] = features["returns"].rolling(window).skew()
        features["returns_kurtosis"] = features["returns"].rolling(window).kurt()

        peak = df[price_col].expanding().max()
        features["drawdown"] = (df[price_col] - peak) / peak
        features["max_drawdown"] = features["drawdown"].rolling(50).min()

        features["autocorr_1"] = features["returns"].rolling(20).apply(lambda x: x.autocorr(lag=1) if len(x) > 1 else 0)

        features["regime_momentum"] = features["trend_short"] * 0.4 + features["trend_long"] * 0.3 + features["rsi_normalized"] * 0.3

        if "ema_15T" in df.columns:
            features["ema_ratio"] = df["ema_15T"] / df[price_col] - 1

        # cleaning
        features = features.fillna(method="ffill")
        for c in features.columns:
            if features[c].isna().any():
                med = features[c].median()
                if np.isnan(med):
                    med = 0.0
                features[c] = features[c].fillna(med)

        return features

    def fit_hmm_model(self, features):
        if not HMM_AVAILABLE:
            return self.fit_clustering_fallback(features)

        key_features = [
            "returns",
            "volatility",
            "trend_short",
            "trend_long",
            "rsi_normalized",
            "bb_position",
            "regime_momentum",
        ]

        X = features[key_features].fillna(0)

        use_safe = os.getenv("REGIME_SAFE_CLEAN", "0") == "1"
        if use_safe:
            X = X.copy()
            for col in X.columns:
                try:
                    arr = X[col].values.astype(float)
                    arr = np.where(np.isinf(arr), np.nan, arr)
                    lo = np.nanquantile(arr, 0.005)
                    hi = np.nanquantile(arr, 0.995)
                    arr = np.clip(arr, lo, hi)
                    med = np.nanmedian(arr)
                    arr = np.where(np.isnan(arr), med, arr)
                    X[col] = arr
                except Exception:
                    X[col] = X[col].fillna(0)
            scaler = RobustScaler()
        else:
            scaler = self.scaler if self.scaler is not None else StandardScaler()

        X_scaled = scaler.fit_transform(X)
        self.scaler = scaler

        cov_type = os.getenv("REGIME_HMM_COV", "full")
        n_iter = int(os.getenv("REGIME_HMM_ITERS", "100"))
        n_init = int(os.getenv("REGIME_HMM_NINIT", "1"))
        seed = int(os.getenv("REGIME_HMM_SEED", "42"))

        best_model = None
        best_score = -np.inf

        try:
            for attempt in range(max(1, n_init)):
                rs = seed + attempt
                model = hmm.GaussianHMM(n_components=self.n_regimes, covariance_type=cov_type, n_iter=n_iter, random_state=rs)
                model.fit(X_scaled)
                try:
                    score = model.score(X_scaled)
                except Exception:
                    score = -np.inf
                if best_model is None or score > best_score:
                    best_model = model
                    best_score = score

            if best_model is None:
                raise RuntimeError("HMM training failed")

            self.hmm_model = best_model
            regimes = self.hmm_model.predict(X_scaled)
            regime_probs = self.hmm_model.predict_proba(X_scaled)
            self.transition_matrix = self.hmm_model.transmat_
            return regimes, regime_probs, X_scaled

        except Exception:
            return self.fit_clustering_fallback(features)

    def fit_clustering_fallback(self, features):
        cluster_features = ["returns", "volatility", "trend_short", "rsi_normalized", "regime_momentum"]
        X = features[cluster_features].fillna(0)
        X_scaled = self.scaler.fit_transform(X)
        kmeans = KMeans(n_clusters=self.n_regimes, random_state=42, n_init=10)
        regimes = kmeans.fit_predict(X_scaled)
        distances = kmeans.transform(X_scaled)
        regime_probs = np.exp(-distances) / np.exp(-distances).sum(axis=1, keepdims=True)
        self.transition_matrix = self._estimate_transition_matrix(regimes)
        return regimes, regime_probs, X_scaled

    def _estimate_transition_matrix(self, regimes):
        n = len(np.unique(regimes))
        counts = np.zeros((n, n))
        for i in range(len(regimes) - 1):
            counts[regimes[i], regimes[i + 1]] += 1
        tm = counts / counts.sum(axis=1, keepdims=True)
        tm = np.nan_to_num(tm)
        return tm

    def detect_regimes(self, df):
        features = self.extract_regime_features(df)
        regimes, regime_probs, _ = self.fit_hmm_model(features)
        # simple labeling by average return per cluster
        remapped = self._label_by_return(regimes, features)
        self.regime_history = pd.Series(remapped, index=features.index)
        self.regime_probabilities = pd.DataFrame(regime_probs, index=features.index, columns=[f"{n}_prob" for n in self.regime_names])
        self.current_regime = int(remapped[-1])
        return {
            "regimes": remapped,
            "probabilities": regime_probs,
            "features": features,
            "current_regime": self.current_regime,
            "regime_characteristics": self.regime_characteristics,
        }

    def _label_by_return(self, regimes, features):
        stats = {}
        for r in np.unique(regimes):
            mask = regimes == r
            stats[r] = features[mask]["returns"].mean()
        sorted_regimes = sorted(stats.items(), key=lambda x: x[1])
        mapping = {sorted_regimes[i][0]: i for i in range(len(sorted_regimes))}
        remapped = np.array([mapping.get(r, r) for r in regimes])
        return remapped


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--input", default="data/features_sample.csv")
    p.add_argument("--out", default="artifacts/diagnostics/regime_features_safe.json")
    args = p.parse_args()

    if not os.path.exists(os.path.dirname(args.out)):
        os.makedirs(os.path.dirname(args.out), exist_ok=True)

    df = pd.read_csv(args.input)
    if "Unnamed: 0" in df.columns:
        df = df.set_index("Unnamed: 0")
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            pass

    det = MarketRegimeDetectorSafe(n_regimes=3)
    res = det.detect_regimes(df)

    report = {
        "timestamp": datetime.now().isoformat(),
        "current_regime": int(res["current_regime"]),
        "regime_counts": int(len(res["regimes"])),
    }
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print("✅ Safe detector executed. Report written to:", args.out)


# End of merged preview
