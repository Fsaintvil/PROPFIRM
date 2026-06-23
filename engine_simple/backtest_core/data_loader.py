"""
DataLoader — Chargement, nettoyage et préparation des données pour le backtest.

Supporte :
  - Parquet H1/H4/D1 (format {symbol}_{TF}.parquet)
  - Parquet M1 (format {symbol}_M1.parquet)
  - Tick data (format {symbol}_tick.parquet)
  - Détection des sessions (Asia/London/NY)
  - Calcul d'indicateurs (ATR, ADX, RSI, EMA, etc.)
  - Nettoyage : outliers, spread=0, trous, barres corrompues

Usage :
    dl = DataLoader()
    df = dl.load("EURUSD", "H1", start="2012-01-01", end="2026-01-01")
    df = dl.add_indicators(df)
    df = dl.detect_sessions(df)
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("backtest_core.data_loader")

# ─── Chemins des données ──────────────────────────────────────────────────

DATA_PATHS = {
    "historical": Path("data/historical"),
    "raw": Path("data/raw"),
    "tick": Path("data/tick"),
    "m1": Path("data/m1"),
    "m5": Path("data/m5"),
    "m15": Path("data/m15"),
}

# ─── Sessions de trading (UTC) ────────────────────────────────────────────

SESSION_HOURS = {
    "asia": (0, 8),
    "london": (7, 16),
    "ny": (13, 21),
}


# ═══════════════════════════════════════════════════════════════════════════
# DataLoader
# ═══════════════════════════════════════════════════════════════════════════


class DataLoader:
    """
    Charge et prépare les données historiques pour le backtest.
    """

    def __init__(self, data_root: Optional[str | Path] = None):
        """
        Args:
            data_root: Racine des données. Si None, utilise le dossier par défaut.
        """
        self.data_root = Path(data_root) if data_root else Path(".")

    # ─── Chargement ──────────────────────────────────────────────────────

    def load(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        source: str = "historical",
    ) -> pd.DataFrame:
        """
        Charge les données pour un symbole et timeframe.

        Args:
            symbol: Symbole (ex: "EURUSD", "XAUUSD")
            timeframe: Timeframe ("H1", "H4", "D1", "M15", "M5", "M1")
            start: Date de début (format "YYYY-MM-DD", None = tout)
            end: Date de fin (format "YYYY-MM-DD", None = tout)
            source: "historical" | "raw" | "tick" | "m1" | "m5" | "m15"

        Returns:
            DataFrame avec colonnes : timestamp, open, high, low, close, volume[, spread]
        """
        # Déterminer le chemin du fichier
        if source == "tick":
            filepath = DATA_PATHS.get("tick", self.data_root / "data/tick") / f"{symbol}_tick.parquet"
        elif source in ("m1", "m5", "m15"):
            filepath = DATA_PATHS.get(source, self.data_root / f"data/{source}") / f"{symbol}_{source.upper()}.parquet"
        elif source == "raw":
            filepath = DATA_PATHS.get("raw", self.data_root / "data/raw") / f"{symbol}_{timeframe}_raw.parquet"
        else:
            filepath = (
                DATA_PATHS.get("historical", self.data_root / "data/historical") / f"{symbol}_{timeframe}.parquet"
            )

        if not filepath.exists():
            logger.warning(f"Fichier introuvable : {filepath}")
            # Fallback : chercher dans retired/data/
            retired_path = Path("retired/data") / f"{symbol}_{timeframe}.parquet"
            if retired_path.exists():
                logger.info(f"Fallback vers {retired_path}")
                filepath = retired_path
            else:
                raise FileNotFoundError(f"Données non trouvées pour {symbol} {timeframe}")

        # Charger le parquet
        logger.debug(f"Chargement {filepath}")
        df = pd.read_parquet(filepath)

        # Normaliser les colonnes
        df = self._normalize_columns(df)

        # Filtrer par période
        if start:
            start_dt = pd.Timestamp(start)
            df = df[df["timestamp"] >= start_dt]
        if end:
            end_dt = pd.Timestamp(end)
            df = df[df["timestamp"] < end_dt]

        if df.empty:
            logger.warning(f"DataFrame vide pour {symbol} {timeframe} après filtre dates")
            return df

        # Trier par timestamp
        df = df.sort_values("timestamp").reset_index(drop=True)

        logger.info(f"Chargé {symbol} {timeframe}: {len(df)} barres, {df['timestamp'].min()} → {df['timestamp'].max()}")
        return df

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalise les noms de colonnes."""
        rename = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if col_lower in ("time", "date", "datetime", "timestamp", "time"):
                rename[col] = "timestamp"
            elif col_lower in ("open", "o"):
                rename[col] = "open"
            elif col_lower in ("high", "h"):
                rename[col] = "high"
            elif col_lower in ("low", "l"):
                rename[col] = "low"
            elif col_lower in ("close", "c"):
                rename[col] = "close"
            elif col_lower in ("volume", "tick_volume", "vol", "tv"):
                rename[col] = "volume"
            elif col_lower in ("real_volume", "rv"):
                rename[col] = "real_volume"
            elif col_lower in ("spread", "sp"):
                rename[col] = "spread"

        df = df.rename(columns=rename)

        # S'assurer que les colonnes obligatoires existent
        required = ["timestamp", "open", "high", "low", "close"]
        for col in required:
            if col not in df.columns:
                raise ValueError(f"Colonne obligatoire manquante : {col}")

        # S'assurer que timestamp est datetime
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Volume par défaut si absent
        if "volume" not in df.columns:
            df["volume"] = 0

        # Spread par défaut si absent
        if "spread" not in df.columns:
            df["spread"] = 0

        return df

    # ─── Nettoyage ──────────────────────────────────────────────────────

    def clean(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        remove_outliers: bool = True,
        fill_spread: bool = True,
        remove_corrupt: bool = True,
    ) -> pd.DataFrame:
        """
        Nettoie un DataFrame de données OHLCV.

        1. Supprime les barres avec open/high/low/close <= 0
        2. Supprime les barres où high < low ou open/close hors range
        3. Interpole les spread = 0 (si fill_spread=True)
        4. Supprime les outliers (z-score > 5 sur les rendements)
        5. Supprime les doublons de timestamp
        """
        if df.empty:
            return df

        n_before = len(df)
        df = df.copy()

        # 1. Prix invalides
        mask_valid = (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)
        df = df[mask_valid]

        # 2. Barres corrompues
        if remove_corrupt:
            mask_corrupt = (
                (df["high"] >= df["low"])
                & (df["high"] >= df["open"] * 0.9)
                & (df["high"] <= df["open"] * 1.1)
                & (df["low"] >= df["open"] * 0.9)
                & (df["low"] <= df["open"] * 1.1)
            )
            # Garder les barres où high/low sont dans 10% de open (filtre large)
            df = df[mask_corrupt]

        # 3. Spread = 0 → interpolation
        if fill_spread and "spread" in df.columns:
            zero_spread = df["spread"] == 0
            if zero_spread.any():
                n_zero = zero_spread.sum()
                df.loc[zero_spread, "spread"] = np.nan
                df["spread"] = df["spread"].ffill().fillna(df["spread"].median() if df["spread"].median() > 0 else 10)
                logger.debug(f"  [CLEAN] {symbol}: {n_zero} spread=0 interpolés")

        # 4. Outliers (rendements extrêmes)
        if remove_outliers and len(df) > 20:
            returns = df["close"].pct_change().dropna()
            z_scores = np.abs((returns - returns.mean()) / returns.std())
            outlier_indices = returns[z_scores > 5].index
            if len(outlier_indices) > 0:
                n_outliers = len(outlier_indices)
                df = df.drop(outlier_indices, errors="ignore")
                logger.debug(f"  [CLEAN] {symbol}: {n_outliers} outliers supprimés")

        # 5. Doublons de timestamp
        n_before_dedup = len(df)
        df = df.drop_duplicates(subset=["timestamp"])
        n_dedup = n_before_dedup - len(df)
        if n_dedup > 0:
            logger.debug(f"  [CLEAN] {symbol}: {n_dedup} doublons supprimés")

        # Trier
        df = df.sort_values("timestamp").reset_index(drop=True)

        n_after = len(df)
        n_removed = n_before - n_after
        if n_removed > 0:
            logger.info(f"  [CLEAN] {symbol}: {n_removed} barres supprimées ({n_before} → {n_after})")

        return df

    # ─── Sessions ────────────────────────────────────────────────────────

    def detect_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ajoute les colonnes de session de trading.

        Colonnes ajoutées :
          - session: "asia" | "london" | "ny" | "overlap" | "closed"
          - is_asia, is_london, is_ny: bool
          - hour_utc: int
        """
        df = df.copy()
        df["hour_utc"] = df["timestamp"].dt.hour

        sessions = []
        for hour in df["hour_utc"]:
            if (
                SESSION_HOURS["asia"][0] <= hour < SESSION_HOURS["asia"][1]
                and SESSION_HOURS["london"][0] <= hour < SESSION_HOURS["london"][1]
            ):
                sessions.append("overlap_asia_london")
            elif (
                SESSION_HOURS["london"][0] <= hour < SESSION_HOURS["london"][1]
                and SESSION_HOURS["ny"][0] <= hour < SESSION_HOURS["ny"][1]
            ):
                sessions.append("overlap_london_ny")
            elif SESSION_HOURS["asia"][0] <= hour < SESSION_HOURS["asia"][1]:
                sessions.append("asia")
            elif SESSION_HOURS["london"][0] <= hour < SESSION_HOURS["london"][1]:
                sessions.append("london")
            elif SESSION_HOURS["ny"][0] <= hour < SESSION_HOURS["ny"][1]:
                sessions.append("ny")
            else:
                sessions.append("closed")
        df["session"] = sessions

        df["is_asia"] = df["hour_utc"].between(SESSION_HOURS["asia"][0], SESSION_HOURS["asia"][1] - 1)
        df["is_london"] = df["hour_utc"].between(SESSION_HOURS["london"][0], SESSION_HOURS["london"][1] - 1)
        df["is_ny"] = df["hour_utc"].between(SESSION_HOURS["ny"][0], SESSION_HOURS["ny"][1] - 1)

        return df

    # ─── Indicateurs ─────────────────────────────────────────────────────

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ajoute les indicateurs techniques au DataFrame.

        Indicateurs :
          - atr_14 : Average True Range (14 périodes)
          - adx_14 : ADX (14)
          - rsi_14 : RSI (14)
          - ema_12, ema_26 : Exponential Moving Averages
          - sma_20, sma_50, sma_200 : Simple Moving Averages
          - bb_upper, bb_lower : Bollinger Bands (20, 2)
          - macd, macd_signal : MACD (12, 26, 9)
          - volume_sma_20 : Volume moyen 20 périodes
        """
        df = df.copy()

        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float) if "volume" in df.columns else None

        n = len(close)

        # ATR 14
        df["atr_14"] = self._compute_atr(high, low, close, 14)

        # ADX 14
        df["adx_14"] = self._compute_adx(high, low, close, 14)

        # RSI 14
        df["rsi_14"] = self._compute_rsi(close, 14)

        # EMA
        df["ema_12"] = self._compute_ema(close, 12)
        df["ema_26"] = self._compute_ema(close, 26)

        # SMA
        df["sma_20"] = self._compute_sma(close, 20)
        df["sma_50"] = self._compute_sma(close, 50)
        df["sma_200"] = self._compute_sma(close, 200)

        # Bollinger Bands
        bb_upper, bb_lower = self._compute_bollinger(close, 20, 2.0)
        df["bb_upper"] = bb_upper
        df["bb_lower"] = bb_lower

        # MACD
        macd_line, macd_signal = self._compute_macd(close, 12, 26, 9)
        df["macd"] = macd_line
        df["macd_signal"] = macd_signal

        # Volume SMA
        if volume is not None:
            df["volume_sma_20"] = self._compute_sma(volume, 20)

        return df

    # ─── Indicateurs statiques ────────────────────────────────────────────

    @staticmethod
    def _compute_atr(high, low, close, period=14):
        n = len(close)
        atr = np.full(n, np.nan)
        if n < period + 1:
            return atr
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        atr[period] = np.mean(tr[:period])
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i - 1]) / period
        return atr

    @staticmethod
    def _compute_adx(high, low, close, period=14):
        n = len(close)
        adx = np.full(n, np.nan)
        if n < period * 2:
            return adx

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        up = high[1:] - high[:-1]
        down = low[:-1] - low[1:]
        pos_dm = np.where((up > down) & (up > 0), up, 0)
        neg_dm = np.where((down > up) & (down > 0), down, 0)

        tr_sm = np.full(n, np.nan)
        pos_sm = np.full(n, np.nan)
        neg_sm = np.full(n, np.nan)

        for i in range(period, n):
            tr_sm[i] = np.mean(tr[i - period : i])
            pos_sm[i] = np.mean(pos_dm[i - period : i])
            neg_sm[i] = np.mean(neg_dm[i - period : i])

        pos_di = 100 * pos_sm / tr_sm
        neg_di = 100 * neg_sm / tr_sm
        dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di)

        for i in range(period * 2, n):
            adx[i] = np.mean(dx[i - period : i])

        return adx

    @staticmethod
    def _compute_rsi(close, period=14):
        n = len(close)
        rsi = np.full(n, np.nan)
        if n < period + 1:
            return rsi
        diff = np.diff(close)
        gains = np.where(diff > 0, diff, 0)
        losses = np.where(diff < 0, -diff, 0)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        if avg_loss == 0:
            rsi[period] = 100
        else:
            rsi[period] = 100 - (100 / (1 + avg_gain / avg_loss))
        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            if avg_loss == 0:
                rsi[i] = 100
            else:
                rsi[i] = 100 - (100 / (1 + avg_gain / avg_loss))
        return rsi

    @staticmethod
    def _compute_ema(data, period):
        d = np.asarray(data, dtype=float)
        result = np.full_like(d, np.nan)
        if len(d) < period * 2:
            return result
        alpha = 2.0 / (period + 1)
        result[period - 1] = np.mean(d[:period])
        for i in range(period, len(d)):
            result[i] = alpha * d[i] + (1 - alpha) * result[i - 1]
        return result

    @staticmethod
    def _compute_sma(data, period):
        d = np.asarray(data, dtype=float)
        result = np.full_like(d, np.nan)
        if len(d) < period:
            return result
        cum = np.cumsum(d)
        cum[period:] = cum[period:] - cum[:-period]
        result[period - 1 :] = cum[period - 1 :] / period
        return result

    @staticmethod
    def _compute_bollinger(data, period=20, std_dev=2.0):
        d = np.asarray(data, dtype=float)
        middle = DataLoader._compute_sma(d, period)
        std = np.full_like(d, np.nan)
        upper = np.full_like(d, np.nan)
        lower = np.full_like(d, np.nan)
        for i in range(period - 1, len(d)):
            std[i] = np.std(d[i - period + 1 : i + 1])
            upper[i] = middle[i] + std_dev * std[i]
            lower[i] = middle[i] - std_dev * std[i]
        return upper, lower

    @staticmethod
    def _compute_macd(data, fast=12, slow=26, signal=9):
        d = np.asarray(data, dtype=float)
        ema_fast = DataLoader._compute_ema(d, fast)
        ema_slow = DataLoader._compute_ema(d, slow)
        macd_line = ema_fast - ema_slow
        valid = ~np.isnan(macd_line)
        sig_line = np.full_like(macd_line, np.nan)
        if np.any(valid):
            first_valid = np.argmax(valid)
            sig_segment = DataLoader._compute_ema(macd_line[first_valid:], signal)
            sig_line[first_valid:] = sig_segment
        return macd_line, sig_line

    # ─── Agrégation / Resampling ──────────────────────────────────────────

    def resample_to_tf(self, df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
        """
        Agrège des données vers un timeframe supérieur.

        Args:
            df: DataFrame avec timestamp indexé
            target_tf: "H1", "H4", "D1", "M15", "M5"

        Returns:
            DataFrame OHLCV agrégé
        """
        # Mapper les timeframes aux fréquences pandas
        tf_map = {
            "M1": "1min",
            "M5": "5min",
            "M15": "15min",
            "M30": "30min",
            "H1": "1h",
            "H4": "4h",
            "D1": "1D",
            "W1": "1W",
        }
        freq = tf_map.get(target_tf, "1h")

        df = df.set_index("timestamp")
        ohlc = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
        resampled = df.resample(freq).agg(ohlc).dropna()
        resampled = resampled.reset_index()
        if "spread" in df.columns:
            resampled["spread"] = df["spread"].resample(freq).mean().values

        return resampled

    # ─── Liste des symboles disponibles ──────────────────────────────────

    @staticmethod
    def list_available_symbols(timeframe: str = "H1", source: str = "historical") -> list[str]:
        """Liste les symboles disponibles pour un timeframe donné."""
        if source == "historical":
            data_dir = DATA_PATHS.get("historical", Path("data/historical"))
        elif source == "raw":
            data_dir = DATA_PATHS.get("raw", Path("data/raw"))
        else:
            data_dir = DATA_PATHS.get(source, Path(f"data/{source}"))

        if not data_dir.exists():
            return []

        pattern = f"*_{timeframe}.parquet"
        files = sorted(data_dir.glob(pattern))

        symbols = []
        for f in files:
            parts = f.stem.split("_")
            # Gérer les noms avec points (ex: US500.cash_H1)
            if len(parts) > 2 and parts[-1] == timeframe:
                sym = "_".join(parts[:-1])
            elif len(parts) == 2:
                sym = parts[0]
            else:
                continue
            symbols.append(sym)

        return sorted(symbols)
