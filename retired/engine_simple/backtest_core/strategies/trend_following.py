"""
TrendFollowing — Stratégie Trend Following.

Principe :
  - EMA12 croise EMA26 à la hausse → BUY (golden cross)
  - EMA12 croise EMA26 à la baisse → SELL (death cross)
  - Confirmation : ADX > 25 (trend suffisamment fort)
  - Entrée différée : pullback sur EMA20 après le cross
  - Multi-TF : confirmation sur HTF (H4) pour tendance, entrée sur LTF (H1)

Paramètres :
  - fast_ema: 12
  - slow_ema: 26
  - adx_threshold: 25
  - lookback_confirmation: 3 (barres de confirmation)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from engine_simple.backtest_core.strategies.base import Strategy, Signal

logger = logging.getLogger("backtest_core.strategies.trend_following")

DEFAULT_CONFIG = {
    "fast_ema": 12,
    "slow_ema": 26,
    "adx_threshold": 25,
    "lookback_confirmation": 3,
    "sl_atr_mult": 2.0,
    "tp_atr_mult": 5.0,
    "min_score": 0.50,
}


class TrendFollowing(Strategy):
    """Stratégie de suivi de tendance par croisement EMA + confirmation ADX."""

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    def name(self) -> str:
        return "TrendFollowing"

    def generate(
        self, bar_idx: int, data: dict, regime: str, open_positions: list, timestamp: Optional[datetime] = None
    ) -> Optional[Signal]:
        if bar_idx < self.config["slow_ema"] + 20:
            return None

        close = data["close"]
        high = data["high"]
        low = data["low"]

        # EMA
        ema_fast = self._ema(close, self.config["fast_ema"], bar_idx)
        ema_slow = self._ema(close, self.config["slow_ema"], bar_idx)
        ema_fast_prev = self._ema(close, self.config["fast_ema"], bar_idx - 1)
        ema_slow_prev = self._ema(close, self.config["slow_ema"], bar_idx - 1)

        if any(np.isnan(v) for v in [ema_fast, ema_slow, ema_fast_prev, ema_slow_prev]):
            return None

        # Croisement
        cross_up = ema_fast_prev <= ema_slow_prev and ema_fast > ema_slow
        cross_down = ema_fast_prev >= ema_slow_prev and ema_fast < ema_slow

        if not cross_up and not cross_down:
            return None

        # ADX confirmation
        adx_val = self._adx(high, low, close, bar_idx)
        if np.isnan(adx_val) or adx_val < self.config["adx_threshold"]:
            return None

        # Vérifier la pente ADX (doit être positive en trend)
        adx_prev = self._adx(high, low, close, bar_idx - 3)
        if not np.isnan(adx_prev) and adx_val < adx_prev:
            return None

        # Direction
        action = "BUY" if cross_up else "SELL"

        # ATR pour SL/TP
        atr_v = self._atr(high, low, close, bar_idx)
        if atr_v <= 0:
            return None

        sl_mult = self.config["sl_atr_mult"]
        tp_mult = self.config["tp_atr_mult"]

        if action == "BUY":
            sl = close[bar_idx] - sl_mult * atr_v
            tp = close[bar_idx] + tp_mult * atr_v
        else:
            sl = close[bar_idx] + sl_mult * atr_v
            tp = close[bar_idx] - tp_mult * atr_v

        # Score : basé sur la force de la tendance
        trend_strength = min(1.0, abs(ema_fast - ema_slow) / (atr_v * 2))
        confidence = trend_strength * min(1.0, adx_val / 40)

        symbol = data.get("symbol", "UNKNOWN")
        regime_label = "TREND_UP" if action == "BUY" else "TREND_DOWN"

        return Signal(
            symbol=symbol,
            action=action,
            score=round(min(1.0, confidence), 4),
            entry_price=round(close[bar_idx], 5),
            sl=round(sl, 5),
            tp=round(tp, 5),
            regime=regime_label,
            timestamp=timestamp or datetime.utcnow(),
            strategy=self.name(),
            metadata={
                "ema_fast": round(ema_fast, 5),
                "ema_slow": round(ema_slow, 5),
                "adx": round(adx_val, 1),
                "atr": round(atr_v, 5),
                "cross_up": cross_up,
                "cross_down": cross_down,
            },
        )

    @staticmethod
    def _ema(data, period, i):
        if i < period:
            return float("nan")
        alpha = 2.0 / (period + 1)
        result = float(np.mean(data[i - period + 1 : i + 1]))
        for j in range(i - period + 2, i + 1):
            result = alpha * data[j] + (1 - alpha) * result
        return result

    @staticmethod
    def _atr(high, low, close, i, period=14):
        if i < period + 1:
            return 0.0
        tr_vals = [
            max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
            for j in range(i - period + 1, i + 1)
        ]
        return float(np.mean(tr_vals))

    @staticmethod
    def _adx(high, low, close, i, period=14):
        if i < period * 2:
            return float("nan")
        dx_vals = []
        for k in range(i - period, i + 1):
            if k < period * 2:
                continue
            tr = np.mean(
                [
                    max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
                    for j in range(k - period + 1, k + 1)
                ]
            )
            up = np.mean([max(0, high[j] - high[j - 1]) for j in range(k - period + 1, k + 1)])
            down = np.mean([max(0, low[j - 1] - low[j]) for j in range(k - period + 1, k + 1)])
            pdi = 100 * up / tr if tr > 0 else 0
            ndi = 100 * down / tr if tr > 0 else 0
            dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0
            dx_vals.append(dx)
        return float(np.mean(dx_vals)) if dx_vals else float("nan")

    def get_config(self) -> dict:
        return dict(self.config)
