"""Signal : MOM20x3 pur + classe Signal value object."""
import logging
from dataclasses import dataclass

import numpy as np

from engine_simple.indicators import atr

logger = logging.getLogger("strategy")

MIN_SIGNAL_SCORE = 0.55


@dataclass
class Signal:
    action: str       # BUY | SELL | HOLD
    score: float      # 0-1 confiance brute
    confidence: float # 0-1 calibré
    regime: str
    adx: float
    atr_val: float
    threshold: float
    rr_ratio: float   # risk/reward attendu

    def is_valid(self, min_score: float = MIN_SIGNAL_SCORE) -> bool:
        return self.action in ("BUY", "SELL") and self.score >= min_score


THRESHOLD_BY_REGIME = {
    "TREND_UP": 2.5, "TREND_DOWN": 2.5,
    "RANGING": 2.0, "HIGH_VOL": 2.0, "LOW_VOL": 2.0,
}
MAX_THRESHOLD = 3.0
MOMENTUM_LOOKBACK = 20
VOLUME_LOOKBACK = 50


class MOM20x3:
    """Momentum breakout sur 20 périodes avec seuil adaptatif ATR."""

    def __init__(self, rates_dict: dict[str, np.ndarray], symbol: str):
        self.symbol = symbol
        self.h1 = rates_dict.get("H1")
        self.m15 = rates_dict.get("M15")
        self.m5 = rates_dict.get("M5")

    def analyze(self, regime: str, adx_val: float, atr_val: float,
                min_score: float = 0.55, risk_mult: float = 1.0,
                adx_thresh: float = 22) -> Signal | None:
        if self.h1 is None or len(self.h1) < MOMENTUM_LOOKBACK + 5:
            logger.debug(f"{self.symbol}: pas assez de donnees H1")
            return None

        close = np.array([r[4] for r in self.h1[-MOMENTUM_LOOKBACK - 5:]], dtype=float)

        # Breakout = dernier close - close d'il y a 20 périodes
        momentum = close[-1] - close[-MOMENTUM_LOOKBACK - 1]
        thresh_base = THRESHOLD_BY_REGIME.get(regime, 2.0)

        # Seuil dynamique : ADX bas → seuil plus bas (pour capter les petits moves)
        if adx_val < adx_thresh:
            thresh_base = min(thresh_base, 2.0)

        threshold = min(thresh_base * risk_mult, MAX_THRESHOLD) * atr_val

        direction = None
        if momentum > threshold:
            direction = "BUY"
        elif momentum < -threshold:
            direction = "SELL"

        if direction is None:
            return None

        score = min(1.0, abs(momentum) / (threshold * 1.5) if threshold > 0 else 0.5)
        confidence = score * (1.0 + 0.2 * (1 if adx_val > adx_thresh else 0))
        confidence = min(1.0, confidence)

        rr = 2.0
        if regime in ("TREND_UP", "TREND_DOWN"):
            rr = 2.5

        return Signal(
            action=direction,
            score=score,
            confidence=min(1.0, confidence),
            regime=regime,
            adx=adx_val,
            atr_val=atr_val,
            threshold=threshold,
            rr_ratio=rr,
        )
