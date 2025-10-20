#!/usr/bin/env python3
"""
Script pour configurer la collecte de données live MT5 pour l'entraînement
du robot de trading.

Ce script :
1. Vérifie la connexion MT5
2. Configure la collecte automatique de données
3. Démarre la collecte en temps réel pour les features
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta

# Ajouter les dossiers au path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from src.utils.mt5_connector import get_mt5
except ImportError:
    print("⚠️ Module MT5 non disponible, mode simulation")

    def get_mt5():
        return None


try:
    from config.constants import INSTRUMENTS
except ImportError:
    print("⚠️ Constants non disponibles, utilisation par défaut")
    INSTRUMENTS = ["EURUSD", "GBPUSD", "USDJPY"]


def check_mt5_connection():
    """Vérifier la connexion MT5"""
    print("🔍 Vérification de la connexion MT5...")

    mt5 = get_mt5()
    if mt5 is None:
        print("❌ MetaTrader5 non disponible")
        return False

    if not mt5.initialize():
        print("❌ Échec d'initialisation MT5")
        return False

    print("✅ MT5 connecté avec succès")

    # Vérifier les symboles
    for symbol in INSTRUMENTS:
        si = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if si and tick:
            print(f"✅ {symbol}: Disponible (Bid: {tick.bid}, Ask: {tick.ask})")
        else:
            print(f"⚠️  {symbol}: Non disponible ou pas de tick")

    mt5.shutdown()
    return True


def collect_live_features(symbol, timeframe_minutes=5, lookback_hours=24):
    """Collecter les features live pour un symbole"""
    print(f"📊 Collecte des features pour {symbol} ({timeframe_minutes}M)...")

    mt5 = get_mt5()
    if mt5 is None or not mt5.initialize():
        print("❌ Impossible de se connecter à MT5")
        return None

    try:
        # Calculer la période de collecte
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=lookback_hours)

        # Récupérer les données OHLCV
        timeframe_map = {
            1: mt5.TIMEFRAME_M1,
                5: mt5.TIMEFRAME_M5,
                    15: mt5.TIMEFRAME_M15,
                    30: mt5.TIMEFRAME_M30,
                    60: mt5.TIMEFRAME_H1,
                    240: mt5.TIMEFRAME_H4,
                    }

        tf = timeframe_map.get(timeframe_minutes, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_range(symbol, tf, start_time, end_time)

        if rates is None or len(rates) == 0:
            print(f"❌ Aucune donnée pour {symbol}")
            return None

        # Convertir en DataFrame
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.rename(columns={"tick_volume": "volume"})

        # Calculer les indicateurs techniques
        df["sma_20"] = df["close"].rolling(20).mean()
        df["ema_15"] = df["close"].ewm(span=15).mean()
        df["rsi_14"] = calculate_rsi(df["close"], 14)
        df["bb_upper"], df["bb_lower"] = calculate_bollinger_bands(df["close"])
        df["macd"], df["macd_signal"] = calculate_macd(df["close"])

        print(f"✅ {len(df)} points collectés pour {symbol}")
        return df

    except Exception as e:
        print(f"❌ Erreur lors de la collecte: {e}")
        return None
    finally:
        mt5.shutdown()


def calculate_rsi(prices, period=14):
    """Calculer le RSI"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_bollinger_bands(prices, period=20, std_dev=2):
    """Calculer les Bandes de Bollinger"""
    sma = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, lower


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculer le MACD"""
    ema_fast = prices.ewm(span=fast).mean()
    ema_slow = prices.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal).mean()
    return macd, macd_signal


def save_features_for_training(symbol, features_df):
    """Sauvegarder les features pour l'entraînement"""
    if features_df is None or len(features_df) == 0:
        return False

    # Créer le dossier de données live
    os.makedirs("data/live_training", exist_ok=True)

    # Format pour l'entraînement
    training_features = features_df[
        ["time", "close", "volume", "sma_20", "ema_15", "rsi_14"]
    ].copy()
    training_features = training_features.dropna()

    # Sauvegarder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"data/live_training/{symbol}_live_features_{timestamp}.csv"
    training_features.to_csv(filename, index=False)

    print(f"💾 Features sauvegardées: {filename}")
    print(
        f"📈 {len(training_features)} lignes de "
        f"features prêtes pour l'entraînement"
    )

    return True


def setup_automated_collection():
    """Configurer la collecte automatisée"""
    print("🔄 Configuration de la collecte automatisée...")

    # Créer un script de collecte périodique
    script_content = """
import schedule
import time
from setup_live_data_collection import collect_and_save_all_symbols


def job():
    print(f"🕐 Collecte programmée: {datetime.now()}")
    collect_and_save_all_symbols()

# Collecter toutes les 5 minutes
schedule.every(5).minutes.do(job)

print("⏰ Collecte automatique démarrée (toutes les 5 minutes)")
print("Press Ctrl+C to stop...")

while True:
    schedule.run_pending()
    time.sleep(1)
"""

    with open("scripts/automated_collection.py", "w") as f:
        f.write(script_content)

    print(
        "✅ Script de collecte automatique créé: "
        "scripts/automated_collection.py"
    )


def collect_and_save_all_symbols():
    """Collecter et sauvegarder pour tous les symboles"""
    for symbol in INSTRUMENTS:
        print(f"\n🎯 Collecte pour {symbol}...")
        df = collect_live_features(
            symbol, timeframe_minutes=5, lookback_hours=6
        )
        if df is not None:
            save_features_for_training(symbol, df)


def main():
    """Fonction principale"""
    print("🚀 Configuration de la collecte de données live")
    print("=" * 50)

    # 1. Vérifier MT5
    if not check_mt5_connection():
        print("❌ Configuration impossible - MT5 non disponible")
        return

    # 2. Collecte initiale
    print("\n📊 Collecte initiale des données...")
    collect_and_save_all_symbols()

    # 3. Configuration automatique
    print("\n🔄 Configuration de la collecte automatique...")
    setup_automated_collection()

    print("\n✅ Configuration terminée !")
    print("\nPour démarrer la collecte automatique:")
    print("python scripts/automated_collection.py")

    print("\nPour entraîner avec les nouvelles données:")
    print("python scripts/auto_improve_bot.py --use-live-data")


if __name__ == "__main__":
    main()
