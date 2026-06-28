"""
Breakout — Stratégie de cassure de range.

Principe :
  - Identifier les ranges (période de consolidation)
  - Cassure du haut du range → BUY
  - Cassure du bas du range → SELL
  - Confirmation : volume > moyenne × 1.5, ATR en expansion
  - Entrée : dès que le prix dépasse le range + filtre ATR

Paramètres :
  - lookback: 20 (période pour identifier le range)
  - volume_mult: 1.5 (multiplicateur de volume pour confirmer)
  - atr_expansion: 1.2 (expansion ATR minimale)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from engine_simple.backtest_core.strategies.base import Strategy, Signal

logger = logging.getLogger("backtest_core.strategies.breakout")

DEFAULT_CONFIG = {
    "lookback": 20,
    "volume_mult": 1.5,
    "atr_expansion": 1.2,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 3.0,
    "min_score": 0.50,
}


class Breakout(Strategy):
    """Stratégie de cassure de range avec confirmation volume + ATR."""

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    def name(self) -> str:
        return "Breakout"

    def generate(
        self, bar_idx: int, data: dict, regime: str, open_positions: list, timestamp: Optional[datetime] = None
    ) -> Optional[Signal]:
        lookback = self.config["lookback"]
        if bar_idx < lookback + 20:
            return None

        close = data["close"]
        high = data["high"]
        low = data["low"]
        vol = data.get("volume")

        # Range sur les N dernières barres
        range_high = float(np.max(high[bar_idx - lookback : bar_idx]))
        range_low = float(np.min(low[bar_idx - lookback : bar_idx]))
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        # Prix courant
        current_close = close[bar_idx]
        current_high = high[bar_idx]
        current_low = low[bar_idx]

        # ATR
        atr_v = self._atr(high, low, close, bar_idx)
        if atr_v <= 0:
            return None

        # Vérifier si on est en sortie de range
        breakout_up = current_close > range_high and current_high > range_high
        breakout_down = current_close < range_low and current_low < range_low

        if not breakout_up and not breakout_down:
            return None

        # Confirmation volume
        if vol is not None:
            vol_sma = np.mean(vol[bar_idx - lookback : bar_idx])
            if vol_sma > 0 and vol[bar_idx] < vol_sma * self.config["volume_mult"]:
                return None

        # Confirmation ATR expansion
        atr_prev = self._atr(high, low, close, bar_idx - lookback)
        if atr_prev > 0 and atr_v < atr_prev * self.config["atr_expansion"]:
            return None

        # Direction
        action = "BUY" if breakout_up else "SELL"

        # SL/TP
        if action == "BUY":
            sl = range_low - 0.5 * atr_v  # SL sous le range
            tp = current_close + (current_close - sl) * 2.0  # RR = 2
        else:
            sl = range_high + 0.5 * atr_v
            tp = current_close - (sl - current_close) * 2.0

        # Score : basé sur la force de la cassure
        if breakout_up:
            breakout_strength = (current_close - range_high) / atr_v
        else:
            breakout_strength = (range_low - current_close) / atr_v

        confidence = min(1.0, max(0.0, breakout_strength / 2.0))
        # Réduire le score si range trop étroit (fausse cassure probable)
        if range_size < atr_v * 0.5:
            confidence *= 0.5

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
                "range_high": round(range_high, 5),
                "range_low": round(range_low, 5),
                "range_size": round(range_size, 5),
                "atr": round(atr_v, 5),
                "breakout_strength": round(breakout_strength, 3),
            },
        )

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
