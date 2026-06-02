"""Détection de régime de marché : ADX, pente MA, percentile volatilité."""
import logging

import numpy as np

from engine_simple.indicators import adx, atr

logger = logging.getLogger("regime")

ADX_TREND_THRESHOLD = 20
SLOPE_BULLISH = 0.002
SLOPE_BEARISH = -0.002
VOL_HIGH_PCT = 0.80
VOL_LOW_PCT = 0.20


class RegimeDetector:
    """Détecte le régime en fonction de ADX, pente MA20, et volatilité relative."""

    def detect(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
               adx_val: float | None = None) -> tuple[str, dict]:
        if len(close) < 30:
            return "RANGING", {"adx": 0, "atr": 0, "slope": 0}

        if adx_val is None:
            adx_val = self._calc_adx(high, low, close)
        atr_arr = atr(high, low, close, 14)
        atr_val = float(atr_arr[-1]) if isinstance(atr_arr, np.ndarray) else float(atr_arr)

        if atr_val <= 0:
            atr_val = float(np.mean(high[-20:] - low[-20:]) * 0.5)

        # Percentile de volatilité (ATR actuel vs historique)
        atr_history = np.array([
            float(atr(high[:i], low[:i], close[:i], 14)[-1]
                  if isinstance(atr(high[:i], low[:i], close[:i], 14), np.ndarray)
                  else atr(high[:i], low[:i], close[:i], 14))
            for i in range(20, len(close), 5)
        ])
        vol_percentile = np.mean(atr_history < atr_val) if len(atr_history) > 0 else 0.5

        # Pente MA20
        ma20 = np.mean(close[-20:])
        ma20_prev = np.mean(close[-40:-20]) if len(close) >= 40 else ma20
        slope = (ma20 - ma20_prev) / max(ma20_prev, 1e-4)

        # Décision
        if adx_val >= ADX_TREND_THRESHOLD:
            if slope > SLOPE_BULLISH:
                regime = "TREND_UP"
            elif slope < SLOPE_BEARISH:
                regime = "TREND_DOWN"
            else:
                regime = "RANGING"
        elif vol_percentile >= VOL_HIGH_PCT:
            regime = "HIGH_VOL"
        elif vol_percentile <= VOL_LOW_PCT:
            regime = "LOW_VOL"
        else:
            regime = "RANGING"

        return regime, {
            "adx": adx_val, "atr": atr_val,
            "atr_pct": atr_val / max(np.mean(close[-20:]), 1e-4),
            "slope": slope, "vol_percentile": vol_percentile,
        }

    def _calc_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
        """Hook pour tests (peut être patché par les tests existants)."""
        return adx(high, low, close, 14)
