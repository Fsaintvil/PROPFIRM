#!/usr/bin/env python3
"""
🧠 AMÉLIORATION: GESTION SIGNAUX CONTRADICTOIRES
Système avancé pour gérer les conflits entre différents modèles
"""

import numpy as np
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum


class SignalStrength(Enum):
    """Force du signal"""
    WEAK = 0.3
    MODERATE = 0.6
    STRONG = 0.8
    VERY_STRONG = 1.0


@dataclass
class Signal:
    """Structure d'un signal"""
    source: str
    action: str  # buy/sell/hold
    confidence: float
    strength: SignalStrength
    timeframe: str
    context: Dict[str, Any]


class ConflictResolutionEngine:
    """Moteur de résolution des conflits entre signaux"""
    
    def __init__(self):
        # Poids par source de signal (basé sur performance historique)
        self.source_weights = {
            'regime_detection': 0.35,    # Forte performance sur tendances
            'meta_learning': 0.30,       # Bon pour patterns complexes
            'rl_agent': 0.20,           # Adaptatif mais volatile
            'technical_indicators': 0.15 # Support pour confirmation
        }
        
        # Poids par timeframe
        self.timeframe_weights = {
            'M1': 0.05,   # Bruit court terme
            'M5': 0.15,   # Opportunités rapides
            'M15': 0.25,  # Sweet spot
            'H1': 0.35,   # Tendance moyenne
            'H4': 0.20    # Tendance long terme
        }
        
    def resolve_conflicts(self, signals: List[Signal]) -> Dict[str, Any]:
        """Résoudre conflits entre signaux multiples"""
        if not signals:
            return {'action': 'hold', 'confidence': 0.0, 'consensus': 'no_signals'}
            
        # 1. Grouper par action
        action_groups = self._group_by_action(signals)
        
        # 2. Calculer scores pondérés par action
        action_scores = {}
        for action, group_signals in action_groups.items():
            score = self._calculate_weighted_score(group_signals)
            action_scores[action] = score
            
        # 3. Déterminer action dominante
        dominant_action = max(action_scores.items(), key=lambda x: x[1])
        
        # 4. Évaluer niveau de consensus
        consensus_level = self._evaluate_consensus(action_scores)
        
        # 5. Ajuster confiance selon consensus
        base_confidence = dominant_action[1]
        adjusted_confidence = self._adjust_confidence_for_consensus(
            base_confidence, consensus_level
        )
        
        return {
            'action': dominant_action[0],
            'confidence': adjusted_confidence,
            'consensus': consensus_level,
            'action_scores': action_scores,
            'signal_count': len(signals),
            'conflict_resolution': 'weighted_voting'
        }
        
    def _group_by_action(self, signals: List[Signal]) -> Dict[str, List[Signal]]:
        """Grouper signaux par action"""
        groups = {}
        for signal in signals:
            action = signal.action
            if action not in groups:
                groups[action] = []
            groups[action].append(signal)
        return groups
        
    def _calculate_weighted_score(self, signals: List[Signal]) -> float:
        """Calculer score pondéré pour un groupe de signaux"""
        total_score = 0
        total_weight = 0
        
        for signal in signals:
            # Poids combiné : source + timeframe + force
            source_weight = self.source_weights.get(signal.source, 0.1)
            tf_weight = self.timeframe_weights.get(signal.timeframe, 0.1)
            strength_weight = signal.strength.value
            
            combined_weight = source_weight * tf_weight * strength_weight
            score = signal.confidence * combined_weight
            
            total_score += score
            total_weight += combined_weight
            
        return total_score / total_weight if total_weight > 0 else 0
        
    def _evaluate_consensus(self, action_scores: Dict[str, float]) -> str:
        """Évaluer niveau de consensus"""
        if len(action_scores) <= 1:
            return 'strong_consensus'
            
        sorted_scores = sorted(action_scores.values(), reverse=True)
        
        if len(sorted_scores) >= 2:
            dominant_score = sorted_scores[0]
            second_score = sorted_scores[1]
            
            # Ratio entre dominant et second
            if second_score == 0:
                ratio = float('inf')
            else:
                ratio = dominant_score / second_score
                
            if ratio >= 3.0:
                return 'strong_consensus'
            elif ratio >= 2.0:
                return 'moderate_consensus'
            elif ratio >= 1.5:
                return 'weak_consensus'
            else:
                return 'high_conflict'
        
        return 'weak_consensus'
        
    def _adjust_confidence_for_consensus(
        self, 
        base_confidence: float, 
        consensus_level: str
    ) -> float:
        """Ajuster confiance selon niveau de consensus"""
        adjustments = {
            'strong_consensus': 1.1,     # Boost confiance
            'moderate_consensus': 1.0,   # Pas de changement
            'weak_consensus': 0.9,       # Réduction légère
            'high_conflict': 0.7         # Réduction forte
        }
        
        multiplier = adjustments.get(consensus_level, 1.0)
        return min(base_confidence * multiplier, 1.0)


class SignalQualityFilter:
    """Filtre qualité des signaux"""
    
    def __init__(self):
        self.min_confidence = 0.4
        self.max_age_minutes = 30
        
    def filter_signals(self, signals: List[Signal]) -> List[Signal]:
        """Filtrer signaux selon critères qualité"""
        filtered = []
        
        for signal in signals:
            # Vérifications qualité
            if self._is_signal_valid(signal):
                filtered.append(signal)
                
        return filtered
        
    def _is_signal_valid(self, signal: Signal) -> bool:
        """Vérifier validité d'un signal"""
        # Confiance minimum
        if signal.confidence < self.min_confidence:
            return False
            
        # Action valide
        if signal.action not in ['buy', 'sell', 'hold']:
            return False
            
        # Source reconnue
        if signal.source not in ['regime_detection', 'meta_learning', 
                                'rl_agent', 'technical_indicators']:
            return False
            
        return True


def create_test_signals() -> List[Signal]:
    """Créer signaux de test"""
    return [
        Signal('regime_detection', 'buy', 0.75, SignalStrength.STRONG, 'H1', {}),
        Signal('meta_learning', 'buy', 0.68, SignalStrength.MODERATE, 'M15', {}),
        Signal('rl_agent', 'sell', 0.55, SignalStrength.WEAK, 'M5', {}),
        Signal('technical_indicators', 'buy', 0.72, SignalStrength.STRONG, 'H1', {}),
    ]


def test_conflict_resolution():
    """Test du système de résolution de conflits"""
    print("🧠 TEST RÉSOLUTION CONFLITS SIGNAUX")
    print("=" * 50)
    
    engine = ConflictResolutionEngine()
    filter_engine = SignalQualityFilter()
    
    # Créer signaux test
    test_signals = create_test_signals()
    
    print(f"📊 Signaux d'entrée: {len(test_signals)}")
    for signal in test_signals:
        print(f"   {signal.source}: {signal.action} ({signal.confidence:.2f})")
    
    # Filtrer signaux
    filtered_signals = filter_engine.filter_signals(test_signals)
    print(f"\n✅ Signaux valides: {len(filtered_signals)}")
    
    # Résoudre conflits
    resolution = engine.resolve_conflicts(filtered_signals)
    
    print(f"\n🎯 RÉSOLUTION:")
    print(f"   Action finale: {resolution['action']}")
    print(f"   Confiance: {resolution['confidence']:.3f}")
    print(f"   Consensus: {resolution['consensus']}")
    print(f"   Scores par action: {resolution['action_scores']}")
    
    print("\n✅ Test résolution conflits terminé")


if __name__ == "__main__":
    test_conflict_resolution()