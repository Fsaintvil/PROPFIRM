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
