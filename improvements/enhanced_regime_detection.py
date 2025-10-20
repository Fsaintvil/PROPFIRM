#!/usr/bin/env python3
"""
📈 AMÉLIORATION: DÉTECTION RÉGIMES DE MARCHÉ AVANCÉE
Système amélioré pour identifier et s'adapter aux régimes de marché
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings("ignore")


class MarketRegime(Enum):
    """Types de régimes de marché"""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    BREAKOUT = "breakout"
    CONSOLIDATION = "consolidation"


@dataclass
class RegimeMetrics:
    """Métriques d'un régime de marché"""
    regime: MarketRegime
    confidence: float
    stability: float
    duration: int  # en périodes
    strength: float


class EnhancedRegimeDetector:
    """Détecteur de régimes de marché amélioré"""
    
    def __init__(self):
        self.lookback_periods = {
            'short': 20,    # Court terme
            'medium': 50,   # Moyen terme  
            'long': 100     # Long terme
        }
        
        # Seuils adaptatifs par régime
        self.regime_thresholds = {
            'trend_strength': 0.6,
            'volatility_low': 0.5,
            'volatility_high': 2.0,
            'ranging_ratio': 0.3
        }
        
        self.regime_history = []
        self.adaptation_factor = 0.1
        
    def detect_regime(self, data: pd.DataFrame) -> RegimeMetrics:
        """Détecter régime de marché principal"""
        if len(data) < self.lookback_periods['medium']:
            return RegimeMetrics(
                MarketRegime.RANGING, 0.5, 0.5, 0, 0.5
            )
            
        # Calculer métriques multi-timeframes
        metrics = self._calculate_regime_metrics(data)
        
        # Analyser chaque type de régime
        regime_scores = {}
        
        # 1. Régimes de tendance
        trend_up_score = self._score_trending_up(metrics)
        trend_down_score = self._score_trending_down(metrics)
        
        # 2. Régimes de volatilité
        high_vol_score = self._score_high_volatility(metrics)
        low_vol_score = self._score_low_volatility(metrics)
        
        # 3. Régimes de range/consolidation
        ranging_score = self._score_ranging(metrics)
        consolidation_score = self._score_consolidation(metrics)
        
        # 4. Régime de breakout
        breakout_score = self._score_breakout(metrics)
        
        regime_scores = {
            MarketRegime.TRENDING_UP: trend_up_score,
            MarketRegime.TRENDING_DOWN: trend_down_score,
            MarketRegime.HIGH_VOLATILITY: high_vol_score,
            MarketRegime.LOW_VOLATILITY: low_vol_score,
            MarketRegime.RANGING: ranging_score,
            MarketRegime.CONSOLIDATION: consolidation_score,
            MarketRegime.BREAKOUT: breakout_score
        }
        
        # Sélectionner régime dominant
        dominant_regime = max(regime_scores.items(), key=lambda x: x[1])
        
        # Calculer stabilité et durée
        stability = self._calculate_stability(dominant_regime[0])
        duration = self._calculate_duration(dominant_regime[0])
        
        regime_metrics = RegimeMetrics(
            regime=dominant_regime[0],
            confidence=dominant_regime[1],
            stability=stability,
            duration=duration,
            strength=self._calculate_regime_strength(metrics, dominant_regime[0])
        )
        
        # Mettre à jour historique
        self._update_regime_history(regime_metrics)
        
        return regime_metrics
        
    def _calculate_regime_metrics(self, data: pd.DataFrame) -> Dict[str, float]:
        """Calculer métriques pour détection de régime"""
        metrics = {}
        
        # Prix et retours
        prices = data['close']
        returns = prices.pct_change().dropna()
        
        # 1. Métriques de tendance
        for period_name, period in self.lookback_periods.items():
            if len(prices) >= period:
                # Pente de régression linéaire
                x = np.arange(period)
                y = prices.iloc[-period:].values
                slope = np.polyfit(x, y, 1)[0]
                metrics[f'slope_{period_name}'] = slope / prices.iloc[-1]
                
                # Corrélation avec temps (force de tendance)
                correlation = np.corrcoef(x, y)[0, 1]
                metrics[f'trend_strength_{period_name}'] = abs(correlation)
                
        # 2. Métriques de volatilité
        for period_name, period in self.lookback_periods.items():
            if len(returns) >= period:
                vol = returns.iloc[-period:].std()
                metrics[f'volatility_{period_name}'] = vol
                
        # 3. Métriques de range
        for period_name, period in self.lookback_periods.items():
            if len(data) >= period:
                high = data['high'].iloc[-period:].max()
                low = data['low'].iloc[-period:].min()
                current = data['close'].iloc[-1]
                
                # Position dans le range
                range_position = (current - low) / (high - low) if high > low else 0.5
                metrics[f'range_position_{period_name}'] = range_position
                
                # Taille du range relative
                avg_price = data['close'].iloc[-period:].mean()
                range_size = (high - low) / avg_price if avg_price > 0 else 0
                metrics[f'range_size_{period_name}'] = range_size
                
        # 4. Métriques de momentum
        for period_name, period in self.lookback_periods.items():
            if len(returns) >= period:
                momentum = returns.iloc[-period:].sum()
                metrics[f'momentum_{period_name}'] = momentum
                
        # 5. Breakout metrics
        if len(data) >= 20:
            # Volume spike (si disponible)
            if 'volume' in data.columns:
                recent_vol = data['volume'].iloc[-5:].mean()
                avg_vol = data['volume'].iloc[-20:].mean()
                vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
                metrics['volume_spike'] = vol_ratio
            else:
                metrics['volume_spike'] = 1.0
                
            # Price breakout
            resistance = data['high'].iloc[-20:-1].max()
            support = data['low'].iloc[-20:-1].min()
            current_price = data['close'].iloc[-1]
            
            if current_price > resistance:
                metrics['breakout_strength'] = (current_price - resistance) / resistance
            elif current_price < support:
                metrics['breakout_strength'] = (support - current_price) / support
            else:
                metrics['breakout_strength'] = 0
                
        return metrics
        
    def _score_trending_up(self, metrics: Dict[str, float]) -> float:
        """Score pour régime trending up"""
        score = 0
        
        # Pentes positives
        slopes = [v for k, v in metrics.items() if 'slope_' in k]
        positive_slopes = sum(1 for s in slopes if s > 0)
        score += (positive_slopes / len(slopes)) * 0.4 if slopes else 0
        
        # Force de tendance
        trend_strengths = [v for k, v in metrics.items() if 'trend_strength_' in k]
        avg_trend_strength = np.mean(trend_strengths) if trend_strengths else 0
        score += avg_trend_strength * 0.3
        
        # Momentum positif
        momentums = [v for k, v in metrics.items() if 'momentum_' in k]
        positive_momentum = sum(1 for m in momentums if m > 0)
        score += (positive_momentum / len(momentums)) * 0.3 if momentums else 0
        
        return min(score, 1.0)
        
    def _score_trending_down(self, metrics: Dict[str, float]) -> float:
        """Score pour régime trending down"""
        score = 0
        
        # Pentes négatives
        slopes = [v for k, v in metrics.items() if 'slope_' in k]
        negative_slopes = sum(1 for s in slopes if s < 0)
        score += (negative_slopes / len(slopes)) * 0.4 if slopes else 0
        
        # Force de tendance
        trend_strengths = [v for k, v in metrics.items() if 'trend_strength_' in k]
        avg_trend_strength = np.mean(trend_strengths) if trend_strengths else 0
        score += avg_trend_strength * 0.3
        
        # Momentum négatif
        momentums = [v for k, v in metrics.items() if 'momentum_' in k]
        negative_momentum = sum(1 for m in momentums if m < 0)
        score += (negative_momentum / len(momentums)) * 0.3 if momentums else 0
        
        return min(score, 1.0)
        
    def _score_high_volatility(self, metrics: Dict[str, float]) -> float:
        """Score pour régime high volatility"""
        volatilities = [v for k, v in metrics.items() if 'volatility_' in k]
        if not volatilities:
            return 0
            
        avg_vol = np.mean(volatilities)
        # Seuil adaptatif basé sur historique
        vol_threshold = self.regime_thresholds.get('volatility_high', 0.02)
        
        return min(avg_vol / vol_threshold, 1.0)
        
    def _score_low_volatility(self, metrics: Dict[str, float]) -> float:
        """Score pour régime low volatility"""
        volatilities = [v for k, v in metrics.items() if 'volatility_' in k]
        if not volatilities:
            return 0
            
        avg_vol = np.mean(volatilities)
        vol_threshold = self.regime_thresholds.get('volatility_low', 0.005)
        
        return max(0, 1.0 - (avg_vol / vol_threshold))
        
    def _score_ranging(self, metrics: Dict[str, float]) -> float:
        """Score pour régime ranging"""
        score = 0
        
        # Faible force de tendance
        trend_strengths = [v for k, v in metrics.items() if 'trend_strength_' in k]
        if trend_strengths:
            weak_trend_score = 1.0 - np.mean(trend_strengths)
            score += weak_trend_score * 0.5
            
        # Position centrale dans le range
        range_positions = [v for k, v in metrics.items() if 'range_position_' in k]
        if range_positions:
            # Plus proche de 0.5 = mieux pour ranging
            centrality = 1.0 - abs(np.mean(range_positions) - 0.5) * 2
            score += centrality * 0.5
            
        return min(score, 1.0)
        
    def _score_consolidation(self, metrics: Dict[str, float]) -> float:
        """Score pour régime consolidation"""
        score = 0
        
        # Faible taille de range
        range_sizes = [v for k, v in metrics.items() if 'range_size_' in k]
        if range_sizes:
            small_range_score = max(0, 1.0 - np.mean(range_sizes) * 10)
            score += small_range_score * 0.6
            
        # Faible volatilité
        volatilities = [v for k, v in metrics.items() if 'volatility_' in k]
        if volatilities:
            low_vol_score = max(0, 1.0 - np.mean(volatilities) * 50)
            score += low_vol_score * 0.4
            
        return min(score, 1.0)
        
    def _score_breakout(self, metrics: Dict[str, float]) -> float:
        """Score pour régime breakout"""
        score = 0
        
        # Force du breakout
        breakout_strength = metrics.get('breakout_strength', 0)
        score += min(abs(breakout_strength) * 10, 0.6)
        
        # Spike de volume
        volume_spike = metrics.get('volume_spike', 1.0)
        if volume_spike > 1.5:
            score += min((volume_spike - 1.0) * 0.5, 0.4)
            
        return min(score, 1.0)
        
    def _calculate_stability(self, regime: MarketRegime) -> float:
        """Calculer stabilité du régime"""
        if len(self.regime_history) < 5:
            return 0.5
            
        # Compter changements récents
        recent_regimes = [r.regime for r in self.regime_history[-10:]]
        same_regime_count = sum(1 for r in recent_regimes if r == regime)
        
        return same_regime_count / len(recent_regimes)
        
    def _calculate_duration(self, regime: MarketRegime) -> int:
        """Calculer durée du régime actuel"""
        if not self.regime_history:
            return 1
            
        duration = 1
        for r in reversed(self.regime_history):
            if r.regime == regime:
                duration += 1
            else:
                break
                
        return duration
        
    def _calculate_regime_strength(
        self, 
        metrics: Dict[str, float], 
        regime: MarketRegime
    ) -> float:
        """Calculer force du régime"""
        # Logique simplifiée - à affiner selon le régime
        if regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]:
            trend_strengths = [v for k, v in metrics.items() if 'trend_strength_' in k]
            return np.mean(trend_strengths) if trend_strengths else 0.5
        elif regime == MarketRegime.HIGH_VOLATILITY:
            volatilities = [v for k, v in metrics.items() if 'volatility_' in k]
            return min(np.mean(volatilities) * 50, 1.0) if volatilities else 0.5
        else:
            return 0.5
            
    def _update_regime_history(self, regime_metrics: RegimeMetrics):
        """Mettre à jour historique des régimes"""
        self.regime_history.append(regime_metrics)
        
        # Garder seulement les 100 derniers
        if len(self.regime_history) > 100:
            self.regime_history.pop(0)
            
    def get_regime_trading_strategy(self, regime: MarketRegime) -> Dict[str, Any]:
        """Obtenir stratégie de trading adaptée au régime"""
        strategies = {
            MarketRegime.TRENDING_UP: {
                'preferred_action': 'buy',
                'confidence_adjustment': 1.1,
                'risk_adjustment': 0.9,
                'timeframe_preference': ['H1', 'H4']
            },
            MarketRegime.TRENDING_DOWN: {
                'preferred_action': 'sell',
                'confidence_adjustment': 1.1,
                'risk_adjustment': 0.9,
                'timeframe_preference': ['H1', 'H4']
            },
            MarketRegime.RANGING: {
                'preferred_action': 'contrarian',
                'confidence_adjustment': 0.9,
                'risk_adjustment': 1.1,
                'timeframe_preference': ['M15', 'H1']
            },
            MarketRegime.HIGH_VOLATILITY: {
                'preferred_action': 'cautious',
                'confidence_adjustment': 0.8,
                'risk_adjustment': 1.3,
                'timeframe_preference': ['M5', 'M15']
            },
            MarketRegime.LOW_VOLATILITY: {
                'preferred_action': 'patient',
                'confidence_adjustment': 1.0,
                'risk_adjustment': 0.8,
                'timeframe_preference': ['H1', 'H4']
            },
            MarketRegime.BREAKOUT: {
                'preferred_action': 'momentum',
                'confidence_adjustment': 1.2,
                'risk_adjustment': 1.0,
                'timeframe_preference': ['M15', 'H1']
            },
            MarketRegime.CONSOLIDATION: {
                'preferred_action': 'wait',
                'confidence_adjustment': 0.7,
                'risk_adjustment': 1.2,
                'timeframe_preference': ['H1', 'H4']
            }
        }
        
        return strategies.get(regime, {
            'preferred_action': 'neutral',
            'confidence_adjustment': 1.0,
            'risk_adjustment': 1.0,
            'timeframe_preference': ['H1']
        })


def test_regime_detection():
    """Test du détecteur de régimes amélioré"""
    print("📈 TEST DÉTECTION RÉGIMES AMÉLIORÉE")
    print("=" * 50)
    
    detector = EnhancedRegimeDetector()
    
    # Créer données de test - tendance haussière
    np.random.seed(42)
    dates = pd.date_range(start='2025-01-01', periods=100, freq='H')
    trend = np.linspace(1.1000, 1.1200, 100)  # Tendance haussière
    noise = np.random.normal(0, 0.001, 100)
    prices = trend + noise
    
    test_data = pd.DataFrame({
        'timestamp': dates,
        'open': prices,
        'high': prices + 0.0005,
        'low': prices - 0.0005,
        'close': prices,
        'volume': np.random.randint(1000, 5000, 100)
    })
    
    # Détecter régime
    regime_metrics = detector.detect_regime(test_data)
    
    print(f"🎯 RÉGIME DÉTECTÉ:")
    print(f"   Type: {regime_metrics.regime.value}")
    print(f"   Confiance: {regime_metrics.confidence:.3f}")
    print(f"   Stabilité: {regime_metrics.stability:.3f}")
    print(f"   Durée: {regime_metrics.duration} périodes")
    print(f"   Force: {regime_metrics.strength:.3f}")
    
    # Obtenir stratégie associée
    strategy = detector.get_regime_trading_strategy(regime_metrics.regime)
    print(f"\n📊 STRATÉGIE RECOMMANDÉE:")
    print(f"   Action préférée: {strategy['preferred_action']}")
    print(f"   Ajustement confiance: {strategy['confidence_adjustment']:.2f}")
    print(f"   Ajustement risque: {strategy['risk_adjustment']:.2f}")
    print(f"   Timeframes: {strategy['timeframe_preference']}")
    
    print("\n✅ Test détection régimes terminé")


if __name__ == "__main__":
    test_regime_detection()