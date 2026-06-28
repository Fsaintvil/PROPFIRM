"""
MOM20x3 — Stratégie Momentum 20 périodes (port de engine_simple/strategy.py).

Principe :
  close[i] - close[i-20] > threshold × ATR  →  BUY
  close[i-20] - close[i] > threshold × ATR  →  SELL

Seuils : 2.5×ATR trending / 2.0×ATR ranging (plafond 2.5, plancher 1.5)
Filtres : ADX slope, +DI/-DI, pullback EMA20, DI Override, NaN guard

Config par symbole supportée (momentum_period, thresholds, pullback bands).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from engine_simple.backtest_core.strategies.base import Strategy, Signal

logger = logging.getLogger("backtest_core.strategies.mom20x3")

# ─── Default parameters ──────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "momentum_period": 20,
    "threshold_trending": 2.5,  # ADX >= 22
    "threshold_ranging": 2.0,  # ADX < 22
    "threshold_max": 2.5,
    "threshold_min": 1.5,
    "adx_period": 14,
    "adx_threshold_trend": 22,
    "adx_threshold_range": 18,
    "pullback_band_trending": 0.5,  # ×ATR
    "pullback_band_ranging": 0.3,  # ×ATR
    "adx_slope_threshold": -6.0,
    "adx_slope_threshold_strong": -10.0,
    "min_score": 0.55,
}


class MOM20x3(Strategy):
    """Momentum 20 périodes avec filtres avancés."""

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.momentum_period = self.config["momentum_period"]

    def name(self) -> str:
        return "MOM20x3"

    def generate(
        self, bar_idx: int, data: dict, regime: str, open_positions: list, timestamp: Optional[datetime] = None
    ) -> Optional[Signal]:
        if bar_idx < self.momentum_period + 20:
            return None

        close = data["close"]
        high = data["high"]
        low = data["low"]
        vol = data.get("volume")
        spread = data.get("spread")

        # NaN guard
        mom_val = float(close[bar_idx] - close[bar_idx - self.momentum_period])
        if np.isnan(mom_val) or np.isinf(mom_val):
            return None

        # ATR
        atr_v = self._atr(high, low, close, bar_idx)
        if atr_v <= 0:
            return None

        # ADX
        adx_val = self._adx(high, low, close, bar_idx)
        if np.isnan(adx_val):
            return None

        is_trending = adx_val >= self.config["adx_threshold_trend"]
        thresh = self.config["threshold_trending"] if is_trending else self.config["threshold_ranging"]
        thresh_val = max(self.config["threshold_min"], min(self.config["threshold_max"], thresh)) * atr_v

        # Seuil de momentum
        mom_abs = abs(mom_val)
        if mom_abs < thresh_val:
            return None

        # Direction
        action = "BUY" if mom_val > 0 else "SELL"

        # ADX slope filter
        slope = self._adx_slope(high, low, close, bar_idx)
        if slope is not None:
            score = min(1.0, mom_abs / (thresh_val * 1.5))
            slope_thresh = (
                self.config["adx_slope_threshold_strong"] if score > 0.70 else self.config["adx_slope_threshold"]
            )
            if slope < slope_thresh:
                return None

        # DI filter
        pos_di, neg_di = self._di(high, low, close, bar_idx)
        if not np.isnan(pos_di) and not np.isnan(neg_di):
            if action == "BUY" and pos_di < neg_di * 0.8:
                return None
            if action == "SELL" and neg_di < pos_di * 0.8:
                return None

        # Pullback filter
        ema20 = self._ema(close, 20, bar_idx)
        if not np.isnan(ema20) and ema20 > 0:
            pullback_band = (
                self.config["pullback_band_trending"] * atr_v
                if is_trending
                else self.config["pullback_band_ranging"] * atr_v
            )
            price_dist = abs(close[bar_idx] - ema20)
            if price_dist > pullback_band:
                return None

        # Calculer SL/TP
        if is_trending:
            sl_mult = 2.0
            tp_mult = 5.0
        else:
            sl_mult = 1.5
            tp_mult = 4.0

        if action == "BUY":
            sl = close[bar_idx] - sl_mult * atr_v
            tp = close[bar_idx] + tp_mult * atr_v
        else:
            sl = close[bar_idx] + sl_mult * atr_v
            tp = close[bar_idx] - tp_mult * atr_v

        # Score de confiance
        confidence = min(1.0, max(0.0, mom_abs / (thresh_val * 2.0)))

        symbol = data.get("symbol", "UNKNOWN")
        regime_label = (
            "TREND_UP"
            if (is_trending and action == "BUY")
            else "TREND_DOWN"
            if (is_trending and action == "SELL")
            else regime
        )

        return Signal(
            symbol=symbol,
            action=action,
            score=round(confidence, 4),
            entry_price=round(close[bar_idx], 5),
            sl=round(sl, 5),
            tp=round(tp, 5),
            regime=regime_label,
            timestamp=timestamp or datetime.utcnow(),
            strategy=self.name(),
            metadata={
                "momentum": round(mom_val, 5),
                "atr": round(atr_v, 5),
                "adx": round(adx_val, 1),
                "adx_slope": round(slope, 2) if slope else 0,
                "threshold": round(thresh_val, 5),
                "regime": regime_label,
            },
        )

    # ─── Indicateurs intégrés ───────────────────────────────────────────

    @staticmethod
    def _atr(high, low, close, i, period=14):
        if i < period + 1:
            return 0.0
        tr = np.maximum(
            high[i] - low[i],
            np.maximum(abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])),
        )
        tr_vals = [
            np.maximum(
                high[j] - low[j],
                np.maximum(abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1])),
            )
            for j in range(max(1, i - period + 1), i + 1)
        ]
        return float(np.mean(tr_vals))

    @staticmethod
    def _adx(high, low, close, i, period=14):
        if i < period * 2:
            return float("nan")
        tr = np.array(
            [
                max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
                for j in range(i - period + 1, i + 1)
            ]
        )
        up = np.array([max(0, high[j] - high[j - 1]) for j in range(i - period + 1, i + 1)])
        down = np.array([max(0, low[j - 1] - low[j]) for j in range(i - period + 1, i + 1)])
        pos_dm = np.where((up > down) & (up > 0), up, 0)
        neg_dm = np.where((down > up) & (down > 0), down, 0)
        tr_sm = np.mean(tr)
        pos_di = 100 * np.mean(pos_dm) / tr_sm if tr_sm > 0 else 0
        neg_di = 100 * np.mean(neg_dm) / tr_sm if tr_sm > 0 else 0
        dx = 100 * abs(pos_di - neg_di) / (pos_di + neg_di) if (pos_di + neg_di) > 0 else 0

        # Lisser ADX
        dx_vals = []
        for k in range(i - period, i + 1):
            if k < period * 2:
                continue
            tr_k = np.mean(
                [
                    max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
                    for j in range(k - period + 1, k + 1)
                ]
            )
            up_k = np.mean([max(0, high[j] - high[j - 1]) for j in range(k - period + 1, k + 1)])
            down_k = np.mean([max(0, low[j - 1] - low[j]) for j in range(k - period + 1, k + 1)])
            pdi = 100 * up_k / tr_k if tr_k > 0 else 0
            ndi = 100 * down_k / tr_k if tr_k > 0 else 0
            dx_k = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0
            dx_vals.append(dx_k)

        return float(np.mean(dx_vals)) if dx_vals else dx

    @staticmethod
    def _adx_slope(high, low, close, i, period=14):
        if i < period * 2 + 10:
            return None
        adx_vals = []
        for k in range(i - 10, i + 1):
            adx_k = MOM20x3._adx(high, low, close, k, period)
            if not np.isnan(adx_k):
                adx_vals.append(adx_k)
        if len(adx_vals) < 5:
            return None
        # Pente linéaire
        x = np.arange(len(adx_vals))
        if np.std(x) == 0:
            return 0.0
        slope = np.polyfit(x, adx_vals, 1)[0]
        return slope

    @staticmethod
    def _di(high, low, close, i, period=14):
        if i < period + 1:
            return float("nan"), float("nan")
        up = [max(0, high[j] - high[j - 1]) for j in range(i - period + 1, i + 1)]
        down = [max(0, low[j - 1] - low[j]) for j in range(i - period + 1, i + 1)]
        tr = [
            max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
            for j in range(i - period + 1, i + 1)
        ]
        tr_sm = np.mean(tr)
        pos_dm = np.sum([u if u > d and u > 0 else 0 for u, d in zip(up, down)])
        neg_dm = np.sum([d if d > u and d > 0 else 0 for u, d in zip(up, down)])
        return (100 * pos_dm / tr_sm if tr_sm > 0 else 0, 100 * neg_dm / tr_sm if tr_sm > 0 else 0)

    @staticmethod
    def _ema(data, period, i):
        if i < period:
            return float("nan")
        alpha = 2.0 / (period + 1)
        result = float(np.mean(data[i - period + 1 : i + 1]))
        for j in range(i - period + 2, i + 1):
            result = alpha * data[j] + (1 - alpha) * result
        return result

    def get_config(self) -> dict:
        return dict(self.config)
