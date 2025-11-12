"""Optimized MultiAssetPortfolioOptimizer for Forex-dominant allocation.
Favors Forex instruments with adaptive weighting and light diversification.
"""

from typing import Any, Dict

class MultiAssetPortfolioOptimizer:
    def __init__(self, forex_bias: float = 0.7, crypto_bias: float = 0.1, metal_bias: float = 0.1, index_bias: float = 0.1):
        self.bias = {
            "forex": forex_bias,
            "crypto": crypto_bias,
            "metal": metal_bias,
            "index": index_bias,
        }

    def allocate(self, assets: Dict[str, float]) -> Dict[str, float]:
        if not assets:
            return {}

        # Define asset classes
        forex_pairs = {"USDCAD", "AUDNZD", "EURJPY", "GBPCHF", "NZDJPY", "EURUSD", "EURAUD"}
        metals = {"XAUUSD"}
        crypto = {"BTCUSD", "ETHUSD"}
        indices = {"US500.cash", "JP225.cash"}

        # Separate assets by class
        classified = {
            "forex": [a for a in assets if a in forex_pairs],
            "metal": [a for a in assets if a in metals],
            "crypto": [a for a in assets if a in crypto],
            "index": [a for a in assets if a in indices],
        }

        # Compute allocation per class
        allocation = {}
        for cls, symbols in classified.items():
            if not symbols:
                continue
            per_symbol = self.bias[cls] / len(symbols)
            for s in symbols:
                allocation[s] = round(per_symbol, 4)

        # Normalize in case some classes are missing
        total = sum(allocation.values())
        if total > 0:
            allocation = {k: v / total for k, v in allocation.items()}

        return allocation

    def optimize(self, historical_data) -> bool:
        # Placeholder for later ML optimization
        return True
