"""Regime Engine — Détection avancée de 6 régimes de marché.

Remplace regime.py avec une détection plus sophistiquée :
1. STRONG_UPTREND  — ADX > 25, slope > 0.3%, ATR modéré
2. WEAK_UPTREND    — ADX 22-25, slope > 0.1%
3. RANGING         — ADX < 22, pas de direction claire
4. WEAK_DOWNTREND  — ADX 22-25, slope < -0.1%
5. STRONG_DOWNTREND — ADX > 25, slope < -0.3%
6. HIGH_VOL        — ATR > 1.5% du prix (indépendant de ADX)

Features:
- Hystérésis ADX par symbole (22 entrée / 18 sortie)
- Slope MA20 pondéré (recent > old)
- Volatilité relative (ATR/prix)
- Persistence de regime (evite le bounce)
- Score de confiance [0-1]

Usage:
    engine = RegimeEngine()
    regime, meta = engine.detect(high, low, close, symbol="XAUUSD")
"""
import logging
from dataclasses import dataclass, field

import numpy as np

from engine_simple.indicators import adx, atr

logger = logging.getLogger("regime_engine")

# ============================================================================
# CONSTANTS
# ============================================================================
ADX_TREND_ENTER = 22    # Hystérésis: entrée en mode TREND
ADX_TREND_EXIT = 18     # Hystérésis: sortie du mode TREND
ADX_STRONG_THRESHOLD = 25  # Seuil pour STRONG trend
SLOPE_STRONG = 0.003    # Pente forte (> 0.3%)
SLOPE_WEAK = 0.001      # Pente faible (> 0.1%)
VOL_HIGH_RATIO = 0.015  # ATR > 1.5% du prix = HIGH_VOL
VOL_LOW_RATIO = 0.003   # ATR < 0.3% du prix = LOW_VOL

# 6 régimes possibles
REGIME_STRONG_UPTREND = "STRONG_UPTREND"
REGIME_WEAK_UPTREND = "WEAK_UPTREND"
REGIME_RANGING = "RANGING"
REGIME_WEAK_DOWNTREND = "WEAK_DOWNTREND"
REGIME_STRONG_DOWNTREND = "STRONG_DOWNTREND"
REGIME_HIGH_VOL = "HIGH_VOL"
REGIME_LOW_VOL = "LOW_VOL"

ALL_REGIMES = [
    REGIME_STRONG_UPTREND, REGIME_WEAK_UPTREND, REGIME_RANGING,
    REGIME_WEAK_DOWNTREND, REGIME_STRONG_DOWNTREND,
    REGIME_HIGH_VOL, REGIME_LOW_VOL,
]


@dataclass
class RegimeResult:
    """Résultat de la détection de régime."""
    regime: str
    confidence: float  # [0-1]
    adx: float
    atr: float
    atr_pct: float
    slope: float
    prev_regime: str
    is_trending: bool
    details: dict = field(default_factory=dict)


class RegimeEngine:
    """Moteur de détection de régime avancé avec 7 régimes.
    
    Features:
    - Hystérésis ADX par symbole (evite le bounce)
    - Slope MA20 pondéré (recent > old)
    - Volatilité relative (ATR/prix)
    - Score de confiance [0-1]
    - Persistence de régime (minimal switching)
    """
    
    def __init__(self):
        self._prev_regime: dict[str, str] = {}
        self._regime_duration: dict[str, int] = {}
    
    def detect(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
               adx_val: float | None = None,
               symbol: str = "_default") -> RegimeResult:
        """Détecte le régime de marché.
        
        Args:
            high: Array des prix hauts
            low: Array des prix bas
            close: Array des prix de clôture
            adx_val: ADX pré-calculé (optionnel)
            symbol: Nom du symbole (pour hystérésis)
        
        Returns:
            RegimeResult avec regime, confidence, et métadonnées
        """
        if len(close) < 30:
            return RegimeResult(
                regime=REGIME_RANGING, confidence=0.3,
                adx=0, atr=0, atr_pct=0, slope=0,
                prev_regime="RANGING", is_trending=False
            )
        
        # Calculate indicators
        if adx_val is None:
            adx_val, plus_di, minus_di = self._calc_adx(high, low, close)
        else:
            plus_di, minus_di = 0, 0
        
        atr_arr = atr(high, low, close, 14)
        atr_val = float(atr_arr[-1]) if isinstance(atr_arr, np.ndarray) else float(atr_arr)
        
        if atr_val <= 0:
            atr_val = float(np.mean(high[-20:] - low[-20:]) * 0.5)
        
        # ATR percentage
        atr_pct = atr_val / max(np.mean(close[-20:]), 1e-4)
        
        # Weighted MA20 slope (recent bars weighted more)
        ma20 = self._weighted_ma(close[-20:], decay=0.9)
        ma20_prev = self._weighted_ma(close[-40:-20], decay=0.9) if len(close) >= 40 else ma20
        slope = (ma20 - ma20_prev) / max(ma20_prev, 1e-4)
        
        # Previous regime for hysteresis
        prev_regime = self._prev_regime.get(symbol, REGIME_RANGING)
        is_trending = prev_regime in (REGIME_STRONG_UPTREND, REGIME_WEAK_UPTREND,
                                       REGIME_WEAK_DOWNTREND, REGIME_STRONG_DOWNTREND)
        
        # Hysteresis logic
        if is_trending:
            if adx_val < ADX_TREND_EXIT:
                is_trending = False
        else:
            if adx_val >= ADX_TREND_ENTER:
                is_trending = True
        
        # Regime classification
        confidence = 0.5
        
        if atr_pct >= VOL_HIGH_RATIO:
            # HIGH_VOL takes priority (independent of ADX)
            regime = REGIME_HIGH_VOL
            confidence = min(1.0, 0.5 + (atr_pct - VOL_HIGH_RATIO) / VOL_HIGH_RATIO)
        elif atr_pct <= VOL_LOW_RATIO:
            regime = REGIME_LOW_VOL
            confidence = min(1.0, 0.5 + (VOL_LOW_RATIO - atr_pct) / VOL_LOW_RATIO)
        elif is_trending:
            if adx_val >= ADX_STRONG_THRESHOLD:
                # Strong trend
                if slope > SLOPE_STRONG:
                    regime = REGIME_STRONG_UPTREND
                    confidence = min(1.0, 0.6 + (adx_val - ADX_STRONG_THRESHOLD) / 20)
                elif slope < -SLOPE_STRONG:
                    regime = REGIME_STRONG_DOWNTREND
                    confidence = min(1.0, 0.6 + (adx_val - ADX_STRONG_THRESHOLD) / 20)
                else:
                    # ADX strong but slope weak → use previous direction
                    if prev_regime in (REGIME_STRONG_UPTREND, REGIME_WEAK_UPTREND):
                        regime = REGIME_STRONG_UPTREND
                    else:
                        regime = REGIME_STRONG_DOWNTREND
                    confidence = 0.5
            else:
                # Weak trend (ADX 22-25)
                if slope > SLOPE_WEAK:
                    regime = REGIME_WEAK_UPTREND
                    confidence = 0.5 + (adx_val - ADX_TREND_ENTER) / (ADX_STRONG_THRESHOLD - ADX_TREND_ENTER) * 0.3
                elif slope < -SLOPE_WEAK:
                    regime = REGIME_WEAK_DOWNTREND
                    confidence = 0.5 + (adx_val - ADX_TREND_ENTER) / (ADX_STRONG_THRESHOLD - ADX_TREND_ENTER) * 0.3
                else:
                    regime = REGIME_RANGING
                    confidence = 0.4
        else:
            # Not trending → RANGING
            regime = REGIME_RANGING
            confidence = 0.5 + (ADX_TREND_EXIT - adx_val) / ADX_TREND_EXIT * 0.3
        
        # Update state
        self._prev_regime[symbol] = regime
        self._regime_duration[symbol] = self._regime_duration.get(symbol, 0) + 1
        
        # Confidence adjustment for regime persistence
        if regime == prev_regime:
            confidence = min(1.0, confidence + 0.1)  # Bonus for persistence
        
        return RegimeResult(
            regime=regime,
            confidence=round(confidence, 3),
            adx=round(adx_val, 2),
            atr=round(atr_val, 6),
            atr_pct=round(atr_pct * 100, 4),
            slope=round(slope * 100, 4),
            prev_regime=prev_regime,
            is_trending=is_trending,
            details={
                "plus_di": round(plus_di, 2) if plus_di else None,
                "minus_di": round(minus_di, 2) if minus_di else None,
                "duration": self._regime_duration.get(symbol, 0),
            }
        )
    
    def _weighted_ma(self, arr: np.ndarray, decay: float = 0.9) -> float:
        """Moving average avec decay exponentiel (recent > old)."""
        if len(arr) == 0:
            return 0.0
        weights = np.array([decay ** i for i in range(len(arr) - 1, -1, -1)])
        return float(np.sum(arr * weights) / np.sum(weights))
    
    def _calc_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> tuple:
        """Calcule ADX, +DI, -DI."""
        adx_val, plus_di, minus_di = adx(high, low, close, 14)
        return adx_val, plus_di, minus_di
    
    def get_regime_for_trading(self, regime: str) -> dict:
        """Retourne les paramètres de trading adaptés au régime."""
        params = {
            REGIME_STRONG_UPTREND: {
                "sl_mult": 2.0, "tp_mult": 5.0, "risk_mult": 1.0,
                "trailing_first_lock": 1.0, "trailing_n1": 0.80,
                "description": "Fort uptrend — SL serré, TP large"
            },
            REGIME_WEAK_UPTREND: {
                "sl_mult": 1.8, "tp_mult": 4.5, "risk_mult": 0.9,
                "trailing_first_lock": 1.0, "trailing_n1": 0.70,
                "description": "Faible uptrend — paramètres modérés"
            },
            REGIME_RANGING: {
                "sl_mult": 1.5, "tp_mult": 4.0, "risk_mult": 1.0,
                "trailing_first_lock": 1.0, "trailing_n1": 0.50,
                "description": "Range — SL large, TP modéré"
            },
            REGIME_WEAK_DOWNTREND: {
                "sl_mult": 1.8, "tp_mult": 4.5, "risk_mult": 0.9,
                "trailing_first_lock": 1.0, "trailing_n1": 0.70,
                "description": "Faible downtrend — paramètres modérés"
            },
            REGIME_STRONG_DOWNTREND: {
                "sl_mult": 2.0, "tp_mult": 5.0, "risk_mult": 1.0,
                "trailing_first_lock": 1.0, "trailing_n1": 0.80,
                "description": "Fort downtrend — SL serré, TP large"
            },
            REGIME_HIGH_VOL: {
                "sl_mult": 2.5, "tp_mult": 6.0, "risk_mult": 0.7,
                "trailing_first_lock": 1.2, "trailing_n1": 1.00,
                "description": "Haute volatilité — SL large, risque réduit"
            },
            REGIME_LOW_VOL: {
                "sl_mult": 1.5, "tp_mult": 3.5, "risk_mult": 1.0,
                "trailing_first_lock": 0.8, "trailing_n1": 0.40,
                "description": "Basse volatilité — SL/TP serrés"
            },
        }
        return params.get(regime, params[REGIME_RANGING])
    
    def get_regime_history(self, symbol: str) -> list[str]:
        """Retourne l'historique des régimes pour un symbole."""
        # In production, this would be stored in a buffer
        return [self._prev_regime.get(symbol, REGIME_RANGING)]
