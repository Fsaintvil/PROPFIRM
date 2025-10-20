"""Root-level minimal MarketRegimeDetector stub for compatibility."""
from typing import Dict, Any


class MarketRegimeDetector:
    def __init__(self, n_regimes: int = 3):
        self.n_regimes = n_regimes
        self.regime_names = [f"Regime_{i}" for i in range(n_regimes)]

    def detect_regimes(self, data) -> Dict[str, Any]:
        return {"current_regime": 0, "probabilities": [1.0] + [0.0] * (self.n_regimes-1)}

    def get_regime_strategy_signals(self, regime_index: int) -> Dict[str, Any]:
        if regime_index == 0:
            return {"action": "hold", "confidence": 0.5}
        elif regime_index == 1:
            return {"action": "buy", "confidence": 0.6}
        else:
            return {"action": "sell", "confidence": 0.6}
