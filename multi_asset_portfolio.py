"""Root-level stub for MultiAssetPortfolioOptimizer used by the engine.
Non-invasive: returns equal allocations by default.
"""
from typing import Any, Dict


class MultiAssetPortfolioOptimizer:
    def __init__(self, **kwargs: Any):
        self.params = kwargs

    def allocate(self, assets: Dict[str, float]):
        if not assets:
            return {}
        n = len(assets)
        return {s: 1.0 / n for s in assets}

    def optimize(self, historical_data):
        return True
