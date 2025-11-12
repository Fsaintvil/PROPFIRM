"""Root-level minimal MarketRegimeDetector with price-direction logic."""
from typing import Dict, Any


class MarketRegimeDetector:
    def __init__(self, n_regimes: int = 3):
        self.n_regimes = n_regimes
        self.regime_names = [f"Regime_{i}" for i in range(n_regimes)]

    def detect_regimes(self, data) -> Dict[str, Any]:
        """
        Détecte le régime du marché en fonction de la variation du prix.

        Args:
            data: liste ou série de prix (au moins 2 valeurs)
        Returns:
            dict: {"current_regime": int, "probabilities": [float, ...]}
        """
        if not data or len(data) < 2:
            # Données insuffisantes -> marché neutre
            return {"current_regime": 0, "probabilities": [1.0, 0.0, 0.0]}

        delta = data[-1] - data[-2]

        if delta > 0:
            # Hausse → régime haussier (buy)
            return {"current_regime": 1, "probabilities": [0.0, 1.0, 0.0]}
        elif delta < 0:
            # Baisse → régime baissier (sell)
            return {"current_regime": 2, "probabilities": [0.0, 0.0, 1.0]}
        else:
            # Stable → régime neutre (hold)
            return {"current_regime": 0, "probabilities": [1.0, 0.0, 0.0]}

    def get_regime_strategy_signals(self, regime_index: int) -> Dict[str, Any]:
        """
        Traduit un régime en signal de trading.
        """
        if regime_index == 0:
            return {"action": "hold", "confidence": 0.5}
        elif regime_index == 1:
            return {"action": "buy", "confidence": 0.7}
        else:
            return {"action": "sell", "confidence": 0.7}
