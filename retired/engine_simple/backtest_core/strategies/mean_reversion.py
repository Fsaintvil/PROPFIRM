"""
MeanReversion — Stratégie de retour à la moyenne.

Principe :
  - RSI < 30 (surold) → BUY (le prix va remonter)
  - RSI > 70 (surachat) → SELL (le prix va baisser)
  - Confirmation : Bollinger Band touchée (prix en dehors des bandes)
  - Entrée : pullback après le touché de bande
  - Timeframe idéal : M15/M30 pour les extremums, H1 pour confirmation

Paramètres :
  - rsi_period: 14
  - rsi_oversold: 30
  - rsi_overbought: 70
  - bb_period: 20
  - bb_std: 2.0
  - confirmation_bars: 1 (barres après le touché de bande)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from engine_simple.backtest_core.strategies.base import Strategy, Signal

logger = logging.getLogger("backtest_core.strategies.mean_reversion")

DEFAULT_CONFIG = {
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "bb_period": 20,
    "bb_std": 2.0,
    "confirmation_bars": 1,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 3.0,
    "min_score": 0.50,
}


class MeanReversion(Strategy):
    """Stratégie de retour à la moyenne (RSI + Bollinger Bands)."""

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    def name(self) -> str:
        return "MeanReversion"

    def generate(
        self, bar_idx: int, data: dict, regime: str, open_positions: list, timestamp: Optional[datetime] = None
    ) -> Optional[Signal]:
        bb_period = self.config["bb_period"]
        if bar_idx < bb_period + 30:
            return None

        close = data["close"]
        high = data["high"]
        low = data["low"]

        # RSI
        rsi_val = self._rsi(close, self.config["rsi_period"], bar_idx)
        if np.isnan(rsi_val):
            return None

        # Bollinger Bands
        bb_upper, bb_mid, bb_lower = self._bollinger(close, bb_period, self.config["bb_std"], bar_idx)
        if any(np.isnan(v) for v in [bb_upper, bb_lower]):
            return None

        current_close = close[bar_idx]
        current_high = high[bar_idx]
        current_low = low[bar_idx]

        # Détection des conditions de retour à la moyenne
        oversold = rsi_val < self.config["rsi_oversold"] and current_low <= bb_lower
        overbought = rsi_val > self.config["rsi_overbought"] and current_high >= bb_upper

        if not oversold and not overbought:
            return None

        # Vérifier la confirmation (tendance baissière pour oversold = plus fiable)
        if regime in ("TREND_DOWN",) and oversold:
            return None  # Pas de BUY dans une tendance baissière forte
        if regime in ("TREND_UP",) and overbought:
            return None  # Pas de SELL dans une tendance haussière forte

        # Action
        action = "BUY" if oversold else "SELL"

        # ATR pour SL/TP
        atr_v = self._atr(high, low, close, bar_idx)
        if atr_v <= 0:
            return None

        # SL/TP
        if action == "BUY":
            # SL sous le plus bas récent
            recent_low = float(np.min(low[bar_idx - 10 : bar_idx + 1]))
            sl = min(recent_low - 0.5 * atr_v, current_close - self.config["sl_atr_mult"] * atr_v)
            tp = bb_mid + (bb_mid - current_close) * 1.5  # Retour vers la moyenne
        else:
            recent_high = float(np.max(high[bar_idx - 10 : bar_idx + 1]))
            sl = max(recent_high + 0.5 * atr_v, current_close + self.config["sl_atr_mult"] * atr_v)
            tp = bb_mid - (current_close - bb_mid) * 1.5

        # Score
        if oversold:
            reversion_strength = (bb_lower - current_close) / atr_v
            confidence = min(1.0, max(0.0, (self.config["rsi_oversold"] - rsi_val) / 30))
        else:
            reversion_strength = (current_close - bb_upper) / atr_v
            confidence = min(1.0, max(0.0, (rsi_val - self.config["rsi_overbought"]) / 30))

        if confidence < self.config["min_score"]:
            return None

        symbol = data.get("symbol", "UNKNOWN")

        return Signal(
            symbol=symbol,
            action=action,
            score=round(confidence, 4),
            entry_price=round(current_close, 5),
            sl=round(sl, 5),
            tp=round(tp, 5),
            regime=regime,
            timestamp=timestamp or datetime.utcnow(),
            strategy=self.name(),
            metadata={
                "rsi": round(rsi_val, 1),
                "bb_upper": round(bb_upper, 5),
                "bb_lower": round(bb_lower, 5),
                "bb_mid": round(bb_mid, 5),
                "atr": round(atr_v, 5),
                "oversold": oversold,
                "overbought": overbought,
            },
        )

    @staticmethod
    def _rsi(data, period, i):
        if i < period + 1:
            return float("nan")
        diff = np.diff(data[max(0, i - period - 1) : i + 1])
        if len(diff) < period:
            return float("nan")
        gains = np.where(diff > 0, diff, 0)
        losses = np.where(diff < 0, -diff, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _bollinger(data, period, std_dev, i):
        if i < period:
            return float("nan"), float("nan"), float("nan")
        segment = data[i - period + 1 : i + 1]
        mid = float(np.mean(segment))
        std = float(np.std(segment))
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        return upper, mid, lower

    @staticmethod
    def _atr(high, low, close, i, period=14):
        if i < period + 1:
            return 0.0
        tr_vals = [
            max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
            for j in range(i - period + 1, i + 1)
        ]
        return float(np.mean(tr_vals))

    def get_config(self) -> dict:
        return dict(self.config)
