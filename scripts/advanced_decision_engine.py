"""Stub minimal pour advanced_decision_engine

Ce module fournit une implémentation non-invasive et sûre de
`AdvancedDecisionEngine` lorsque le vrai module est absent.

Le but : supprimer les warnings répétitifs en production et
fournir un point d'insertion pour une future implémentation complète.
La classe retourne systématiquement enhancement_applied=False
pour ne pas altérer le comportement de trading.
"""
from typing import Any, Dict


class AdvancedDecisionEngine:
    """Implementation minimale et sûre du moteur de décision avancé.

    Note: cette version est volontairement non-invasive : elle ne modifie
    pas les signaux et sert uniquement à éviter les ImportError en production.
    """

    def __init__(self, *args, **kwargs):
        # aucun état persistant requis pour le stub
        self.initialized = True

    def make_enhanced_decision(self, symbol: str, data: Any, base_signals: Dict) -> Dict:
        """Retourne une dict indiquant qu'aucune amélioration n'a été appliquée.

        Le format retourné correspond à l'usage attendu par
        `LiveTradingEngine.apply_advanced_decision_engine`.
        """
        try:
            return {
                "enhancement_applied": False,
                "action": base_signals.get("combined_signal", "hold"),
                "confidence": base_signals.get("confidence", 0.0),
            }
        except Exception:
            return {"enhancement_applied": False, "action": "hold", "confidence": 0.0}
