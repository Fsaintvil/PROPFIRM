"""Stub Reinforcement Learning agent minimal pour tests et dry-run.
Classe minimale ReinforcementLearningTradingSystem utilisée par le moteur.
Ne lance aucun entraînement ni connexion externe.
"""
from typing import Any


class ReinforcementLearningTradingSystem:
    def __init__(self, use_dqn: bool = True, **kwargs: Any):
        self.use_dqn = use_dqn
        self.trained = False

    def predict(self, state):
        """Retourne une action factice et une confiance."""
        return {"action": "hold", "confidence": 0.0}

    def load(self, path: str):
        self.trained = True

    def save(self, path: str):
        return True
