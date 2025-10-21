from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

import numpy as np
import pandas as pd

try:
    import ta  # type: ignore
    TA_OK = True
except Exception:
    TA_OK = False


SUPPORTED_TFS = {
    "1D": "1D",
    "4H": "4h",
    "1H": "1h",
    "30MIN": "30min",
    "15 MIN": "15min",
    "15MIN": "15min",
    "5 MIN": "5min",
    "5MIN": "5min",
}


@dataclass
class MTFConfig:
    base_tf: str = "15min"  # 15 minutes
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
        try:
            rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        except Exception:
            rsi = _rsi_simple(close, 14)
        try:
            macd_obj = ta.trend.MACD(close)
            macd_line = macd_obj.macd()
            macd_signal = macd_obj.macd_signal()
            macd_hist = macd_obj.macd_diff()
        except Exception:
            macd_line, macd_signal, macd_hist = _macd_simple(close)
        try:
            ema = ta.trend.EMAIndicator(close, window=20).ema_indicator()
        except Exception:
            ema = close.ewm(span=20, adjust=False).mean()
        try:
            bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
            bb_high = bb.bollinger_hband()
            bb_low = bb.bollinger_lband()
        except Exception:
            std = close.rolling(20, min_periods=2).std()
            ma20 = close.rolling(20, min_periods=2).mean()
            bb_high = ma20 + 2 * std
            bb_low = ma20 - 2 * std
        try:
            atr = ta.volatility.AverageTrueRange(
                df["high"], df["low"], close, window=14
            ).average_true_range()
        except Exception:
            tr1 = df["high"] - df["low"]
            tr2 = (df["high"] - close.shift()).abs()
            tr3 = (df["low"] - close.shift()).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=2).mean()
    else:
        # Fallback simple si lib ta non dispo
        rsi = _rsi_simple(close, 14)
        macd_line, macd_signal, macd_hist = _macd_simple(close)
        ema = close.ewm(span=20, adjust=False).mean()
        std = close.rolling(20, min_periods=2).std()
        ma20 = close.rolling(20, min_periods=2).mean()
        bb_high = ma20 + 2 * std
        bb_low = ma20 - 2 * std
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - close.shift()).abs()
        tr3 = (df["low"] - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=2).mean()

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
    # Associer labels (utilisés dans les noms de colonnes) à des règles pandas sans alias dépréciés
    tf_rules = []
    labels = ["1D", "4H", "1H", "30T", "15T", "5T"]
    # Utiliser 'min' et 'h' pour éviter les FutureWarnings; '1D' reste inchangé
    rules = ["1D", "4h", "1h", "30min", "15min", "5min"]
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


def compute_mtf_convergence(
    tech_features_15m_aligned: pd.DataFrame,
    labels: Optional[List[str]] = None,
) -> Tuple[str, float, int]:
    """Calcule un signal de convergence multi-timeframe basé sur RSI/MACD.

    Retourne (action, confidence, agreement_count)
    - action: 'buy' | 'sell' | 'hold'
    - confidence: [0..1]
    - agreement_count: nombre de timeframes alignés non neutres
    """
    if tech_features_15m_aligned is None or len(tech_features_15m_aligned) == 0:
        return ("hold", 0.0, 0)

    labels = labels or ["1D", "4H", "1H", "30T", "15T", "5T"]
    last_idx = tech_features_15m_aligned.index[-1]

    score = 0
    considered = 0

    for lbl in labels:
        try:
            rsi = tech_features_15m_aligned.loc[last_idx, f"tech_{lbl}_rsi14"]
            macd = tech_features_15m_aligned.loc[last_idx, f"tech_{lbl}_macd"]
            macd_sig = tech_features_15m_aligned.loc[last_idx, f"tech_{lbl}_macd_signal"]
        except Exception:
            continue

        if pd.isna(rsi) or pd.isna(macd) or pd.isna(macd_sig):
            continue

        considered += 1
        if rsi < 30 and macd > macd_sig:
            score += 1
        elif rsi > 70 and macd < macd_sig:
            score -= 1
        else:
            # neutre
            pass

    if considered == 0:
        return ("hold", 0.0, 0)

    # Décision
    if score >= 2:
        action = "buy"
    elif score <= -2:
        action = "sell"
    else:
        action = "hold"

    # Confiance proportionnelle à l'accord
    agreement = abs(score)
    confidence = min(1.0, agreement / max(3, considered) * 1.5)
    return (action, float(confidence), agreement)


def build_live_mtf_from_m1(
    df_m1: pd.DataFrame,
    fundamentals: Optional[Dict[str, pd.Series]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Construit les features MTF 15m à partir d'un DF M1 OHLCV.

    Retourne (ohlcv_15m, tech_features_aligned_15m, funda_aligned_15m)
    """
    if df_m1 is None or len(df_m1) == 0:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # S'assurer des colonnes OHLCV
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(set(df_m1.columns)):
        raise ValueError("df_m1 doit contenir open/high/low/close")

    df_m1u = _ensure_dt_index(df_m1)
    if "volume" not in df_m1u.columns:
        # volume optionnel: construire une série nulle si absente
        df_m1u["volume"] = 0.0

    ohlcv_15m = resample_ohlcv(df_m1u, "15min")

    tech = build_mtf_technical(ohlcv_15m)

    if fundamentals is not None and len(fundamentals) > 0:
        from .fundamentals import build_7_fundamentals  # import local pour éviter cycles
        funda = build_7_fundamentals(tech.index, fundamentals)
    else:
        funda = pd.DataFrame(index=tech.index)

    return ohlcv_15m, tech, funda
