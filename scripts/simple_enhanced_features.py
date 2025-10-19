#!/usr/bin/env python3
"""
Features engineering avancé simplifié - Sans dépendances TA-Lib
"""

import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")


def create_enhanced_features(df):
    """Créer des features enrichies à partir des données existantes"""
    print("🔧 Génération des features avancées (version simplifiée)...")

    # Commencer avec les features existantes
    enhanced = df.copy()

    # 1. Features de volatilité
    print("  📊 Features de volatilité...")
    returns = df["close"].pct_change()

    # Volatilité réalisée sur différentes fenêtres
    for window in [5, 10, 20]:
        enhanced[f"realized_vol_{window}"] = returns.rolling(
            window
        ).std() * np.sqrt(252)

    # Volatility clustering (GARCH-like simple)
    vol_5 = returns.rolling(5).std()
    vol_20 = returns.rolling(20).std()
    enhanced["vol_ratio"] = vol_5 / (vol_20 + 1e-8)

    # 2. Features de momentum
    print("  🚀 Features de momentum...")

    # ROC (Rate of Change) sur différentes périodes
    for period in [3, 7, 14]:
        enhanced[f"roc_{period}"] = df["close"].pct_change(period)

    # Williams %R
    high_14 = df["close"].rolling(14).max()  # Approximation avec close
    low_14 = df["close"].rolling(14).min()
    enhanced["williams_r"] = (
        -100 * (high_14 - df["close"]) / (high_14 - low_14 + 1e-8)
    )

    # 3. Features de tendance
    print("  📈 Features de tendance...")

    # Multiple moving averages
    for ma_period in [7, 14, 21]:
        enhanced[f"sma_{ma_period}"] = df["close"].rolling(ma_period).mean()
        enhanced[f"ema_{ma_period}"] = df["close"].ewm(span=ma_period).mean()

        # Distance relative à la MA
        enhanced[f"price_vs_sma_{ma_period}"] = (
            df["close"] - enhanced[f"sma_{ma_period}"]
        ) / enhanced[f"sma_{ma_period}"]

    # ADX approximation (trend strength)
    enhanced["trend_strength"] = calculate_trend_strength(df)

    # 4. Features de volume (si disponible)
    if "volume" in df.columns:
        print("  📦 Features de volume...")

        # Volume moving averages
        enhanced["volume_sma_10"] = df["volume"].rolling(10).mean()
        enhanced["volume_ratio"] = df["volume"] / enhanced["volume_sma_10"]

        # VWAP approximation
        enhanced["vwap_5"] = (df["close"] * df["volume"]).rolling(
            5
        ).sum() / df["volume"].rolling(5).sum()
        enhanced["price_vs_vwap"] = (
            df["close"] - enhanced["vwap_5"]
        ) / enhanced["vwap_5"]

    # 5. Features statistiques
    print("  📊 Features statistiques...")

    # Z-score du prix
    enhanced["price_zscore_20"] = (
        df["close"] - df["close"].rolling(20).mean()
    ) / df["close"].rolling(20).std()

    # Percentile rank
    enhanced["price_percentile_50"] = df["close"].rolling(50).rank(pct=True)

    # Skewness et Kurtosis des rendements
    enhanced["returns_skew_20"] = returns.rolling(20).skew()
    enhanced["returns_kurt_20"] = returns.rolling(20).kurt()

    # 6. Features de cycles et saisonnalité
    print("  🔄 Features cycliques...")

    if hasattr(df.index, "hour"):
        # Features temporelles
        enhanced["hour"] = df.index.hour
        enhanced["day_of_week"] = df.index.dayofweek
        enhanced["is_weekend"] = (df.index.dayofweek >= 5).astype(int)

        # Cycles sinusoidaux
        enhanced["hour_sin"] = np.sin(2 * np.pi * df.index.hour / 24)
        enhanced["hour_cos"] = np.cos(2 * np.pi * df.index.hour / 24)

    # 7. Features de support/résistance
    print("  🎯 Features S/R...")

    # Local min/max
    window = 10
    enhanced["local_max"] = (
        df["close"].rolling(window * 2 + 1, center=True).max() == df["close"]
    )
    enhanced["local_min"] = (
        df["close"].rolling(window * 2 + 1, center=True).min() == df["close"]
    )

    # Distance aux extremes récents
    recent_high = df["close"].rolling(20).max()
    recent_low = df["close"].rolling(20).min()
    enhanced["dist_to_high"] = (recent_high - df["close"]) / df["close"]
    enhanced["dist_to_low"] = (df["close"] - recent_low) / df["close"]

    # 8. Features de corrélation
    print("  🔗 Features de corrélation...")

    # Auto-corrélation des rendements
    for lag in [1, 2, 5]:
        enhanced[f"returns_autocorr_{lag}"] = returns.rolling(30).apply(
            lambda x: x.autocorr(lag) if len(x) >= lag + 1 else 0
        )

    # 9. Features de régime de marché
    print("  🌍 Features de régime...")

    # Bull/Bear market indicator
    sma_short = df["close"].rolling(10).mean()
    sma_long = df["close"].rolling(50).mean()
    enhanced["bull_bear_regime"] = np.where(sma_short > sma_long, 1, -1)

    # Volatility regime
    vol_current = returns.rolling(20).std()
    vol_percentile = vol_current.rolling(100).rank(pct=True)
    enhanced["vol_regime"] = np.where(
        vol_percentile > 0.8,
        2,
        np.where(vol_percentile < 0.2, 0, 1),  # High vol
    )  # Low/Medium

    # 10. Features avancées de price action
    print("  💹 Features de price action...")

    # Body vs wicks (approximation avec close seulement)
    enhanced["price_range_5"] = (
        df["close"].rolling(5).max() - df["close"].rolling(5).min()
    )
    enhanced["price_position"] = (
        df["close"] - df["close"].rolling(5).min()
    ) / (enhanced["price_range_5"] + 1e-8)

    # Gap detection (approximation)
    enhanced["gap"] = abs(df["close"] - df["close"].shift(1)) / df[
        "close"
    ].shift(1)
    enhanced["gap_up"] = (df["close"] > df["close"].shift(1) * 1.002).astype(
        int
    )
    enhanced["gap_down"] = (df["close"] < df["close"].shift(1) * 0.998).astype(
        int
    )

    # Nettoyer les données
    enhanced = enhanced.replace([np.inf, -np.inf], np.nan)
    enhanced = enhanced.fillna(method="ffill").fillna(method="bfill").fillna(0)

    print(
        f"✅ Features créées: {len(enhanced.columns)} "
        f"(originales: {len(df.columns)})"
    )

    return enhanced


def calculate_trend_strength(df):
    """Calcul simplifié de la force de tendance (ADX-like)"""
    try:
        close = df["close"]

        # True Range approximation
        tr = abs(close - close.shift(1))

        # Directional Movement approximation
        dm_plus = np.where(close > close.shift(1), close - close.shift(1), 0)
        dm_minus = np.where(close < close.shift(1), close.shift(1) - close, 0)

        # Smooth avec rolling
        tr_smooth = pd.Series(tr).rolling(14).mean()
        dm_plus_smooth = pd.Series(dm_plus).rolling(14).mean()
        dm_minus_smooth = pd.Series(dm_minus).rolling(14).mean()

        # DI calculation
        di_plus = 100 * dm_plus_smooth / tr_smooth
        di_minus = 100 * dm_minus_smooth / tr_smooth

        # ADX calculation
        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus + 1e-8)
        adx = dx.rolling(14).mean()

        return adx.fillna(50)  # Valeur neutre par défaut

    except Exception:
        return pd.Series(50, index=df.index)  # Fallback


def enhance_existing_features():
    """Améliorer le fichier de features existant"""
    print("🚀 Amélioration des features existantes")
    print("=" * 50)

    try:
        # Charger les données existantes
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        print(
            f"📊 Données originales: {len(df)} échantillons, "
            f"{len(df.columns)} features"
        )

        # Créer les features enrichies
        enhanced_df = create_enhanced_features(df)

        # Statistiques
        original_features = len(df.columns)
        enhanced_features = len(enhanced_df.columns)
        new_features = enhanced_features - original_features

        print("\n📈 RÉSULTATS:")
        print(f"Features originales: {original_features}")
        print(f"Features enrichies: {enhanced_features}")
        print(f"Nouvelles features: +{new_features}")
        print(f"Amélioration: +{new_features/original_features*100:.0f}%")

        # Sauvegarder
        enhanced_df.to_csv("data/features_enhanced.csv")
        print("\n💾 Features sauvegardées: data/features_enhanced.csv")

        # Afficher quelques nouvelles features
        new_feature_names = [
            col for col in enhanced_df.columns if col not in df.columns
        ]

        print("\n🆕 Aperçu des nouvelles features:")
        for i, feat in enumerate(new_feature_names[:15]):
            non_na_count = enhanced_df[feat].count()
            mean_val = enhanced_df[feat].mean()
            print(
                f"  {i+1:2d}. {feat:<25} (Valides: {non_na_count:3d}, "
                f"Moy: {mean_val:8.4f})"
            )

        if len(new_feature_names) > 15:
            print(f"  ... et {len(new_feature_names)-15} autres features")

        # Vérifier la qualité des features
        print("\n🔍 QUALITÉ DES FEATURES:")

        # Features avec trop de NaN
        nan_counts = enhanced_df.isnull().sum()
        high_nan_features = nan_counts[nan_counts > len(enhanced_df) * 0.1]
        if len(high_nan_features) > 0:
            print(f"⚠️  Features avec >10% NaN: {len(high_nan_features)}")

        # Features avec variance nulle
        zero_var_features = enhanced_df.var() == 0
        if zero_var_features.any():
            print(f"⚠️  Features constantes: {zero_var_features.sum()}")
        else:
            print("✅ Aucune feature constante")

        # Features corrélées
        corr_matrix = enhanced_df.corr().abs()
        high_corr_pairs = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                if corr_matrix.iloc[i, j] > 0.95:
                    high_corr_pairs.append(
                        (corr_matrix.columns[i], corr_matrix.columns[j])
                    )

        if high_corr_pairs:
            print(f"⚠️  Paires très corrélées (>95%): {len(high_corr_pairs)}")
        else:
            print("✅ Pas de corrélation excessive")

        print("\n✅ Features enrichies créées avec succès !")

        return enhanced_df

    except Exception as e:
        print(f"❌ Erreur: {e}")
        return None


if __name__ == "__main__":
    enhance_existing_features()
