"""Root-level stub ReinforcementLearningTradingSystem for compatibility.
This is a minimal, non-invasive implementation used in tests and dry-run.
"""
from typing import Any


class ReinforcementLearningTradingSystem:
    def __init__(self, use_dqn: bool = True, **kwargs: Any):
        self.use_dqn = use_dqn
        self.trained = False

    def predict(self, state):
        return {"action": "hold", "confidence": 0.0}

    def load(self, path: str):
        self.trained = True

    def save(self, path: str):
        return True
