#!/usr/bin/env python3
"""
🎯 AMÉLIORATION: OPTIMISATION SEUILS DE CONFIANCE
Système d'optimisation adaptative des seuils selon les conditions de marché
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import json


class ConfidenceThresholdOptimizer:
    """Optimiseur adaptatif des seuils de confiance"""
    
    def __init__(self, initial_threshold: float = 0.68):
        self.current_threshold = initial_threshold
        self.performance_history = []
        self.market_conditions = {}
        
        # Paramètres d'optimisation
        self.optimization_window = 100  # trades pour évaluation
        self.adjustment_step = 0.02     # pas d'ajustement
        self.min_threshold = 0.60       # limite basse
        self.max_threshold = 0.85       # limite haute
        
    def update_performance(self, trade_result: Dict):
        """Mettre à jour historique performance"""
        self.performance_history.append({
            'timestamp': datetime.now(),
            'threshold_used': self.current_threshold,
            'profit': trade_result.get('profit', 0),
            'success': trade_result.get('success', False),
            'market_condition': self._assess_market_condition()
        })
        
        # Garder seulement la fenêtre d'optimisation
        if len(self.performance_history) > self.optimization_window:
            self.performance_history.pop(0)
            
    def _assess_market_condition(self) -> str:
        """Évaluer condition marché actuelle"""
        # Simulation - à remplacer par vraie logique
        import random
        return random.choice(['trending', 'ranging', 'volatile', 'calm'])
        
    def optimize_threshold(self) -> float:
        """Optimiser le seuil selon performance récente"""
        if len(self.performance_history) < 20:
            return self.current_threshold
            
        # Analyser performance par condition de marché
        recent_trades = self.performance_history[-50:]
        
        # Calculer métriques par seuil utilisé
        threshold_performance = {}
        for trade in recent_trades:
            threshold = trade['threshold_used']
            if threshold not in threshold_performance:
                threshold_performance[threshold] = {'profits': [], 'wins': 0, 'total': 0}
                
            threshold_performance[threshold]['profits'].append(trade['profit'])
            threshold_performance[threshold]['total'] += 1
            if trade['success']:
                threshold_performance[threshold]['wins'] += 1
                
        # Trouver seuil optimal
        best_threshold = self.current_threshold
        best_score = -float('inf')
        
        for threshold, perf in threshold_performance.items():
            if perf['total'] >= 5:  # minimum de trades
                win_rate = perf['wins'] / perf['total']
                avg_profit = np.mean(perf['profits'])
                
                # Score combiné : win rate + profit moyen
                score = win_rate * 0.6 + (avg_profit / 100) * 0.4
                
                if score > best_score:
                    best_score = score
                    best_threshold = threshold
                    
        # Ajustement graduel vers le seuil optimal
        if best_threshold != self.current_threshold:
            direction = 1 if best_threshold > self.current_threshold else -1
            adjustment = min(self.adjustment_step, 
                           abs(best_threshold - self.current_threshold))
            
            new_threshold = self.current_threshold + (direction * adjustment)
            new_threshold = np.clip(new_threshold, self.min_threshold, self.max_threshold)
            
            print(f"🎯 Ajustement seuil: {self.current_threshold:.3f} → {new_threshold:.3f}")
            self.current_threshold = new_threshold
            
        return self.current_threshold
        
    def get_adaptive_threshold(self, market_context: Dict) -> float:
        """Seuil adaptatif selon contexte marché"""
        base_threshold = self.current_threshold
        
        # Ajustements selon volatilité
        volatility = market_context.get('volatility_regime', 'normal')
        if volatility == 'high':
            return min(base_threshold + 0.05, self.max_threshold)
        elif volatility == 'low':
            return max(base_threshold - 0.03, self.min_threshold)
            
        # Ajustements selon session
        session = market_context.get('session', 'unknown')
        if session in ['london_ny_overlap']:
            return max(base_threshold - 0.02, self.min_threshold)  # Plus permissif
        elif session == 'asian':
            return min(base_threshold + 0.03, self.max_threshold)  # Plus strict
            
        return base_threshold


class MultiTimeframeConfidenceAnalyzer:
    """Analyseur de confiance multi-timeframes"""
    
    def __init__(self):
        self.timeframes = ['M1', 'M5', 'M15', 'H1', 'H4']
        self.weights = {'M1': 0.1, 'M5': 0.2, 'M15': 0.3, 'H1': 0.25, 'H4': 0.15}
        
    def calculate_weighted_confidence(self, signals_by_timeframe: Dict) -> float:
        """Calculer confiance pondérée multi-timeframes"""
        weighted_sum = 0
        total_weight = 0
        
        for tf, weight in self.weights.items():
            if tf in signals_by_timeframe:
                confidence = signals_by_timeframe[tf].get('confidence', 0)
                weighted_sum += confidence * weight
                total_weight += weight
                
        return weighted_sum / total_weight if total_weight > 0 else 0


def test_optimization():
    """Test du système d'optimisation"""
    print("🎯 TEST OPTIMISATION SEUILS DE CONFIANCE")
    print("=" * 50)
    
    optimizer = ConfidenceThresholdOptimizer()
    
    # Simuler quelques trades
    for i in range(30):
        # Trade simulé
        trade_result = {
            'profit': np.random.normal(5, 10),  # Profit/perte
            'success': np.random.random() > 0.4  # 60% win rate simulé
        }
        
        optimizer.update_performance(trade_result)
        
        if i % 10 == 0:
            new_threshold = optimizer.optimize_threshold()
            print(f"Trade {i}: Seuil = {new_threshold:.3f}")
            
    print("\n✅ Test optimisation terminé")


if __name__ == "__main__":
    test_optimization()