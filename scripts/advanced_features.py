#!/usr/bin/env python3
"""
Features engineering avancé pour améliorer les signaux de trading.

Ajoute des indicateurs sophistiqués :
- Volatility clustering (GARCH-like)
- Patterns de microstructure
- Indicateurs de régime de marché
- Features de correlation inter-assets
"""

import pandas as pd
import numpy as np
import os
from scipy import stats
from sklearn.preprocessing import StandardScaler
import warnings

warnings.filterwarnings("ignore")

# Import conditionnel de talib avec fallback robuste
try:
    import talib

    TALIB_AVAILABLE = True
    print("✅ TA-Lib disponible - indicateurs techniques avancés activés")
except ImportError as e:
    TALIB_AVAILABLE = False
    print(f"⚠️  TA-Lib non disponible: {e}")
    print("🔄 Utilisation alternatives basiques pour indicateurs techniques")
except Exception as e:
    TALIB_AVAILABLE = False
    print(f"🔴 Erreur TA-Lib: {e}")
    print("🔄 Utilisation alternatives basiques")


class AdvancedFeatureEngineer:
    """Générateur de features avancées pour le trading"""

    def __init__(self):
        self.scaler = StandardScaler()
        self.lookback_periods = [5, 10, 20, 50]

    def create_volatility_features(self, df):
        """Features de volatilité avancées"""
        features = {}

        # Volatilité réalisée (différentes fenêtres)
        returns = df["close"].pct_change()

        for period in self.lookback_periods:
            vol_key = f"realized_vol_{period}"
            features[vol_key] = returns.rolling(period).std() * np.sqrt(252)

        # GARCH-like volatility clustering
        features["vol_clustering"] = self._garch_like_volatility(returns)

        # Volatility ratio (court terme / long terme)
        vol_5 = returns.rolling(5).std()
        vol_20 = returns.rolling(20).std()
        features["vol_ratio_5_20"] = vol_5 / (vol_20 + 1e-8)

        # Range-based volatility (Garman-Klass)
        if all(col in df.columns for col in ["high", "low", "open"]):
            features["gk_volatility"] = self._garman_klass_volatility(df)

        return features

    def create_microstructure_features(self, df):
        """Features de microstructure du marché"""
        features = {}

        # Bid-ask spread proxy (high-low as approximation)
        if all(col in df.columns for col in ["high", "low"]):
            spread = (df["high"] - df["low"]) / df["close"]
            features["bid_ask_spread_proxy"] = spread
            features["spread_ma"] = spread.rolling(20).mean()

        # Volume profile features
        if "volume" in df.columns:
            features.update(self._volume_profile_features(df))

        # Price impact estimation
        features["price_impact"] = self._estimate_price_impact(df)

        # Market efficiency measures
        features.update(self._market_efficiency_features(df))

        return features

    def create_regime_features(self, df):
        """Features de détection de régime de marché"""
        features = {}

        returns = df["close"].pct_change()

        # Trend strength
        features["trend_strength"] = self._calculate_trend_strength(df)

        # Market state features
        features.update(self._market_state_features(returns))

        # Momentum regime
        features["momentum_regime"] = self._momentum_regime(df)

        # Volatility regime
        vol = returns.rolling(20).std()
        vol_percentile = vol.rolling(252).rank(pct=True)
        # High vol (>80%), Low vol (<20%), Medium vol (20%-80%)
        features["vol_regime"] = np.where(
            vol_percentile > 0.8, 2, np.where(vol_percentile < 0.2, 0, 1)
        )

        return features

    def create_correlation_features(self, df, market_data=None):
        """Features de corrélation inter-assets"""
        features = {}

        if market_data is not None:
            # Corrélation avec indices de marché
            features.update(self._correlation_with_market(df, market_data))

        # Auto-corrélation des rendements
        returns = df["close"].pct_change()
        for lag in [1, 2, 5, 10]:

            def calc_autocorr(x):
                if len(x) > lag:
                    return np.corrcoef(x[:-lag], x[lag:])[0, 1]
                else:
                    return 0

            autocorr = returns.rolling(50).apply(calc_autocorr)
            features[f"autocorr_lag_{lag}"] = autocorr

        # Corrélation volume-prix
        if "volume" in df.columns:
            price_change = df["close"].pct_change()
            vol_change = df["volume"].pct_change()
            corr = price_change.rolling(20).corr(vol_change)
            features["price_vol_corr"] = corr

        return features

    def create_technical_patterns(self, df):
        """Patterns techniques avancés"""
        features = {}

        # Support/Resistance levels
        features.update(self._support_resistance_features(df))

        # Chart patterns
        features.update(self._chart_pattern_features(df))

        # Candlestick patterns (si OHLC disponible)
        if all(col in df.columns for col in ["open", "high", "low", "close"]):
            features.update(self._candlestick_features(df))

        return features

    def _garch_like_volatility(self, returns, alpha=0.1, beta=0.85):
        """Volatilité GARCH-like simplifiée"""
        vol = pd.Series(index=returns.index, dtype=float)
        vol.iloc[0] = returns.std()

        for i in range(1, len(returns)):
            if pd.notna(returns.iloc[i - 1]):
                vol.iloc[i] = np.sqrt(
                    alpha * returns.iloc[i - 1] ** 2
                    + beta * vol.iloc[i - 1] ** 2
                )
            else:
                vol.iloc[i] = vol.iloc[i - 1]

        return vol

    def _garman_klass_volatility(self, df):
        """Volatilité Garman-Klass utilisant OHLC"""
        hl = np.log(df["high"] / df["low"])
        co = np.log(df["close"] / df["open"])

        gk_vol = np.sqrt(0.5 * hl**2 - (2 * np.log(2) - 1) * co**2)
        return gk_vol.rolling(20).mean()

    def _volume_profile_features(self, df):
        """Features basées sur le profil de volume"""
        features = {}

        volume = df["volume"]
        price = df["close"]

        # Volume-weighted average price
        vwap_window = 20
        price_vol_sum = (price * volume).rolling(vwap_window).sum()
        vol_sum = volume.rolling(vwap_window).sum()
        features["vwap"] = price_vol_sum / vol_sum

        # Volume trend
        features["volume_sma"] = volume.rolling(20).mean()
        features["volume_ratio"] = volume / features["volume_sma"]

        # Volume-price divergence
        def calc_trend(x):
            if len(x) == 10:
                return stats.linregress(range(len(x)), x)[0]
            else:
                return 0

        price_trend = price.rolling(10).apply(calc_trend)
        volume_trend = volume.rolling(10).apply(calc_trend)

        # Divergence volume-prix
        price_sign = np.sign(price_trend)
        volume_sign = np.sign(volume_trend)
        features["vol_price_divergence"] = price_sign != volume_sign

        return features

    def _estimate_price_impact(self, df):
        """Estimation de l'impact prix"""
        if "volume" not in df.columns:
            return pd.Series(0, index=df.index)

        returns = df["close"].pct_change().abs()
        volume_norm = df["volume"] / df["volume"].rolling(20).mean()

        # Impact proxy: rendement / volume normalisé
        price_impact = returns / (volume_norm + 1e-8)
        return price_impact.rolling(10).mean()

    def _market_efficiency_features(self, df):
        """Mesures d'efficience du marché"""
        features = {}

        returns = df["close"].pct_change()

        # Variance ratio test
        features["variance_ratio"] = self._variance_ratio(returns, 5)

        # Hurst exponent
        features["hurst_exponent"] = returns.rolling(100).apply(
            lambda x: self._hurst_exponent(x) if len(x) == 100 else 0.5
        )

        return features

    def _calculate_trend_strength(self, df):
        """Force de la tendance"""
        # ADX-like calculation
        high = df.get("high", df["close"])
        low = df.get("low", df["close"])
        close = df["close"]

        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional Movement
        dm_plus = np.where(
            (high - high.shift(1)) > (low.shift(1) - low),
                np.maximum(high - high.shift(1), 0),
                    0,
                    )
        dm_minus = np.where(
            (low.shift(1) - low) > (high - high.shift(1)),
                np.maximum(low.shift(1) - low, 0),
                    0,
                    )

        # ADX approximation
        dm_plus_sum = pd.Series(dm_plus).rolling(14).sum()
        dm_minus_sum = pd.Series(dm_minus).rolling(14).sum()
        tr_sum = tr.rolling(14).sum()

        di_plus = 100 * dm_plus_sum / tr_sum
        di_minus = 100 * dm_minus_sum / tr_sum

        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus + 1e-8)
        adx = dx.rolling(14).mean()

        return adx

    def _market_state_features(self, returns):
        """Features d'état du marché"""
        features = {}

        # Skewness et Kurtosis
        features["skewness"] = returns.rolling(50).skew()
        features["kurtosis"] = returns.rolling(50).kurt()

        # VIX-like fear index
        vol_current = returns.rolling(20).std()
        vol_longterm = returns.rolling(252).std()
        features["fear_index"] = vol_current / (vol_longterm + 1e-8)

        return features

    def _momentum_regime(self, df):
        """Régime de momentum"""
        short_ma = df["close"].rolling(10).mean()
        long_ma = df["close"].rolling(50).mean()

        momentum_signal = (short_ma - long_ma) / long_ma

        # Classification en régimes
        return np.where(
            momentum_signal > 0.02,
                1,  # Strong uptrend
            np.where(momentum_signal < -0.02, -1, 0),
                )  # Strong downtrend / Sideways

    def _correlation_with_market(self, df, market_data):
        """Corrélation avec données de marché"""
        features = {}

        for market_name, market_df in market_data.items():
            if "close" in market_df.columns:
                market_returns = market_df["close"].pct_change()
                asset_returns = df["close"].pct_change()

                # Corrélation glissante
                reindexed_market = market_returns.reindex(asset_returns.index)
                correlation_key = f"corr_{market_name}"
                features[correlation_key] = asset_returns.rolling(50).corr(
                    reindexed_market
                )

        return features

    def _support_resistance_features(self, df):
        """Features support/résistance"""
        features = {}

        close = df["close"]

        # Local minima/maxima
        window = 10
        local_min = close.rolling(window * 2 + 1, center=True).min() == close
        local_max = close.rolling(window * 2 + 1, center=True).max() == close

        # Distance aux niveaux S/R
        resistance_levels = close[local_max].dropna()
        support_levels = close[local_min].dropna()

        if len(resistance_levels) > 0:
            features["dist_to_resistance"] = close.apply(
                lambda x: min(abs(x - r) for r in resistance_levels) / x
            )

        if len(support_levels) > 0:
            features["dist_to_support"] = close.apply(
                lambda x: min(abs(x - s) for s in support_levels) / x
            )

        return features

    def _chart_pattern_features(self, df):
        """Patterns de charts"""
        features = {}

        close = df["close"]

        # Double top/bottom approximation
        features["double_top"] = self._detect_double_pattern(close, "top")
        features["double_bottom"] = self._detect_double_pattern(
            close, "bottom"
        )

        # Breakout detection
        features["breakout_up"] = self._detect_breakout(close, direction="up")
        features["breakout_down"] = self._detect_breakout(
            close, direction="down"
        )

        return features

    def _candlestick_features(self, df):
        """Patterns de chandelles japonaises"""
        features = {}

        if TALIB_AVAILABLE and all(
            col in df.columns for col in ["open", "high", "low", "close"]
        ):
            try:
                # Utiliser TA-Lib si disponible
                open_price = df["open"].values
                high_price = df["high"].values
                low_price = df["low"].values
                close_price = df["close"].values

                # Patterns de chandelles
                doji_pattern = talib.CDLDOJI(
                    open_price, high_price, low_price, close_price
                )
                features["doji"] = pd.Series(doji_pattern, index=df.index)

                hammer_pattern = talib.CDLHAMMER(
                    open_price, high_price, low_price, close_price
                )
                features["hammer"] = pd.Series(hammer_pattern, index=df.index)

                engulfing_pattern = talib.CDLENGULFING(
                    open_price, high_price, low_price, close_price
                )
                features["engulfing"] = pd.Series(
                    engulfing_pattern, index=df.index
                )

            except Exception as e:
                print(f"⚠️  Erreur TA-Lib: {e}")
                features = self._simple_candlestick_features(df)
        else:
            # Fallback simple si TA-Lib pas disponible ou colonnes
            # OHLC manquantes
            features = self._simple_candlestick_features(df)

        return features

    def _simple_candlestick_features(self, df):
        """Implémentation simple des patterns de chandelles"""
        features = {}

        if all(col in df.columns for col in ["open", "high", "low", "close"]):
            # Fallback avec données OHLC complètes
            body_size = abs(df["close"] - df["open"])
            full_range = df["high"] - df["low"] + 1e-8
            upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)
            lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]

            features["doji"] = (body_size / full_range) < 0.1
            features["hammer"] = (
                (lower_shadow > 2 * body_size)
                & (upper_shadow < 0.3 * body_size)
                & (body_size / full_range > 0.1)
            ).astype(int)
            features["engulfing"] = (
                (body_size > body_size.shift(1))
                & (body_size > 1.5 * body_size.rolling(5).mean())
            ).astype(int)
        else:
            # Fallback avec seulement 'close' (cas le plus courant)
            close = df["close"] if "close" in df.columns else df.iloc[:, 0]

            # Patterns basiques basés sur les mouvements de prix
            returns = close.pct_change()
            vol = returns.rolling(20).std()

            features["doji"] = (abs(returns) < 0.5 * vol).astype(int)
            hammer_condition = (returns > 2 * vol) & (returns.shift(1) < 0)
            features["hammer"] = hammer_condition.astype(int)
            features["engulfing"] = (
                (abs(returns) > 1.5 * vol) & (returns * returns.shift(1) < 0)
            ).astype(int)

        return features

    def _variance_ratio(self, returns, k):
        """Test de ratio de variance"""
        try:
            n = len(returns.dropna())
            if n < k:
                return 1.0

            # Variance des rendements k-périodes
            returns_k = returns.rolling(k).sum().dropna()
            var_k = returns_k.var()

            # Variance des rendements 1-période
            var_1 = returns.dropna().var()

            # Ratio de variance
            vr = var_k / (k * var_1 + 1e-8)
            return vr
        except Exception:
            return 1.0

    def _hurst_exponent(self, ts):
        """Calcul de l'exposant de Hurst"""
        try:
            lags = range(2, min(20, len(ts) // 2))
            tau = []
            for lag in lags:
                diff = np.subtract(ts[lag:], ts[:-lag])
                tau_value = np.sqrt(np.std(diff))
                tau.append(tau_value)

            poly = np.polyfit(np.log(lags), np.log(tau), 1)
            return poly[0] * 2.0
        except Exception:
            return 0.5

    def _detect_double_pattern(self, price, pattern_type="top"):
        """Détection de double top/bottom simplifiée"""
        window = 20
        if pattern_type == "top":
            peaks = price.rolling(window * 2 + 1, center=True).max() == price
        else:
            peaks = price.rolling(window * 2 + 1, center=True).min() == price

        # Simplification: compter les pics récents
        return peaks.rolling(window * 4).sum()

    def _detect_breakout(self, price, direction="up", window=20):
        """Détection de breakout"""
        if direction == "up":
            resistance = price.rolling(window).max().shift(1)
            return (price > resistance).astype(int)
        else:
            support = price.rolling(window).min().shift(1)
            return (price < support).astype(int)

    def generate_all_features(self, df, market_data=None):
        """Génère toutes les features avancées"""
        print("🔧 Génération des features avancées...")

        all_features = {}

        # Features de base (garder les existantes)
        basic_features = ["close", "volume"]
        if "sma_1T" in df.columns:
            basic_features.extend(["sma_1T", "ema_15T", "rsi_60T"])

        for feat in basic_features:
            if feat in df.columns:
                all_features[feat] = df[feat]

        # Features avancées
        print("  Volatilité...")
        all_features.update(self.create_volatility_features(df))

        print("  Microstructure...")
        all_features.update(self.create_microstructure_features(df))

        print("  Régimes de marché...")
        all_features.update(self.create_regime_features(df))

        print("  Corrélations...")
        all_features.update(self.create_correlation_features(df, market_data))

        print("  Patterns techniques...")
        all_features.update(self.create_technical_patterns(df))

        # Créer le DataFrame final
        feature_df = pd.DataFrame(all_features, index=df.index)

        # Nettoyer les valeurs infinies/NaN
        feature_df = feature_df.replace([np.inf, -np.inf], np.nan)
        # Correction pour pandas récent
        feature_df = feature_df.ffill().bfill()

        print(f"✅ {len(feature_df.columns)} features générées")

        return feature_df


def main():
    """Test des features avancées"""
    print("🚀 Test du générateur de features avancées")

    # Charger les données
    try:
        # Essayer différents chemins pour les données
        data_paths = [
            "../data/features_enhanced.csv",
                "../data/features_sample.csv",
                    "data/features_enhanced.csv",
                    "data/features_sample.csv",
                    ]

        df = None
        for path in data_paths:
            if os.path.exists(path):
                df = pd.read_csv(path)
                print(f"✅ Données chargées: {path}")
                break

        if df is None:
            print("❌ Aucun fichier de données trouvé")
            return
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        print(f"📊 Données chargées: {len(df)} échantillons")

        # Créer le générateur
        engineer = AdvancedFeatureEngineer()

        # Générer toutes les features
        enhanced_df = engineer.generate_all_features(df)

        print(f"\n📈 Features originales: {len(df.columns)}")
        print(f"📈 Features enrichies: {len(enhanced_df.columns)}")
        improvement = len(enhanced_df.columns) - len(df.columns)
        print(f"🎯 Amélioration: +{improvement} features")

        # Sauvegarder
        # Sauvegarder dans le bon répertoire
        output_path = "../data/features_enhanced_advanced.csv"
        enhanced_df.to_csv(output_path)
        print("\n💾 Features sauvegardées: data/features_enhanced.csv")

        # Afficher les nouvelles features
        existing_cols = df.columns
        new_features = [
            col for col in enhanced_df.columns if col not in existing_cols
        ]
        print(f"\n🆕 Nouvelles features ({len(new_features)}):")
        # Afficher les 20 premières
        for i, feat in enumerate(new_features[:20]):
            print(f"  {i+1}. {feat}")
        if len(new_features) > 20:
            print(f"  ... et {len(new_features)-20} autres")

    except Exception as e:
        print(f"❌ Erreur: {e}")


if __name__ == "__main__":
    main()
