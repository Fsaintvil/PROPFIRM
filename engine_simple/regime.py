"""Détection de régime de marché : ADX, pente MA, percentile volatilité.

Les paramètres sont lus depuis la configuration YAML (config/default.yaml).
Les valeurs hardcodées ici sont des FALLBACKS si la config est indisponible.
"""

import logging

import numpy as np

from engine_simple.indicators import adx, atr

logger = logging.getLogger("regime")

# ── Chargement depuis la config YAML avec fallback ──
try:
    import config_simple as _cfg

    ADX_TREND_ENTER_DEFAULT = getattr(_cfg, "REGIME_ADX_TREND_ENTER", 22)
    ADX_TREND_EXIT_DEFAULT = getattr(_cfg, "REGIME_ADX_TREND_EXIT", 18)
    HYSTERESIS_OFFSET = getattr(_cfg, "REGIME_HYSTERESIS_OFFSET", 4)
    SLOPE_BULLISH = getattr(_cfg, "REGIME_SLOPE_BULLISH", 0.002)
    SLOPE_BEARISH = getattr(_cfg, "REGIME_SLOPE_BEARISH", -0.002)
    VOL_HIGH_RATIO = getattr(_cfg, "REGIME_VOL_HIGH_RATIO", 0.015)
    VOL_LOW_RATIO = getattr(_cfg, "REGIME_VOL_LOW_RATIO", 0.003)
except Exception:
    logger.warning("Config YAML indisponible, utilisation des fallbacks hardcodes")
    ADX_TREND_ENTER_DEFAULT = 22
    ADX_TREND_EXIT_DEFAULT = 18
    HYSTERESIS_OFFSET = 4
    SLOPE_BULLISH = 0.002
    SLOPE_BEARISH = -0.002
    VOL_HIGH_RATIO = 0.015
    VOL_LOW_RATIO = 0.003

# Alias de compatibilité pour les tests existants
ADX_TREND_ENTER = ADX_TREND_ENTER_DEFAULT
ADX_TREND_EXIT = ADX_TREND_EXIT_DEFAULT


class RegimeDetector:
    """Détecte le régime en fonction de ADX, pente MA20, et volatilité relative.
    _prev_regime est stocké par symbole (dict) pour éviter la cross-contamination."""

    def __init__(self):
        self._prev_regime: dict[str, str] = {}

    def _get_adx_thresholds(self, symbol: str) -> tuple[float, float]:
        """Retourne (enter_threshold, exit_threshold) pour un symbole donné.

        Lit adx_thresh depuis la config SYMBOL_LIMITS (YAML).
        Ex: BTCUSD adx_thresh=20 → enter=20, exit=16 (hysteresis de 4 points).
        Fallback: enter=22, exit=18 si le symbole n'est pas dans la config.
        """
        try:
            import config_simple as _cfg

            sym_limits = _cfg.SYMBOL_LIMITS.get(symbol, {})
            if isinstance(sym_limits, dict):
                adx_thresh = sym_limits.get("adx_thresh")
                if adx_thresh is not None and adx_thresh > 0:
                    enter = float(adx_thresh)
                    exit_ = max(enter - HYSTERESIS_OFFSET, 5.0)  # plancher 5
                    return enter, exit_
        except Exception as e:
            logger.warning(f"  [REGIME] _get_adx_threshold symbol={symbol}: {e}")
            pass
        return ADX_TREND_ENTER_DEFAULT, ADX_TREND_EXIT_DEFAULT

    def detect(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        adx_val: float | None = None,
        symbol: str = "_default",
    ) -> tuple[str, dict]:
        if len(close) < 30:
            return "RANGING", {"adx": 0, "atr": 0, "slope": 0}

        if adx_val is None:
            adx_val, _, _ = self._calc_adx(high, low, close)
        adx_val = adx_val or 0.0  # Guarantee float for comparisons
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

        # Seuils ADX par symbole (lit adx_thresh depuis la config YAML)
        adx_enter, adx_exit = self._get_adx_thresholds(symbol)

        # Hystérésis ADX : stocké par symbole pour éviter bouncing + cross-contamination
        prev_regime = self._prev_regime.get(symbol, "RANGING")
        is_trending = prev_regime in ("TREND_UP", "TREND_DOWN")

        if is_trending:
            # En mode TREND, on sort si ADX < adx_exit
            if adx_val < adx_exit:
                is_trending = False
        else:
            # En mode RANGING, on entre si ADX >= adx_enter
            if adx_val >= adx_enter:
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

        self._prev_regime[symbol] = regime

        return regime, {
            "adx": adx_val,
            "atr": atr_val,
            "atr_pct": atr_pct,
            "slope": slope,
            "vol_percentile": atr_pct / 0.01,  # ratio transformé pour compatibilité (0.01 = 1% = 50e percentile)
        }

    def _calc_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> tuple:
        """Hook pour tests (peut être patché par les tests existants).
        Returns (adx, plus_di, minus_di)."""
        return adx(high, low, close, 14)
