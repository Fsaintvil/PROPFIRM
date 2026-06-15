"""Phase 1 v2 : Téléchargement multi-timeframes (M15/H1/H4/D1) + 100+ features techniques."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import MetaTrader5 as mt5
from datetime import datetime as dt, timedelta
import pandas as pd
import numpy as np
import json
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("data_pipeline")

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]
TIMEFRAMES = {
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
MAX_CANDLES = {
    "M15": 300000,  # ~12 ans
    "H1": 200000,   # ~23 ans
    "H4": 100000,   # ~45 ans
    "D1": 10000,    # ~40 ans
}
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
FEATURES_DIR = DATA_DIR / "features"
METADATA_FILE = DATA_DIR / "metadata.json"

COLUMNS_TARGET = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
RENAME_MAP = {"time": "timestamp", "tick_volume": "volume"}


def ensure_dirs():
    for d in [DATA_DIR, RAW_DIR, FEATURES_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    logger.info(f"Répertoires créés dans {DATA_DIR}")


def init_mt5():
    if not mt5.initialize():
        logger.error(f"MT5 init échouée: {mt5.last_error()}")
        return False
    acc = mt5.account_info()
    logger.info(f"MT5 connecté - compte {acc.login if acc else '?'}")
    return True


def download_symbol(symbol: str, tf_name: str = "H1", max_candles: int = 100000) -> pd.DataFrame | None:
    """Télécharge l'historique complet d'un symbole par lots."""
    tf = TIMEFRAMES[tf_name]
    all_rates = []
    batch_size = 50000
    offset = 0
    
    logger.info(f"Téléchargement {symbol} ({tf_name}, max={max_candles})...")
    
    while offset < max_candles:
        rates = mt5.copy_rates_from_pos(symbol, tf, offset, batch_size)
        if rates is None or len(rates) == 0:
            if offset == 0:
                logger.error(f"  {symbol}: aucune donnée disponible")
                return None
            break
        
        all_rates.append(pd.DataFrame(rates))
        n = len(rates)
        first_time = dt.fromtimestamp(rates[0][0])
        last_time = dt.fromtimestamp(rates[-1][0])
        logger.info(f"  Lot {offset//batch_size + 1}: {n} bougies, {first_time} → {last_time}")
        
        offset += n
        if n < batch_size:
            break
        time.sleep(0.5)
    
    if not all_rates:
        return None
    
    df = pd.concat(all_rates, ignore_index=True)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df = df[COLUMNS_TARGET]
    df = df.rename(columns=RENAME_MAP)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df["symbol"] = symbol
    
    logger.info(f"  Total {symbol}: {len(df)} bougies, {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def add_features_100(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute 100+ features techniques (catégories : momentum, volatilité, volume, structure, micro)."""
    df = df.copy()
    
    # ── 1. RETURNS & MOMENTUM (15 features) ──
    for p in [1, 2, 3, 5, 10, 20, 40]:
        df[f"return_{p}"] = df["close"].pct_change(p)
    for p in [5, 10, 20, 40]:
        df[f"return_log_{p}"] = np.log(df["close"] / df["close"].shift(p))
    df["return_1_abs"] = df["return_1"].abs()
    df["return_20_skew"] = df["return_1"].rolling(20).skew()
    df["return_20_kurt"] = df["return_1"].rolling(20).kurt()
    
    # ── 2. CANDLE PATTERN (10 features) ──
    df["range"] = df["high"] - df["low"]
    df["range_pct"] = df["range"] / df["close"] * 100
    df["body"] = df["close"] - df["open"]
    df["body_abs"] = df["body"].abs()
    df["body_pct"] = df["body_abs"] / df["range"].replace(0, np.nan)
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["upper_wick_pct"] = df["upper_wick"] / df["range"].replace(0, np.nan)
    df["lower_wick_pct"] = df["lower_wick"] / df["range"].replace(0, np.nan)
    df["candle_dir"] = np.sign(df["body"])  # -1: bear, 0: doji, +1: bull
    df["candle_strength"] = df["body_abs"] / df["range"].replace(0, np.nan) * 100
    df["is_doji"] = (df["body_abs"] < df["range"] * 0.1).astype(int)
    df["is_marubozu"] = ((df["upper_wick"] < df["range"] * 0.05) & (df["lower_wick"] < df["range"] * 0.05)).astype(int)
    df["is_hammer"] = ((df["body_abs"] < df["range"] * 0.4) & (df["lower_wick"] > df["body_abs"] * 2)).astype(int)
    df["is_shooting_star"] = ((df["body_abs"] < df["range"] * 0.4) & (df["upper_wick"] > df["body_abs"] * 2)).astype(int)
    
    # ── 3. VOLATILITÉ (12 features) ──
    for p in [7, 14, 20, 30, 50]:
        df[f"atr_{p}"] = df["range"].rolling(p).mean()
        df[f"atr_norm_{p}"] = df[f"atr_{p}"] / df["close"] * 100
    df["volatility_10"] = df["return_1"].rolling(10).std()
    df["volatility_20"] = df["return_20"].rolling(20).std()
    df["volatility_ratio_10_50"] = df["volatility_10"] / df["return_1"].rolling(50).std().replace(0, np.nan)
    df["bollinger_width"] = (df["close"].rolling(20).std() * 4) / df["close"].rolling(20).mean()
    df["range_ratio_5_50"] = df["range"].rolling(5).mean() / df["range"].rolling(50).mean().replace(0, np.nan)
    
    # ── 4. MOYENNES MOBILES (20 features) ──
    for period in [5, 10, 15, 20, 30, 50, 100, 200]:
        df[f"sma_{period}"] = df["close"].rolling(period).mean()
        df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()
        df[f"dist_ema_{period}_pct"] = (df["close"] - df[f"ema_{period}"]) / df[f"ema_{period}"] * 100
    # Croisements de moyennes
    df["ema_10_30_cross"] = df["ema_10"] - df["ema_30"]
    df["ema_30_100_cross"] = df["ema_30"] - df["ema_100"]
    df["sma_50_200_cross"] = df["sma_50"] - df["sma_200"]
    df["ma_trend"] = np.where(df["ema_10_30_cross"] > 0, 1, -1)
    
    # ── 5. RSI & OSCILLATEURS (10 features) ──
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    df["rsi_7"] = 100 - (100 / (1 + (delta.where(delta > 0, 0).rolling(7).mean() / (-delta.where(delta < 0, 0)).rolling(7).mean().replace(0, np.nan))))
    df["rsi_14_roc"] = df["rsi_14"].diff(3)
    df["rsi_divergence"] = np.where(
        (df["close"] > df["close"].shift(5)) & (df["rsi_14"] < df["rsi_14"].shift(5)), -1,
        np.where((df["close"] < df["close"].shift(5)) & (df["rsi_14"] > df["rsi_14"].shift(5)), 1, 0)
    )
    # Stochastic
    k_period = 14
    df["stoch_k"] = ((df["close"] - df["low"].rolling(k_period).min()) /
                     (df["high"].rolling(k_period).max() - df["low"].rolling(k_period).min()).replace(0, np.nan)) * 100
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()
    # Williams %R
    df["williams_r"] = ((df["high"].rolling(14).max() - df["close"]) /
                        (df["high"].rolling(14).max() - df["low"].rolling(14).min()).replace(0, np.nan)) * -100
    # CCI
    tp = (df["high"] + df["low"] + df["close"]) / 3
    df["cci_20"] = (tp - tp.rolling(20).mean()) / (tp.rolling(20).std().replace(0, np.nan) * 0.015)
    
    # ── 6. MACD & TREND (8 features) ──
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["macd_hist_roc"] = df["macd_hist"].diff(3)
    df["macd_cross"] = np.sign(df["macd"] - df["macd_signal"])
    # ADX
    tr = pd.concat([
        abs(df["high"] - df["low"]),
        abs(df["high"] - df["close"].shift(1)),
        abs(df["low"] - df["close"].shift(1))
    ], axis=1).max(axis=1)
    atr_14 = tr.rolling(14).mean().replace(0, np.nan)
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_di = 100 * (plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0).rolling(14).mean() / atr_14)
    minus_di = 100 * (minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0).rolling(14).mean() / atr_14)
    df["adx"] = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)).rolling(14).mean()
    df["adx_direction"] = np.where(plus_di > minus_di, 1, -1)
    df["trend_strength"] = np.where(df["adx"] >= 25, "trend", "ranging")
    
    # ── 7. SUPPORT / RÉSISTANCE (16 features) ──
    for p in [10, 20, 50, 100, 200]:
        df[f"resistance_{p}"] = df["high"].rolling(p).max()
        df[f"support_{p}"] = df["low"].rolling(p).min()
        df[f"dist_res_{p}_pct"] = (df[f"resistance_{p}"] - df["close"]) / df["close"] * 100
        df[f"dist_sup_{p}_pct"] = (df["close"] - df[f"support_{p}"]) / df["close"] * 100
    # Zone de congestion
    df["congestion"] = ((df["resistance_50"] - df["support_50"]) / df["close"] * 100 < 3).astype(int)
    # Fractal levels
    df["fractal_high"] = ((df["high"] > df["high"].shift(2)) &
                          (df["high"] > df["high"].shift(1)) &
                          (df["high"] > df["high"].shift(-1)) &
                          (df["high"] > df["high"].shift(-2))).astype(int)
    df["fractal_low"] = ((df["low"] < df["low"].shift(2)) &
                         (df["low"] < df["low"].shift(1)) &
                         (df["low"] < df["low"].shift(-1)) &
                         (df["low"] < df["low"].shift(-2))).astype(int)
    
    # ── 8. VOLUME & ORDER FLOW (12 features) ──
    df["volume_ma_10"] = df["volume"].rolling(10).mean()
    df["volume_ma_20"] = df["volume"].rolling(20).mean()
    df["volume_ma_50"] = df["volume"].rolling(50).mean()
    df["volume_ratio_10"] = df["volume"] / df["volume_ma_10"].replace(0, np.nan)
    df["volume_ratio_20"] = df["volume"] / df["volume_ma_20"].replace(0, np.nan)
    df["volume_ratio_50"] = df["volume"] / df["volume_ma_50"].replace(0, np.nan)
    df["volume_trend"] = df["volume_ma_10"] / df["volume_ma_50"].replace(0, np.nan)
    # Volume-weighted price
    df["vwap"] = (df["close"] * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum().replace(0, np.nan)
    df["dist_vwap_pct"] = (df["close"] - df["vwap"]) / df["vwap"] * 100
    # Pressure
    df["bull_volume"] = df["volume"] * np.where(df["close"] >= df["open"], 1, 0)
    df["bear_volume"] = df["volume"] * np.where(df["close"] < df["open"], 1, 0)
    df["volume_pressure"] = (df["bull_volume"].rolling(10).sum() - df["bear_volume"].rolling(10).sum()) / df["volume"].rolling(10).sum().replace(0, np.nan)
    
    # ── 9. MARKET MICROSTRUCTURE (8 features) ──
    # Spread analysis
    df["spread_ma_20"] = df["spread"].rolling(20).mean()
    df["spread_ratio"] = df["spread"] / df["spread_ma_20"].replace(0, np.nan)
    df["spread_zscore"] = (df["spread"] - df["spread"].rolling(50).mean()) / df["spread"].rolling(50).std().replace(0, np.nan)
    # Tick intensity
    df["tick_intensity"] = df["volume"] / df["range"].replace(0, np.nan)
    df["tick_ma_20"] = df["tick_intensity"].rolling(20).mean()
    df["tick_ratio"] = df["tick_intensity"] / df["tick_ma_20"].replace(0, np.nan)
    # Micro trends
    df["micro_trend_3"] = np.sign(df["close"] - df["close"].shift(3))
    df["micro_trend_5"] = np.sign(df["close"] - df["close"].shift(5))
    # Rolling efficiency ratio
    move_sum = df["close"].diff().abs().rolling(20).sum()
    net_move = (df["close"] - df["close"].shift(20)).abs()
    df["efficiency_ratio"] = net_move / move_sum.replace(0, np.nan)
    
    # ── 10. CYCLES & SESSIONS (10 features) ──
    hour = df["timestamp"].dt.hour
    minute = df["timestamp"].dt.minute
    weekday = df["timestamp"].dt.weekday
    month = df["timestamp"].dt.month
    
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
    df["weekday_cos"] = np.cos(2 * np.pi * weekday / 7)
    df["month_sin"] = np.sin(2 * np.pi * month / 12)
    df["month_cos"] = np.cos(2 * np.pi * month / 12)
    
    df["session_asia"] = ((hour >= 0) & (hour < 8)).astype(int)
    df["session_london"] = ((hour >= 8) & (hour < 16)).astype(int)
    df["session_ny"] = ((hour >= 13) & (hour < 22)).astype(int)
    df["session_overlap_london_ny"] = ((hour >= 13) & (hour < 16)).astype(int)
    df["is_weekend"] = (weekday >= 5).astype(int)
    df["is_month_start"] = (df["timestamp"].dt.day <= 2).astype(int)
    df["is_month_end"] = (df["timestamp"].dt.day >= 28).astype(int)
    df["is_friday"] = (weekday == 4).astype(int)
    df["is_monday"] = (weekday == 0).astype(int)
    
    # ── 11. LAGGED & CROSS-SYMBOL (places vides pour future) ──
    # Les colonnes seront ajoutées par le module anticipation (features croisées entre symboles)
    
    return df


def save_dataset_multi(df: pd.DataFrame, symbol: str, tf_name: str):
    """Sauvegarde raw + features en parquet."""
    raw_path = RAW_DIR / f"{symbol}_{tf_name}_raw.parquet"
    features_path = FEATURES_DIR / f"{symbol}_{tf_name}_features.parquet"
    
    raw_cols = ["timestamp", "open", "high", "low", "close", "volume", "spread", "symbol"]
    df[raw_cols].to_parquet(raw_path, index=False)
    logger.info(f"  Raw: {raw_path} ({len(df)} lignes)")
    
    df_feat = add_features_100(df)
    df_feat.to_parquet(features_path, index=False)
    logger.info(f"  Features: {features_path} ({len(df_feat.columns)} colonnes)")
    
    # Metadata des colonnes pour l'anticipation
    feat_cols = [c for c in df_feat.columns if c not in raw_cols]
    meta_path = FEATURES_DIR / f"{symbol}_{tf_name}_columns.json"
    json.dump({"feature_count": len(feat_cols), "feature_names": feat_cols}, open(meta_path, "w"))
    logger.info(f"  {len(feat_cols)} features techniques pour {symbol} {tf_name}")


def update_metadata(summary: dict):
    if METADATA_FILE.exists():
        existing = json.loads(METADATA_FILE.read_text())
    else:
        existing = {"downloads": [], "last_update": None}
    existing["downloads"].append(summary)
    existing["last_update"] = dt.now().isoformat()
    METADATA_FILE.write_text(json.dumps(existing, default=str, indent=2))


def main():
    ensure_dirs()
    if not init_mt5():
        return
    
    try:
        for symbol in SYMBOLS:
            for tf_name in TIMEFRAMES:
                max_candles = MAX_CANDLES.get(tf_name, 100000)
                df = download_symbol(symbol, tf_name, max_candles=max_candles)
                if df is not None and len(df) > 100:
                    save_dataset_multi(df, symbol, tf_name)
                    update_metadata({
                        "symbol": symbol,
                        "timeframe": tf_name,
                        "candles": len(df),
                        "from": str(df["timestamp"].min()),
                        "to": str(df["timestamp"].max()),
                        "days": (df["timestamp"].max() - df["timestamp"].min()).days,
                    })
    finally:
        mt5.shutdown()
    
    logger.info("\n=== Téléchargement multi-timeframes terminé ===")


if __name__ == "__main__":
    main()
