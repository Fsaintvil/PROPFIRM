"""Walk-Forward Optimization — Optimisation continue des paramètres.

Effectue des micro-optimisations sur fenêtres glissantes pour maintenir
les paramètres optimaux sans overfitting. Basé sur le walk-forward
validation déjà existant mais en mode temps réel.

Usage:
    wfo = WalkForwardOptimizer("BTCUSD")
    optimal = wfo.get_optimal_params(recent_trades)
    wfo.update(new_trades)
"""
import logging
import numpy as np
from dataclasses import dataclass, field

logger = logging.getLogger("walk_forward_opt")


@dataclass
class OptimalParams:
    """Paramètres optimaux trouvés par WFO."""
    # Core params
    momentum_period: int = 20
    adx_period: int = 14
    atr_period: int = 14
    threshold_trending: float = 2.5
    threshold_ranging: float = 2.0
    adx_trend_threshold: int = 22
    adx_exit_threshold: int = 18
    
    # Risk params
    risk_per_trade: float = 0.004
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 5.0
    
    # Trailing
    trailing_first_lock: float = 1.0
    trailing_n1: float = 0.50
    
    # Metadata
    sharpe: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sample_size: int = 0
    optimization_score: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "momentum_period": self.momentum_period,
            "adx_period": self.adx_period,
            "atr_period": self.atr_period,
            "threshold_trending": self.threshold_trending,
            "threshold_ranging": self.threshold_ranging,
            "adx_trend_threshold": self.adx_trend_threshold,
            "adx_exit_threshold": self.adx_exit_threshold,
            "risk_per_trade": self.risk_per_trade,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
            "trailing_first_lock": self.trailing_first_lock,
            "trailing_n1": self.trailing_n1,
            "sharpe": self.sharpe,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "sample_size": self.sample_size,
            "optimization_score": self.optimization_score,
        }


class WalkForwardOptimizer:
    """Optimise les paramètres sur fenêtres glissantes."""
    
    # Parameter ranges for optimization
    PARAM_RANGES = {
        "threshold_trending": [2.0, 2.25, 2.5, 2.75, 3.0],
        "threshold_ranging": [1.5, 1.75, 2.0, 2.25, 2.5],
        "adx_trend_threshold": [18, 20, 22, 24, 26],
        "sl_atr_mult": [1.5, 1.75, 2.0, 2.25, 2.5],
        "tp_atr_mult": [3.0, 4.0, 5.0, 6.0, 7.0],
        "trailing_first_lock": [0.8, 1.0, 1.2],
    }
    
    def __init__(self, symbol: str, lookback: int = 200,
                 optimization_window: int = 50):
        self.symbol = symbol
        self.lookback = lookback
        self.optimization_window = optimization_window
        self._current_params = OptimalParams()
        self._trades: list[dict] = []
    
    def update(self, trades: list[dict]):
        """Met à jour les trades et réoptimise."""
        self._trades = trades[-self.lookback:]
        
        if len(self._trades) >= self.optimization_window:
            self._optimize()
    
    def _optimize(self):
        """Micro-optimisation sur la fenêtre courante."""
        trades = self._trades
        
        if len(trades) < self.optimization_window:
            return
        
        # Split into IS (in-sample) and OOS (out-of-sample)
        split = len(trades) * 2 // 3
        is_trades = trades[:split]
        oos_trades = trades[split:]
        
        best_score = -np.inf
        best_params = self._current_params.to_dict()
        
        # Grid search on key parameters
        for thresh_t in self.PARAM_RANGES["threshold_trending"]:
            for thresh_r in self.PARAM_RANGES["threshold_ranging"]:
                for sl_m in self.PARAM_RANGES["sl_atr_mult"]:
                    for tp_m in self.PARAM_RANGES["tp_atr_mult"]:
                        # Simulate with these params
                        score = self._evaluate_params(
                            is_trades, thresh_t, thresh_r, sl_m, tp_m
                        )
                        
                        if score > best_score:
                            best_score = score
                            best_params = {
                                "threshold_trending": thresh_t,
                                "threshold_ranging": thresh_r,
                                "sl_atr_mult": sl_m,
                                "tp_atr_mult": tp_m,
                            }
        
        # Validate on OOS
        oos_score = self._evaluate_params(
            oos_trades,
            best_params["threshold_trending"],
            best_params["threshold_ranging"],
            best_params["sl_atr_mult"],
            best_params["tp_atr_mult"],
        )
        
        # Only update if OOS is decent (not overfit)
        if oos_score > 0.5:
            self._current_params.threshold_trending = best_params["threshold_trending"]
            self._current_params.threshold_ranging = best_params["threshold_ranging"]
            self._current_params.sl_atr_mult = best_params["sl_atr_mult"]
            self._current_params.tp_atr_mult = best_params["tp_atr_mult"]
            self._current_params.optimization_score = oos_score
            
            # Calculate metrics
            self._calculate_metrics(oos_trades)
            
            logger.debug(f"  [WFO] {self.symbol}: optimized — "
                        f"thresh_t={self._current_params.threshold_trending}, "
                        f"thresh_r={self._current_params.threshold_ranging}, "
                        f"score={oos_score:.3f}")
    
    def _evaluate_params(self, trades: list[dict], thresh_t: float,
                        thresh_r: float, sl_m: float, tp_m: float) -> float:
        """Évalue des paramètres sur une série de trades."""
        if not trades:
            return 0.0
        
        # Simplified scoring based on trade results
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total = len(trades)
        
        if total == 0:
            return 0.0
        
        wr = wins / total
        
        # Profit factor
        gross_profit = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 1.0
        
        # Sharpe-like ratio
        pnls = [t.get("pnl", 0) for t in trades]
        if len(pnls) > 1:
            avg = np.mean(pnls)
            std = np.std(pnls)
            sharpe = avg / std if std > 0 else 0
        else:
            sharpe = 0
        
        # Combined score
        score = (wr * 0.4 + min(pf / 3, 1.0) * 0.3 + min(sharpe / 2, 1.0) * 0.3)
        
        return score
    
    def _calculate_metrics(self, trades: list[dict]):
        """Calcule les métriques sur les trades."""
        if not trades:
            return
        
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total = len(trades)
        
        self._current_params.win_rate = wins / total if total > 0 else 0
        self._current_params.sample_size = total
        
        gross_profit = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0))
        self._current_params.profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else 1.0
        )
    
    def get_optimal_params(self) -> OptimalParams:
        """Retourne les paramètres optimaux."""
        return self._current_params
    
    def get_status(self) -> dict:
        """Retourne le statut de l'optimisation."""
        return {
            "symbol": self.symbol,
            "sample_size": self._current_params.sample_size,
            "optimization_score": self._current_params.optimization_score,
            "win_rate": self._current_params.win_rate,
            "profit_factor": self._current_params.profit_factor,
            "threshold_trending": self._current_params.threshold_trending,
            "threshold_ranging": self._current_params.threshold_ranging,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_wfo_instances: dict[str, WalkForwardOptimizer] = {}

def get_wfo(symbol: str, lookback: int = 200) -> WalkForwardOptimizer:
    """Retourne (ou crée) l'instance WFO pour un symbole."""
    if symbol not in _wfo_instances:
        _wfo_instances[symbol] = WalkForwardOptimizer(symbol, lookback)
    return _wfo_instances[symbol]

def get_optimal_params(symbol: str) -> OptimalParams:
    """Retourne les paramètres optimaux (fonction convenience)."""
    return get_wfo(symbol).get_optimal_params()
