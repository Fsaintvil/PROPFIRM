"""Détection de régime de marché : ADX, pente MA, percentile volatilité."""
import logging

import numpy as np

from engine_simple.indicators import adx, atr

logger = logging.getLogger("regime")

ADX_TREND_ENTER = 22   # Seuil pour entrer en mode TREND (hystérésis)
ADX_TREND_EXIT = 18    # Seuil pour sortir du mode TREND (hystérésis)
SLOPE_BULLISH = 0.002
SLOPE_BEARISH = -0.002
# Volatilité basée sur ratio ATR/prix fixe (pas percentile instable sur peu d'échantillons)
VOL_HIGH_RATIO = 0.015  # ATR > 1.5% du prix = HIGH_VOL
VOL_LOW_RATIO = 0.003   # ATR < 0.3% du prix = LOW_VOL


class RegimeDetector:
    """Détecte le régime en fonction de ADX, pente MA20, et volatilité relative."""

    def detect(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
               adx_val: float | None = None) -> tuple[str, dict]:
        if len(close) < 30:
            return "RANGING", {"adx": 0, "atr": 0, "slope": 0}

        if adx_val is None:
            adx_val, _, _ = self._calc_adx(high, low, close)
        atr_arr = atr(high, low, close, 14)
        atr_val = float(atr_arr[-1]) if isinstance(atr_arr, np.ndarray) else float(atr_arr)

        if atr_val <= 0:
            atr_val = float(np.mean(high[-20:] - low[-20:]) * 0.5)

        # Ratio ATR/prix fixe pour volatilité (stable, pas de problème de petits échantillons)
        atr_pct = atr_val / max(np.mean(close[-20:]), 1e-4)

        # Pente MA20
        ma20 = np.mean(close[-20:])
        ma20_prev = np.mean(close[-40:-20]) if len(close) >= 40 else ma20
        slope = (ma20 - ma20_prev) / max(ma20_prev, 1e-4)

        # Hystérésis ADX : on utilise _prev_regime stocké pour éviter le bouncing
        prev_regime = getattr(self, '_prev_regime', "RANGING")
        is_trending = prev_regime in ("TREND_UP", "TREND_DOWN")

        if is_trending:
            # En mode TREND, on sort si ADX < ADX_TREND_EXIT
            if adx_val < ADX_TREND_EXIT:
                is_trending = False
        else:
            # En mode RANGING, on entre si ADX >= ADX_TREND_ENTER
            if adx_val >= ADX_TREND_ENTER:
                is_trending = True

        # Décision
        if is_trending:
            if slope > SLOPE_BULLISH:
                regime = "TREND_UP"
            elif slope < SLOPE_BEARISH:
                regime = "TREND_DOWN"
            else:
                regime = "RANGING"
        elif atr_pct >= VOL_HIGH_RATIO:
            regime = "HIGH_VOL"
        elif atr_pct <= VOL_LOW_RATIO:
            regime = "LOW_VOL"
        else:
            regime = "RANGING"

        self._prev_regime = regime

        return regime, {
            "adx": adx_val, "atr": atr_val,
            "atr_pct": atr_pct,
            "slope": slope,
            "vol_percentile": atr_pct / 0.01,  # ratio transformé pour compatibilité (0.01 = 1% = 50e percentile)
        }

    def _calc_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> tuple:
        """Hook pour tests (peut être patché par les tests existants).
        Returns (adx, plus_di, minus_di)."""
        return adx(high, low, close, 14)
