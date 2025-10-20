from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

try:
    import ta  # type: ignore
    TA_OK = True
except Exception:
    TA_OK = False


SUPPORTED_TFS = {
    "1D": "1D",
    "4H": "4H",
    "1H": "1H",
    "30MIN": "30T",
    "15 MIN": "15T",
    "15MIN": "15T",
    "5 MIN": "5T",
    "5MIN": "5T",
}


@dataclass
class MTFConfig:
    base_tf: str = "15T"  # 15 minutes
    tfs: List[str] = ("1D", "4H", "1H", "30T", "15T", "5T")
    min_history_days: int = 365 * 7


def _ensure_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        if "time" in df.columns:
            df = df.set_index(pd.to_datetime(df["time"]))
        else:
            raise ValueError("DataFrame must have DatetimeIndex or 'time' column")
    return df.sort_index()


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    df = _ensure_dt_index(df)
    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    low_res = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    v = df.get("volume", pd.Series(index=c.index, dtype=float)).resample(rule).sum()
    return pd.DataFrame({"open": o, "high": h, "low": low_res, "close": c, "volume": v}).dropna()


def compute_7_tech_indicators(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    df = _ensure_dt_index(df).copy()
    close = df["close"]

    if TA_OK:
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd = ta.trend.MACD(close)
        macd_line = macd.macd()
        macd_signal = macd.macd_signal()
        macd_hist = macd.macd_diff()
        ema = ta.trend.EMAIndicator(close, window=20).ema_indicator()
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_high = bb.bollinger_hband()
        bb_low = bb.bollinger_lband()
        atr = ta.volatility.AverageTrueRange(
            df["high"], df["low"], close, window=14
        ).average_true_range()
    else:
        # Fallback simple si lib ta non dispo
        rsi = _rsi_simple(close, 14)
        macd_line, macd_signal, macd_hist = _macd_simple(close)
        ema = close.ewm(span=20, adjust=False).mean()
        std = close.rolling(20).std()
        ma20 = close.rolling(20).mean()
        bb_high = ma20 + 2 * std
        bb_low = ma20 - 2 * std
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - close.shift()).abs()
        tr3 = (df["low"] - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

    out = pd.DataFrame(index=df.index)
    out[f"{prefix}_rsi14"] = rsi
    out[f"{prefix}_macd"] = macd_line
    out[f"{prefix}_macd_signal"] = macd_signal
    out[f"{prefix}_macd_hist"] = macd_hist
    out[f"{prefix}_ema20"] = ema
    out[f"{prefix}_bb_high"] = bb_high
    out[f"{prefix}_bb_low"] = bb_low
    out[f"{prefix}_atr14"] = atr
    return out


def _rsi_simple(series: pd.Series, window: int) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).rolling(window).mean()
    down = (-delta.clip(upper=0)).rolling(window).mean()
    rs = up / (down.replace(0, np.nan))
    return 100 - (100 / (1 + rs))


def _macd_simple(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    return macd, macd_signal, macd_hist


def build_mtf_technical(df_15m: pd.DataFrame, cfg: MTFConfig | None = None) -> pd.DataFrame:
    cfg = cfg or MTFConfig()
    base = _ensure_dt_index(df_15m).copy()

    # Map TFs vers règles pandas
    tf_rules = [SUPPORTED_TFS.get(tf, tf) for tf in ("1D", "4H", "1H", "30MIN", "15MIN", "5MIN")]
    tf_rules = []
    labels = ["1D", "4H", "1H", "30T", "15T", "5T"]
    rules = ["1D", "4H", "1H", "30T", "15T", "5T"]
    for lbl, rule in zip(labels, rules):
        tf_rules.append({"label": lbl, "rule": rule})

    features = []
    for tf in tf_rules:
        res = resample_ohlcv(base, tf["rule"]) if isinstance(tf, dict) else resample_ohlcv(base, tf)
        label = tf["label"] if isinstance(tf, dict) else tf
        tech = compute_7_tech_indicators(res, prefix=f"tech_{label}")
        # Re-échantillonner sur 15T (base) par asof join
        tech_15 = tech.reindex(base.index, method="pad")
        features.append(tech_15)

    merged = pd.concat(features, axis=1)
    return merged
