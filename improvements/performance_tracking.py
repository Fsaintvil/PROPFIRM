#!/usr/bin/env python3
"""
📊 AMÉLIORATION: SYSTÈME PERFORMANCE TRACKING
Auto-amélioration continue basée sur les résultats de trading
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any
from dataclasses import dataclass, asdict
import os


@dataclass
class TradePerformance:
    """Performance d'un trade individuel"""
    timestamp: str
    symbol: str
    action: str
    confidence: float
    regime: str
    profit: float
    success: bool
    duration_minutes: int
    market_conditions: Dict[str, Any]


@dataclass
class PerformanceMetrics:
    """Métriques de performance globales"""
    total_trades: int
    win_rate: float
    avg_profit: float
    sharpe_ratio: float
    max_drawdown: float
    confidence_accuracy: float
    regime_accuracy: Dict[str, float]


class PerformanceTracker:
    """Système de suivi de performance avancé"""
    
    def __init__(self, data_file: str = "performance_tracking.json"):
        self.data_file = data_file
        self.trades = []
        self.load_data()
        
        # Paramètres d'analyse
        self.analysis_window = 100  # Trades pour analyse
        self.confidence_bins = [0.6, 0.7, 0.8, 0.9, 1.0]
        
    def load_data(self):
        """Charger données existantes"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    self.trades = [TradePerformance(**trade) for trade in data]
                print(f"✅ {len(self.trades)} trades chargés")
            except Exception as e:
                print(f"⚠️ Erreur chargement: {e}")
                self.trades = []
        else:
            self.trades = []
            
    def save_data(self):
        """Sauvegarder données"""
        try:
            data = [asdict(trade) for trade in self.trades]
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")
            
    def record_trade(self, trade: TradePerformance):
        """Enregistrer un nouveau trade"""
        self.trades.append(trade)
        
        # Garder seulement les trades récents
        if len(self.trades) > 1000:
            self.trades = self.trades[-800:]  # Garder 800 plus récents
            
        self.save_data()
        
    def calculate_metrics(self, window: int = None) -> PerformanceMetrics:
        """Calculer métriques de performance"""
        if not self.trades:
            return PerformanceMetrics(0, 0, 0, 0, 0, 0, {})
            
        # Utiliser fenêtre d'analyse
        recent_trades = self.trades[-window:] if window else self.trades
        
        if not recent_trades:
            return PerformanceMetrics(0, 0, 0, 0, 0, 0, {})
            
        # Métriques de base
        total_trades = len(recent_trades)
        wins = sum(1 for t in recent_trades if t.success)
        win_rate = wins / total_trades
        
        profits = [t.profit for t in recent_trades]
        avg_profit = np.mean(profits)
        
        # Sharpe ratio (simplifié)
        returns = np.array(profits)
        sharpe = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0
        
        # Max drawdown
        cumulative = np.cumsum(profits)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_drawdown = np.max(drawdowns) if len(drawdowns) > 0 else 0
        
        # Précision confiance
        confidence_accuracy = self._calculate_confidence_accuracy(recent_trades)
        
        # Précision par régime
        regime_accuracy = self._calculate_regime_accuracy(recent_trades)
        
        return PerformanceMetrics(
            total_trades=total_trades,
            win_rate=win_rate,
            avg_profit=avg_profit,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            confidence_accuracy=confidence_accuracy,
            regime_accuracy=regime_accuracy
        )
        
    def _calculate_confidence_accuracy(self, trades: List[TradePerformance]) -> float:
        """Calculer précision de la confiance"""
        if not trades:
            return 0
            
        # Grouper par bins de confiance
        accuracy_by_bin = {}
        
        for trade in trades:
            # Trouver bin de confiance
            bin_idx = np.digitize(trade.confidence, self.confidence_bins) - 1
            bin_idx = max(0, min(bin_idx, len(self.confidence_bins) - 2))
            
            bin_key = f"{self.confidence_bins[bin_idx]:.1f}-{self.confidence_bins[bin_idx+1]:.1f}"
            
            if bin_key not in accuracy_by_bin:
                accuracy_by_bin[bin_key] = {'total': 0, 'correct': 0}
                
            accuracy_by_bin[bin_key]['total'] += 1
            if trade.success:
                accuracy_by_bin[bin_key]['correct'] += 1
                
        # Calculer précision globale pondérée
        total_weighted = 0
        correct_weighted = 0
        
        for bin_key, data in accuracy_by_bin.items():
            weight = data['total']
            accuracy = data['correct'] / data['total']
            total_weighted += weight
            correct_weighted += weight * accuracy
            
        return correct_weighted / total_weighted if total_weighted > 0 else 0
        
    def _calculate_regime_accuracy(self, trades: List[TradePerformance]) -> Dict[str, float]:
        """Calculer précision par régime de marché"""
        regime_stats = {}
        
        for trade in trades:
            regime = trade.regime
            if regime not in regime_stats:
                regime_stats[regime] = {'total': 0, 'wins': 0}
                
            regime_stats[regime]['total'] += 1
            if trade.success:
                regime_stats[regime]['wins'] += 1
                
        # Calculer précision par régime
        regime_accuracy = {}
        for regime, stats in regime_stats.items():
            if stats['total'] > 0:
                regime_accuracy[regime] = stats['wins'] / stats['total']
                
        return regime_accuracy
        
    def analyze_patterns(self) -> Dict[str, Any]:
        """Analyser patterns de performance"""
        if len(self.trades) < 20:
            return {'status': 'insufficient_data'}
            
        recent_trades = self.trades[-self.analysis_window:]
        
        analysis = {
            'confidence_performance': self._analyze_confidence_patterns(recent_trades),
            'regime_performance': self._analyze_regime_patterns(recent_trades),
            'symbol_performance': self._analyze_symbol_patterns(recent_trades),
            'time_patterns': self._analyze_time_patterns(recent_trades),
            'recommendations': []
        }
        
        # Générer recommandations
        analysis['recommendations'] = self._generate_recommendations(analysis)
        
        return analysis
        
    def _analyze_confidence_patterns(self, trades: List[TradePerformance]) -> Dict[str, Any]:
        """Analyser patterns de confiance"""
        confidence_performance = {}
        
        for trade in trades:
            conf_bin = round(trade.confidence, 1)
            if conf_bin not in confidence_performance:
                confidence_performance[conf_bin] = {'trades': [], 'profits': []}
                
            confidence_performance[conf_bin]['trades'].append(trade.success)
            confidence_performance[conf_bin]['profits'].append(trade.profit)
            
        # Calculer statistiques par niveau de confiance
        stats = {}
        for conf, data in confidence_performance.items():
            if len(data['trades']) >= 3:  # Minimum pour statistiques
                stats[conf] = {
                    'win_rate': np.mean(data['trades']),
                    'avg_profit': np.mean(data['profits']),
                    'count': len(data['trades'])
                }
                
        return stats
        
    def _analyze_regime_patterns(self, trades: List[TradePerformance]) -> Dict[str, Any]:
        """Analyser patterns par régime"""
        regime_performance = {}
        
        for trade in trades:
            regime = trade.regime
            if regime not in regime_performance:
                regime_performance[regime] = {'trades': [], 'profits': []}
                
            regime_performance[regime]['trades'].append(trade.success)
            regime_performance[regime]['profits'].append(trade.profit)
            
        # Calculer statistiques par régime
        stats = {}
        for regime, data in regime_performance.items():
            if len(data['trades']) >= 3:
                stats[regime] = {
                    'win_rate': np.mean(data['trades']),
                    'avg_profit': np.mean(data['profits']),
                    'count': len(data['trades'])
                }
                
        return stats
        
    def _analyze_symbol_patterns(self, trades: List[TradePerformance]) -> Dict[str, Any]:
        """Analyser patterns par symbole"""
        symbol_performance = {}
        
        for trade in trades:
            symbol = trade.symbol
            if symbol not in symbol_performance:
                symbol_performance[symbol] = {'trades': [], 'profits': []}
                
            symbol_performance[symbol]['trades'].append(trade.success)
            symbol_performance[symbol]['profits'].append(trade.profit)
            
        # Calculer statistiques par symbole
        stats = {}
        for symbol, data in symbol_performance.items():
            if len(data['trades']) >= 3:
                stats[symbol] = {
                    'win_rate': np.mean(data['trades']),
                    'avg_profit': np.mean(data['profits']),
                    'count': len(data['trades'])
                }
                
        return stats
        
    def _analyze_time_patterns(self, trades: List[TradePerformance]) -> Dict[str, Any]:
        """Analyser patterns temporels"""
        hourly_performance = {}
        
        for trade in trades:
            try:
                timestamp = datetime.fromisoformat(trade.timestamp.replace('Z', '+00:00'))
                hour = timestamp.hour
                
                if hour not in hourly_performance:
                    hourly_performance[hour] = {'trades': [], 'profits': []}
                    
                hourly_performance[hour]['trades'].append(trade.success)
                hourly_performance[hour]['profits'].append(trade.profit)
                
            except Exception:
                continue
                
        # Calculer statistiques par heure
        stats = {}
        for hour, data in hourly_performance.items():
            if len(data['trades']) >= 2:
                stats[hour] = {
                    'win_rate': np.mean(data['trades']),
                    'avg_profit': np.mean(data['profits']),
                    'count': len(data['trades'])
                }
                
        return stats
        
    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Générer recommandations d'amélioration"""
        recommendations = []
        
        # Analyse confiance
        conf_perf = analysis.get('confidence_performance', {})
        if conf_perf:
            # Trouver seuil optimal
            best_conf = max(conf_perf.items(), 
                          key=lambda x: x[1]['win_rate'] * x[1]['avg_profit'])
            recommendations.append(
                f"Seuil confiance optimal: {best_conf[0]} "
                f"(win rate: {best_conf[1]['win_rate']:.2%})"
            )
            
        # Analyse régimes
        regime_perf = analysis.get('regime_performance', {})
        if regime_perf:
            best_regime = max(regime_perf.items(), 
                            key=lambda x: x[1]['win_rate'])
            worst_regime = min(regime_perf.items(), 
                             key=lambda x: x[1]['win_rate'])
            
            recommendations.append(
                f"Meilleur régime: {best_regime[0]} "
                f"({best_regime[1]['win_rate']:.2%})"
            )
            recommendations.append(
                f"Éviter régime: {worst_regime[0]} "
                f"({worst_regime[1]['win_rate']:.2%})"
            )
            
        # Analyse symboles
        symbol_perf = analysis.get('symbol_performance', {})
        if symbol_perf:
            best_symbol = max(symbol_perf.items(), 
                            key=lambda x: x[1]['avg_profit'])
            recommendations.append(
                f"Symbole le plus profitable: {best_symbol[0]} "
                f"({best_symbol[1]['avg_profit']:.2f} avg)"
            )
            
        return recommendations
        
    def get_adaptive_parameters(self) -> Dict[str, Any]:
        """Obtenir paramètres adaptatifs basés sur performance"""
        if len(self.trades) < 50:
            return {'status': 'insufficient_data'}
            
        analysis = self.analyze_patterns()
        
        # Seuil de confiance adaptatif
        conf_perf = analysis.get('confidence_performance', {})
        optimal_confidence = 0.68  # valeur par défaut
        
        if conf_perf:
            # Trouver seuil avec meilleur ratio risk/reward
            best_score = -1
            for conf, stats in conf_perf.items():
                if stats['count'] >= 5:  # Minimum pour fiabilité
                    score = stats['win_rate'] * stats['avg_profit']
                    if score > best_score:
                        best_score = score
                        optimal_confidence = conf
                        
        # Ajustements par régime
        regime_adjustments = {}
        regime_perf = analysis.get('regime_performance', {})
        
        for regime, stats in regime_perf.items():
            if stats['count'] >= 3:
                if stats['win_rate'] > 0.6:
                    regime_adjustments[regime] = {'confidence_boost': 0.05}
                elif stats['win_rate'] < 0.4:
                    regime_adjustments[regime] = {'confidence_penalty': 0.1}
                    
        return {
            'optimal_confidence_threshold': optimal_confidence,
            'regime_adjustments': regime_adjustments,
            'recommendations': analysis.get('recommendations', [])
        }


def test_performance_tracking():
    """Test du système de performance tracking"""
    print("📊 TEST PERFORMANCE TRACKING")
    print("=" * 50)
    
    tracker = PerformanceTracker("test_performance.json")
    
    # Simuler quelques trades
    test_trades = [
        TradePerformance(
            timestamp=datetime.now().isoformat(),
            symbol="EURUSD",
            action="buy",
            confidence=0.75,
            regime="trending_up",
            profit=15.5,
            success=True,
            duration_minutes=45,
            market_conditions={"volatility": "normal"}
        ),
        TradePerformance(
            timestamp=datetime.now().isoformat(),
            symbol="XAUUSD",
            action="sell",
            confidence=0.82,
            regime="trending_down",
            profit=-8.2,
            success=False,
            duration_minutes=32,
            market_conditions={"volatility": "high"}
        )
    ]
    
    for trade in test_trades:
        tracker.record_trade(trade)
        
    # Calculer métriques
    metrics = tracker.calculate_metrics()
    print(f"📊 MÉTRIQUES:")
    print(f"   Total trades: {metrics.total_trades}")
    print(f"   Win rate: {metrics.win_rate:.2%}")
    print(f"   Profit moyen: {metrics.avg_profit:.2f}")
    print(f"   Sharpe ratio: {metrics.sharpe_ratio:.2f}")
    
    # Analyser patterns
    analysis = tracker.analyze_patterns()
    print(f"\n🎯 RECOMMANDATIONS:")
    for rec in analysis.get('recommendations', []):
        print(f"   • {rec}")
        
    # Paramètres adaptatifs
    adaptive = tracker.get_adaptive_parameters()
    if 'optimal_confidence_threshold' in adaptive:
        print(f"\n⚙️ PARAMÈTRES ADAPTATIFS:")
        print(f"   Seuil optimal: {adaptive['optimal_confidence_threshold']}")
        
    print("\n✅ Test performance tracking terminé")
    
    # Nettoyer fichier test
    if os.path.exists("test_performance.json"):
        os.remove("test_performance.json")


if __name__ == "__main__":
    test_performance_tracking()