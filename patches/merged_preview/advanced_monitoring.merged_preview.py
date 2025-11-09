# Merged preview for prefix: advanced
# Generated from 3 files

################################################################################
# FROM: scripts\advanced_decision_engine.py
################################################################################
"""Stub minimal pour advanced_decision_engine

Ce module fournit une implémentation non-invasive et sûre de
`AdvancedDecisionEngine` lorsque le vrai module est absent.

Le but : supprimer les warnings répétitifs en production et
fournir un point d'insertion pour une future implémentation complète.
La classe retourne systématiquement enhancement_applied=False
pour ne pas altérer le comportement de trading.
"""
from typing import Any, Dict


class AdvancedDecisionEngine:
    """Implementation minimale et sûre du moteur de décision avancé.

    Note: cette version est volontairement non-invasive : elle ne modifie
    pas les signaux et sert uniquement à éviter les ImportError en production.
    """

    def __init__(self, *args, **kwargs):
        # aucun état persistant requis pour le stub
        self.initialized = True

    def make_enhanced_decision(self, symbol: str, data: Any, base_signals: Dict) -> Dict:
        """Retourne une dict indiquant qu'aucune amélioration n'a été appliquée.

        Le format retourné correspond à l'usage attendu par
        `LiveTradingEngine.apply_advanced_decision_engine`.
        """
        try:
            return {
                "enhancement_applied": False,
                "action": base_signals.get("combined_signal", "hold"),
                "confidence": base_signals.get("confidence", 0.0),
            }
        except Exception:
            return {"enhancement_applied": False, "action": "hold", "confidence": 0.0}


################################################################################
# FROM: scripts\advanced_features.py
################################################################################
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


################################################################################
# FROM: scripts\advanced_monitoring.py
################################################################################
#!/usr/bin/env python3
"""
Système de Monitoring et Risk Management Avancé.

Ce système implémente :
- Alertes intelligentes et notifications en temps réel
- Détection automatique de dégradation de performance
- Arrêt d'urgence automatique avec seuils adaptatifs
- Tableau de bord en temps réel avec métriques institutionnelles
- Surveillance des modèles et drift detection
- Rapport de performance automatique
"""

import pandas as pd
import numpy as np
import json
import os
import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
import warnings

warnings.filterwarnings("ignore")

# Email
try:
    from email.mime.text import MimeText
    from email.mime.multipart import MimeMultipart
    import smtplib

    EMAIL_AVAILABLE = True
except ImportError:
    EMAIL_AVAILABLE = False
    print("⚠️  Email non disponible - Pas d'alertes email")

# Visualisation et dashboard
try:
    import matplotlib.pyplot as plt

    plt.style.use("seaborn-v0_8")
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("⚠️  Matplotlib/Seaborn non disponible - Pas de graphiques")

# Note: requests supprimé car inutilisé
REQUESTS_AVAILABLE = False


class AdvancedMonitoringSystem:
    """Système de monitoring et risk management avancé"""

    def __init__(self, alert_thresholds=None, email_config=None):
        """
        Args:
            alert_thresholds: Dictionnaire des seuils d'alerte
            email_config: Configuration email pour notifications
        """
        # Seuils d'alerte par défaut
        self.alert_thresholds = alert_thresholds or {
            "max_drawdown": -0.15,  # -15%
            "consecutive_losses": 5,
            "daily_loss_limit": -0.05,  # -5%
            "volatility_spike": 0.02,  # 2%
            "min_sharpe_ratio": 0.5,
            "max_correlation_breakdown": 0.3,
            "model_accuracy_decline": 0.1,  # 10% de baisse
        }

        # Configuration email
        self.email_config = email_config

        # État du système
        self.monitoring_active = False
        self.emergency_stop_triggered = False
        self.last_alert_time = {}

        # Historique de surveillance
        self.performance_history = []
        self.alert_history = []
        self.risk_events = []

        # Métriques en temps réel
        self.current_metrics = {
            "drawdown": 0,
            "win_rate": 0,
            "sharpe_ratio": 0,
            "total_trades": 0,
            "daily_pnl": 0,
            "volatility": 0,
            "var_95": 0,
            "model_accuracy": 0,
        }

        # Setup logging avancé
        self.setup_advanced_logging()

        print("🛡️  Système Monitoring Avancé initialisé:")
        print(f"  📊 Seuils d'alerte: {len(self.alert_thresholds)} métriques")
        print(f"  📧 Email: {'✅' if email_config else '❌'}")
        print(f"  📈 Graphiques: {'✅' if PLOTTING_AVAILABLE else '❌'}")

    def setup_advanced_logging(self):
        """Configuration du logging avancé avec rotation"""
        os.makedirs("logs", exist_ok=True)

        # Logger principal
        self.logger = logging.getLogger("AdvancedMonitoring")
        self.logger.setLevel(logging.INFO)

        # Handler fichier avec rotation (10MB max, 5 fichiers)
        file_handler = RotatingFileHandler(
            f"logs/monitoring_{datetime.now().strftime('%Y%m%d')}.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
        )
        file_handler.setLevel(logging.INFO)

        # Handler pour alertes critiques
        alert_handler = RotatingFileHandler(
            f"logs/alerts_{datetime.now().strftime('%Y%m%d')}.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
        )
        alert_handler.setLevel(logging.WARNING)

        # Format détaillé
        formatter_pattern = (
            "%(asctime)s - %(name)s - %(levelname)s - "
            "%(funcName)s:%(lineno)d - %(message)s"
        )
        detailed_formatter = logging.Formatter(formatter_pattern)
        file_handler.setFormatter(detailed_formatter)
        alert_handler.setFormatter(detailed_formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(alert_handler)

        self.logger.info("🔧 Système de logging avancé initialisé")

    def analyze_performance_degradation(
        self, recent_trades, historical_baseline
    ):
        """
        Analyser la dégradation de performance

        Args:
            recent_trades: Trades récents à analyser
            historical_baseline: Performance historique de référence
        """
        if not recent_trades:
            return None

        degradation_report = {
            "timestamp": datetime.now(),
            "severity": "normal",
            "issues": [],
            "recommendations": [],
        }

        try:
            # Convertir en DataFrame si nécessaire
            if isinstance(recent_trades, list):
                df_trades = pd.DataFrame(recent_trades)
            else:
                df_trades = recent_trades

            if len(df_trades) == 0:
                return degradation_report

            # 1. Analyser le win rate récent
            if "result" in df_trades.columns:
                recent_win_rate = (
                    df_trades["result"].mean() if len(df_trades) > 0 else 0
                )
                baseline_win_rate = historical_baseline.get("win_rate", 0.6)

                win_rate_decline = baseline_win_rate - recent_win_rate
                if (
                    win_rate_decline
                    > self.alert_thresholds["model_accuracy_decline"]
                ):
                    decline_pct = win_rate_decline * 100
                    degradation_report["issues"].append(
                        {
                            "type": "win_rate_decline",
                            "severity": "high"
                            if win_rate_decline > 0.2
                            else "medium",
                            "value": win_rate_decline,
                            "description": f"Win rate: -{decline_pct:.1f}%",
                        }
                    )
                    degradation_report["recommendations"].append(
                        "Réentraîner les modèles"
                    )

            # 2. Analyser les pertes consécutives
            if "result" in df_trades.columns:
                consecutive_losses = 0
                max_consecutive = 0

                for result in df_trades["result"].values:
                    if result <= 0:
                        consecutive_losses += 1
                        max_consecutive = max(
                            max_consecutive, consecutive_losses
                        )
                    else:
                        consecutive_losses = 0

                if (
                    max_consecutive
                    >= self.alert_thresholds["consecutive_losses"]
                ):
                    degradation_report["issues"].append(
                        {
                            "type": "consecutive_losses",
                            "severity": "critical"
                            if max_consecutive > 8
                            else "high",
                            "value": max_consecutive,
                            "description": f"{max_consecutive} pertes suite",
                        }
                    )
                    degradation_report["recommendations"].append(
                        "Activer arrêt d'urgence"
                    )

            # 3. Analyser la volatilité des résultats
            if "pnl" in df_trades.columns and len(df_trades) > 5:
                recent_volatility = df_trades["pnl"].std()
                baseline_volatility = historical_baseline.get(
                    "volatility", recent_volatility
                )

                volatility_ratio = (
                    recent_volatility / baseline_volatility
                    if baseline_volatility > 0
                    else 1
                )
                if volatility_ratio > 2.0:
                    volatility_desc = f"Volatilité x{volatility_ratio:.1f}"
                    degradation_report["issues"].append(
                        {
                            "type": "volatility_spike",
                            "severity": "medium",
                            "value": volatility_ratio,
                            "description": volatility_desc,
                        }
                    )
                    degradation_report["recommendations"].append(
                        "Réduire la taille des positions"
                    )

            # 4. Analyser le drawdown
            if "cumulative_pnl" in df_trades.columns:
                peak = df_trades["cumulative_pnl"].expanding().max()
                drawdown = (df_trades["cumulative_pnl"] - peak) / peak
                max_drawdown = drawdown.min()

                if max_drawdown < self.alert_thresholds["max_drawdown"]:
                    dd_pct = max_drawdown * 100
                    degradation_report["issues"].append(
                        {
                            "type": "excessive_drawdown",
                            "severity": "critical",
                            "value": max_drawdown,
                            "description": f"Drawdown: {dd_pct:.1f}%",
                        }
                    )
                    degradation_report["recommendations"].append(
                        "Arrêt immédiat du trading"
                    )

            # Déterminer la sévérité globale
            if any(
                issue["severity"] == "critical"
                for issue in degradation_report["issues"]
            ):
                degradation_report["severity"] = "critical"
            elif any(
                issue["severity"] == "high"
                for issue in degradation_report["issues"]
            ):
                degradation_report["severity"] = "high"
            elif degradation_report["issues"]:
                degradation_report["severity"] = "medium"

            nb_issues = len(degradation_report['issues'])
            self.logger.info(f"Analyse dégradation: {nb_issues} problèmes")

            return degradation_report

        except Exception as e:
            self.logger.error(f"Erreur analyse dégradation: {e}")
            return degradation_report

    def check_model_drift(
        self, current_accuracy, historical_accuracy, window=100
    ):
        """
        Détecter le drift des modèles ML

        Args:
            current_accuracy: Précision actuelle
            historical_accuracy: Historique des précisions
            window: Fenêtre d'analyse
        """
        try:
            if len(historical_accuracy) < window:
                return {"drift_detected": False, "severity": "none"}

            # Calculer la précision moyenne historique
            baseline_accuracy = np.mean(historical_accuracy[-window:])

            # Calculer l'écart
            accuracy_decline = baseline_accuracy - current_accuracy

            # Seuils de drift
            if accuracy_decline > 0.15:  # Baisse > 15%
                severity = "critical"
            elif accuracy_decline > 0.10:  # Baisse > 10%
                severity = "high"
            elif accuracy_decline > 0.05:  # Baisse > 5%
                severity = "medium"
            else:
                severity = "none"

            drift_result = {
                "drift_detected": severity != "none",
                "severity": severity,
                "accuracy_decline": accuracy_decline,
                "current_accuracy": current_accuracy,
                "baseline_accuracy": baseline_accuracy,
                "recommendation": self.get_drift_recommendation(severity),
            }

            if drift_result["drift_detected"]:
                self.logger.warning(
                    f"Drift détecté: {accuracy_decline*100:.1f}% de baisse"
                )

            return drift_result

        except Exception as e:
            self.logger.error(f"Erreur détection drift: {e}")
            return {"drift_detected": False, "severity": "error"}

    def get_drift_recommendation(self, severity):
        """Obtenir des recommandations selon la sévérité du drift"""
        recommendations = {
            "critical": [
                "Arrêt immédiat du trading automatique",
                "Réentraînement complet des modèles",
                "Révision des features utilisées",
                "Analyse des changements de marché",
            ],
            "high": [
                "Réduire la taille des positions de 50%",
                "Réentraîner les modèles sur données récentes",
                "Activer mode conservateur",
            ],
            "medium": [
                "Surveillance renforcée",
                "Considérer mise à jour incrémentale",
                "Analyser les nouvelles conditions de marché",
            ],
        }

        return recommendations.get(severity, [])

    def trigger_alert(self, alert_type, severity, message, data=None):
        """
        Déclencher une alerte avec gestion de fréquence

        Args:
            alert_type: Type d'alerte
            severity: Niveau de sévérité
            message: Message d'alerte
            data: Données supplémentaires
        """
        try:
            # Vérifier la fréquence des alertes (éviter le spam)
            alert_key = f"{alert_type}_{severity}"
            now = datetime.now()

            # Délais minimum entre alertes du même type
            min_delays = {
                "critical": timedelta(minutes=5),
                "high": timedelta(minutes=15),
                "medium": timedelta(hours=1),
                "low": timedelta(hours=4),
            }

            if alert_key in self.last_alert_time:
                time_since_last = now - self.last_alert_time[alert_key]
                min_delay = min_delays.get(severity, timedelta(hours=1))

                if time_since_last < min_delay:
                    return  # Skip si trop récent

            # Enregistrer l'alerte
            alert_record = {
                "timestamp": now,
                "type": alert_type,
                "severity": severity,
                "message": message,
                "data": data or {},
            }

            self.alert_history.append(alert_record)
            self.last_alert_time[alert_key] = now

            # Logger l'alerte
            log_level = {
                "critical": logging.CRITICAL,
                "high": logging.ERROR,
                "medium": logging.WARNING,
                "low": logging.INFO,
            }.get(severity, logging.INFO)

            self.logger.log(
                log_level, f"🚨 ALERTE {severity.upper()}: {message}"
            )

            # Envoyer notifications
            self.send_notifications(alert_record)

            # Actions automatiques selon la sévérité
            if severity == "critical":
                self.trigger_emergency_stop(alert_record)

        except Exception as e:
            self.logger.error(f"Erreur déclenchement alerte: {e}")

    def send_notifications(self, alert_record):
        """Envoyer les notifications (email, webhook, etc.)"""
        try:
            # Email notification
            if self.email_config:
                self.send_email_alert(alert_record)

            # Webhook notification (Slack, Discord, etc.)
            if REQUESTS_AVAILABLE and hasattr(self, "webhook_url"):
                self.send_webhook_alert(alert_record)

            # Console notification
            print(
                f"🚨 {alert_record['severity'].upper()}: "
                f"{alert_record['message']}"
            )

        except Exception as e:
            self.logger.error(f"Erreur envoi notifications: {e}")

    def send_email_alert(self, alert_record):
        """Envoyer une alerte par email"""
        try:
            if not self.email_config:
                return

            msg = MimeMultipart()
            msg["From"] = self.email_config["from"]
            msg["To"] = self.email_config["to"]
            msg[
                "Subject"
            ] = f"🚨 Alerte Trading - {alert_record['severity'].upper()}"

            body = f"""
            ALERTE SYSTÈME DE TRADING

            Type: {alert_record['type']}
            Sévérité: {alert_record['severity']}
            Heure: {alert_record['timestamp']}

            Message: {alert_record['message']}

            Données: {json.dumps(alert_record['data'], indent=2, default=str)}

            ---
            Système de Monitoring Automatique
            """

            msg.attach(MimeText(body, "plain"))

            # Envoyer l'email
            server = smtplib.SMTP(
                self.email_config["smtp_server"],
                self.email_config["smtp_port"],
            )
            server.starttls()
            server.login(
                self.email_config["username"], self.email_config["password"]
            )
            text = msg.as_string()
            server.sendmail(
                self.email_config["from"], self.email_config["to"], text
            )
            server.quit()

            self.logger.info("📧 Email d'alerte envoyé")

        except Exception as e:
            self.logger.error(f"Erreur envoi email: {e}")

    def trigger_emergency_stop(self, alert_record):
        """Déclencher l'arrêt d'urgence"""
        try:
            if self.emergency_stop_triggered:
                return  # Déjà activé

            self.emergency_stop_triggered = True

            self.logger.critical("🛑 ARRÊT D'URGENCE DÉCLENCHÉ")

            # Créer le fichier d'arrêt d'urgence
            os.makedirs("control", exist_ok=True)
            emergency_file = "control/emergency_stop.json"

            emergency_data = {
                "triggered_at": alert_record["timestamp"].isoformat(),
                "reason": alert_record["message"],
                "alert_type": alert_record["type"],
                "severity": alert_record["severity"],
                "data": alert_record["data"],
            }

            with open(emergency_file, "w") as f:
                json.dump(emergency_data, f, indent=2, default=str)

            # Sauvegarder les logs d'urgence
            self.save_emergency_report()

            print("🛑 ARRÊT D'URGENCE ACTIVÉ")
            print("Vérifiez control/emergency_stop.json")

        except Exception as e:
            self.logger.error(f"Erreur arrêt d'urgence: {e}")

    def save_emergency_report(self):
        """Sauvegarder un rapport d'urgence complet"""
        try:
            os.makedirs("reports/emergency", exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_file = f"reports/emergency/report_{timestamp}.json"

            emergency_report = {
                "timestamp": datetime.now().isoformat(),
                "current_metrics": self.current_metrics,
                "recent_alerts": self.alert_history[
                    -10:
                ],  # 10 dernières alertes
                "risk_events": self.risk_events,
                "emergency_stop_reason": "Arrêt automatique déclenché",
                "system_state": {
                    "monitoring_active": self.monitoring_active,
                    "emergency_stop_triggered": self.emergency_stop_triggered,
                },
            }

            with open(report_file, "w") as f:
                json.dump(emergency_report, f, indent=2, default=str)

            self.logger.critical(
                f"📋 Rapport d'urgence sauvegardé: {report_file}"
            )

        except Exception as e:
            self.logger.error(f"Erreur sauvegarde rapport urgence: {e}")

    def generate_performance_dashboard(
        self, output_file="reports/dashboard.html"
    ):
        """Générer un tableau de bord HTML"""
        try:
            if not PLOTTING_AVAILABLE:
                self.logger.warning(
                    "Graphiques non disponibles pour le dashboard"
                )
                return False

            os.makedirs(os.path.dirname(output_file), exist_ok=True)

            # Données pour les graphiques
            if not self.performance_history:
                self.logger.info("Pas de données pour le dashboard")
                return False

            df_perf = pd.DataFrame(self.performance_history)

            # Créer les graphiques
            fig, axes = plt.subplots(2, 2, figsize=(15, 10))
            fig.suptitle(
                "Tableau de Bord Trading - Monitoring Temps Réel", fontsize=16
            )

            # 1. Évolution du P&L
            if "cumulative_pnl" in df_perf.columns:
                axes[0, 0].plot(df_perf.index, df_perf["cumulative_pnl"])
                axes[0, 0].set_title("Évolution P&L Cumulé")
                axes[0, 0].grid(True)

            # 2. Drawdown
            if "drawdown" in df_perf.columns:
                axes[0, 1].fill_between(
                    df_perf.index,
                    df_perf["drawdown"],
                    0,
                    alpha=0.7,
                    color="red",
                )
                axes[0, 1].set_title("Drawdown (%)")
                axes[0, 1].grid(True)

            # 3. Win Rate glissant
            if "win_rate" in df_perf.columns:
                axes[1, 0].plot(df_perf.index, df_perf["win_rate"])
                axes[1, 0].axhline(y=0.5, color="r", linestyle="--", alpha=0.5)
                axes[1, 0].set_title("Win Rate")
                axes[1, 0].grid(True)

            # 4. Ratio Sharpe
            if "sharpe_ratio" in df_perf.columns:
                axes[1, 1].plot(df_perf.index, df_perf["sharpe_ratio"])
                axes[1, 1].axhline(y=1.0, color="g", linestyle="--", alpha=0.5)
                axes[1, 1].set_title("Ratio Sharpe")
                axes[1, 1].grid(True)

            plt.tight_layout()

            # Sauvegarder le graphique
            plot_file = output_file.replace(".html", "_plot.png")
            plt.savefig(plot_file, dpi=300, bbox_inches="tight")
            plt.close()

            # Générer le HTML
            html_content = self.generate_dashboard_html(plot_file)

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.logger.info(f"📊 Dashboard généré: {output_file}")
            return True

        except Exception as e:
            self.logger.error(f"Erreur génération dashboard: {e}")
            return False

    def generate_dashboard_html(self, plot_file):
        """Générer le contenu HTML du dashboard"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Statut
        status_text = (
            "🛑 ARRÊT D'URGENCE"
            if self.emergency_stop_triggered
            else "✅ Opérationnel"
        )

        # Métriques actuelles
        metrics_html = ""
        for key, value in self.current_metrics.items():
            if isinstance(value, float):
                formatted_value = f"{value:.4f}"
            else:
                formatted_value = str(value)

            color = (
                "green"
                if key in ["win_rate", "sharpe_ratio"] and value > 0.5
                else "red"
                if value < 0
                else "blue"
            )

            metrics_html += f"""
            <div class="metric">
                <h3>{key.replace('_', ' ').title()}</h3>
                <p style="color: {color}; font-size: 24px;
                   font-weight: bold;">{formatted_value}</p>
            </div>
            """

        # Alertes récentes
        alerts_html = ""
        recent_alerts = self.alert_history[-5:] if self.alert_history else []

        for alert in recent_alerts:
            color = {
                "critical": "#ff4444",
                "high": "#ff8800",
                "medium": "#ffaa00",
                "low": "#00aa00",
            }.get(alert["severity"], "#666666")

            alerts_html += f"""
            <div class="alert" style="border-left: 4px solid {color};">
                <strong>{alert['severity'].upper()}</strong> -
                {alert['type']}<br>
                <small>{alert['timestamp']}</small><br>
                {alert['message']}
            </div>
            """

        # CSS simplifié pour éviter les lignes trop longues
        css_styles = """
            body { font-family: Arial, sans-serif; margin: 20px;
                   background: #f5f5f5; }
            .header { background: #2c3e50; color: white; padding: 20px;
                     border-radius: 8px; margin-bottom: 20px; }
            .metrics { display: grid;
                      grid-template-columns: repeat(auto-fit,
                      minmax(200px, 1fr));
                      gap: 15px; margin-bottom: 20px; }
            .metric { background: white; padding: 15px; border-radius: 8px;
                     box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .chart { background: white; padding: 20px; border-radius: 8px;
                    margin-bottom: 20px; text-align: center; }
            .alerts { background: white; padding: 20px; border-radius: 8px; }
            .alert { margin: 10px 0; padding: 10px; background: #f8f9fa;
                    border-radius: 4px; }
            .status { color: #27ae60; font-weight: bold; }
        """

        plot_basename = os.path.basename(plot_file)
        alerts_content = alerts_html if alerts_html else '<p>Aucune alerte</p>'

        html_template = f"""<!DOCTYPE html>
<html>
<head>
    <title>Dashboard Trading</title>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="60">
    <style>{css_styles}</style>
</head>
<body>
    <div class="header">
        <h1>🛡️ Dashboard Trading - Monitoring</h1>
        <p>Mise à jour: {current_time}</p>
        <p class="status">Statut: {status_text}</p>
    </div>
    <div class="metrics">{metrics_html}</div>
    <div class="chart">
        <h2>📈 Performance</h2>
        <img src="{plot_basename}" alt="Performance" style="max-width: 100%;">
    </div>
    <div class="alerts">
        <h2>🚨 Alertes Récentes</h2>
        {alerts_content}
    </div>
    <div style="text-align: center; margin-top: 20px; color: #666;">
        <p>Monitoring Automatique - Mise à jour toutes les 60 sec</p>
    </div>
</body>
</html>"""

        return html_template

    def run_monitoring_cycle(self, trading_data):
        """Exécuter un cycle complet de monitoring"""
        try:
            if not self.monitoring_active:
                return

            self.logger.info("🔍 Début cycle monitoring")

            # 1. Mettre à jour les métriques
            self.update_current_metrics(trading_data)

            # 2. Analyser la dégradation
            if "trades" in trading_data:
                baseline = {"win_rate": 0.6, "volatility": 0.02}
                degradation = self.analyze_performance_degradation(
                    trading_data["trades"], baseline
                )

                if degradation and degradation["severity"] != "normal":
                    nb_issues = len(degradation['issues'])
                    self.trigger_alert(
                        "performance_degradation",
                        degradation["severity"],
                        f"Dégradation détectée: {nb_issues} problèmes",
                        degradation,
                    )

            # 3. Vérifier le drift des modèles
            if "model_accuracy" in trading_data:
                accuracy_history = trading_data.get("accuracy_history", [])
                drift_result = self.check_model_drift(
                    trading_data["model_accuracy"], accuracy_history
                )

                if drift_result["drift_detected"]:
                    decline_pct = drift_result['accuracy_decline'] * 100
                    self.trigger_alert(
                        "model_drift",
                        drift_result["severity"],
                        f"Drift modèle détecté: {decline_pct:.1f}% de baisse",
                        drift_result,
                    )

            # 4. Vérifier les seuils d'alerte
            self.check_alert_thresholds()

            # 5. Générer le dashboard
            self.generate_performance_dashboard()

            # 6. Enregistrer l'état
            self.performance_history.append(
                {"timestamp": datetime.now(), **self.current_metrics}
            )

            # Limiter l'historique
            if len(self.performance_history) > 1000:
                self.performance_history = self.performance_history[-1000:]

            self.logger.info("✅ Cycle monitoring terminé")

        except Exception as e:
            self.logger.error(f"Erreur cycle monitoring: {e}")

    def update_current_metrics(self, trading_data):
        """Mettre à jour les métriques actuelles"""
        try:
            # Mettre à jour depuis les données de trading
            for key in self.current_metrics:
                if key in trading_data:
                    self.current_metrics[key] = trading_data[key]

            # Calculs dérivés
            if "trades" in trading_data and trading_data["trades"]:
                trades_df = pd.DataFrame(trading_data["trades"])

                if "result" in trades_df.columns:
                    self.current_metrics["win_rate"] = trades_df[
                        "result"
                    ].mean()

                if "pnl" in trades_df.columns:
                    returns = trades_df["pnl"].pct_change().dropna()
                    if len(returns) > 1:
                        self.current_metrics["sharpe_ratio"] = (
                            returns.mean() / returns.std()
                            if returns.std() > 0
                            else 0
                        )
                        self.current_metrics["volatility"] = returns.std()

        except Exception as e:
            self.logger.error(f"Erreur mise à jour métriques: {e}")

    def check_alert_thresholds(self):
        """Vérifier tous les seuils d'alerte"""
        try:
            # Drawdown
            if (
                self.current_metrics["drawdown"]
                < self.alert_thresholds["max_drawdown"]
            ):
                self.trigger_alert(
                    "excessive_drawdown",
                    "critical",
                    f"Drawdown de {self.current_metrics['drawdown']*100:.1f}%",
                )

            # Sharpe ratio
            if (
                self.current_metrics["sharpe_ratio"]
                < self.alert_thresholds["min_sharpe_ratio"]
            ):
                sharpe_val = self.current_metrics['sharpe_ratio']
                self.trigger_alert(
                    "low_sharpe_ratio",
                    "medium",
                    f"Sharpe ratio faible: {sharpe_val:.2f}",
                )

            # Volatilité
            if (
                self.current_metrics["volatility"]
                > self.alert_thresholds["volatility_spike"]
            ):
                vol_pct = self.current_metrics['volatility'] * 100
                self.trigger_alert(
                    "volatility_spike",
                    "high",
                    f"Pic de volatilité: {vol_pct:.2f}%",
                )

        except Exception as e:
            self.logger.error(f"Erreur vérification seuils: {e}")

    def start_monitoring(self):
        """Démarrer le monitoring"""
        self.monitoring_active = True
        self.emergency_stop_triggered = False
        self.logger.info("🚀 Monitoring démarré")

    def stop_monitoring(self):
        """Arrêter le monitoring"""
        self.monitoring_active = False
        self.logger.info("🛑 Monitoring arrêté")


def main():
    """Test du système de monitoring"""
    print("🛡️ TEST SYSTÈME MONITORING AVANCÉ")
    print("=" * 40)

    try:
        # Créer le système de monitoring
        monitoring = AdvancedMonitoringSystem(
            alert_thresholds={
                "max_drawdown": -0.10,
                "consecutive_losses": 3,
                "min_sharpe_ratio": 0.5,
            }
        )

        # Démarrer le monitoring
        monitoring.start_monitoring()

        print("\n🧪 Test des fonctionnalités...")

        # 1. Test données simulées
        test_trading_data = {
            "trades": [
                {"result": 1, "pnl": 100, "timestamp": datetime.now()},
                {"result": 0, "pnl": -50, "timestamp": datetime.now()},
                {"result": 0, "pnl": -75, "timestamp": datetime.now()},
                {"result": 1, "pnl": 200, "timestamp": datetime.now()},
            ],
            "model_accuracy": 0.45,  # Faible pour déclencher alerte
            "accuracy_history": [
                0.65,
                0.63,
                0.61,
                0.58,
                0.55,
                0.52,
                0.48,
                0.45,
            ],
        }

        # 2. Exécuter un cycle de monitoring
        monitoring.run_monitoring_cycle(test_trading_data)

        # 3. Test alerte manuelle
        monitoring.trigger_alert(
            "test_alert",
            "medium",
            "Test du système d'alerte",
            {"test_data": "simulation"},
        )

        # 4. Test dégradation de performance
        print("\n📊 Test analyse dégradation...")
        degradation = monitoring.analyze_performance_degradation(
            test_trading_data["trades"], {"win_rate": 0.7, "volatility": 0.01}
        )

        if degradation:
            print(f"  Problèmes détectés: {len(degradation['issues'])}")
            print(f"  Sévérité: {degradation['severity']}")

        # 5. Test drift des modèles
        print("\n🔍 Test détection drift...")
        drift_result = monitoring.check_model_drift(
            test_trading_data["model_accuracy"],
            test_trading_data["accuracy_history"],
        )

        print(f"  Drift détecté: {drift_result['drift_detected']}")
        if drift_result["drift_detected"]:
            print(f"  Sévérité: {drift_result['severity']}")
            print(f"  Baisse: {drift_result['accuracy_decline']*100:.1f}%")

        # 6. Générer dashboard
        print("\n📈 Génération dashboard...")
        dashboard_generated = monitoring.generate_performance_dashboard()

        if dashboard_generated:
            print("  ✅ Dashboard généré dans reports/dashboard.html")

        # 7. Résumé
        print("\n📋 RÉSUMÉ MONITORING:")
        print(f"  🚨 Alertes générées: {len(monitoring.alert_history)}")
        print(f"  📊 Cycles monitoring: {len(monitoring.performance_history)}")
        stop_status = "Oui" if monitoring.emergency_stop_triggered else "Non"
        print(f"  🛑 Arrêt urgence: {stop_status}")

        # Arrêter le monitoring
        monitoring.stop_monitoring()

        print("\n✅ Test système monitoring terminé")

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()


# End of merged preview
