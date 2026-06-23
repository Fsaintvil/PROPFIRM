"""Portfolio Optimization — Optimisation de la allocation du capital.

Calcule l'allocation optimale entre les symboles en fonction de :
- Corrélations historiques
- Volatilité (ATR%)
- Performance (Sharpe, win rate)
- Taille du drawdown

Usage:
    optimizer = PortfolioOptimizer()
    allocation = optimizer.optimize(portfolio_data)
    risk_per_symbol = optimizer.get_risk_allocation(current_dd=0.05)
"""

import logging
import numpy as np
from dataclasses import dataclass, field

logger = logging.getLogger("portfolio_opt")


@dataclass
class Allocation:
    """Allocation pour un symbole."""

    symbol: str
    weight: float = 0.0  # 0-1, proportion du portfolio
    risk_pct: float = 0.0  # % du capital à risquer
    max_positions: int = 1
    confidence: float = 0.0  # 0-1

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "weight": self.weight,
            "risk_pct": self.risk_pct,
            "max_positions": self.max_positions,
            "confidence": self.confidence,
        }


class PortfolioOptimizer:
    """Optimise l'allocation du portfolio."""

    # Default symbol weights (equal allocation) — Juin 2026: purgé EURUSD
    DEFAULT_WEIGHTS = {
        "XAUUSD": 0.30,
        "BTCUSD": 0.30,
        "ETHUSD": 0.20,
        "US500.cash": 0.20,
    }

    # Correlation matrix (approximate) — Juin 2026: purgé EURUSD
    CORRELATIONS = {
        ("XAUUSD", "BTCUSD"): 0.15,
        ("XAUUSD", "ETHUSD"): 0.12,
        ("XAUUSD", "US500.cash"): -0.10,
        ("BTCUSD", "ETHUSD"): 0.89,
        ("BTCUSD", "US500.cash"): 0.30,
        ("ETHUSD", "US500.cash"): 0.25,
    }

    def __init__(self, total_capital: float = 200000):
        self.total_capital = total_capital

    def _get_correlation(self, sym1: str, sym2: str) -> float:
        """Retourne la corrélation entre deux symboles."""
        if sym1 == sym2:
            return 1.0

        key1 = (sym1, sym2)
        key2 = (sym2, sym1)

        return self.CORRELATIONS.get(key1, self.CORRELATIONS.get(key2, 0.0))

    def optimize(self, performance_data: dict[str, dict]) -> dict[str, Allocation]:
        """Optimise l'allocation basée sur les performances.

        Args:
            performance_data: {symbol: {"win_rate": 0.6, "sharpe": 0.8, "atr_pct": 0.5, ...}}

        Returns:
            {symbol: Allocation}
        """
        symbols = list(performance_data.keys())
        n = len(symbols)

        if n == 0:
            return {}

        # Calculate raw scores
        scores = {}
        for sym in symbols:
            data = performance_data[sym]
            wr = data.get("win_rate", 0.5)
            sharpe = data.get("sharpe", 0.5)
            atr_pct = data.get("atr_pct", 0.5)

            # Score: higher WR + Sharpe, lower volatility
            score = wr * 0.4 + min(sharpe / 2, 1.0) * 0.4 + max(0, 1.0 - atr_pct) * 0.2
            scores[sym] = max(score, 0.1)  # Minimum 0.1

        # Adjust for correlations
        adjusted_scores = scores.copy()

        for i, sym1 in enumerate(symbols):
            for sym2 in symbols[i + 1 :]:
                corr = self._get_correlation(sym1, sym2)

                if corr > 0.7:
                    # High correlation → reduce both slightly
                    penalty = (corr - 0.7) * 0.5
                    adjusted_scores[sym1] *= 1 - penalty
                    adjusted_scores[sym2] *= 1 - penalty

        # Normalize to weights
        total_score = sum(adjusted_scores.values())

        allocations = {}
        for sym in symbols:
            weight = adjusted_scores[sym] / total_score

            # Get performance data
            data = performance_data[sym]
            wr = data.get("win_rate", 0.5)
            sharpe = data.get("sharpe", 0.5)

            # Risk percentage
            risk_pct = weight * 0.004  # Base 0.4% * weight

            # Max positions based on weight
            max_pos = max(1, int(weight * 10))

            # Confidence based on sample size
            sample_size = data.get("sample_size", 0)
            confidence = min(sample_size / 100, 1.0)

            allocations[sym] = Allocation(
                symbol=sym,
                weight=weight,
                risk_pct=risk_pct,
                max_positions=min(max_pos, 3),
                confidence=confidence,
            )

        return allocations

    def get_risk_allocation(self, current_dd: float = 0.0, performance_data: dict | None = None) -> dict[str, float]:
        """Retourne le risk multiplier par symbole basé sur le DD actuel.

        Args:
            current_dd: Drawdown actuel (0-1)
            performance_data: Données de performance optionnelles

        Returns:
            {symbol: risk_multiplier}
        """
        symbols = list(self.DEFAULT_WEIGHTS.keys())

        # Base allocation
        if performance_data:
            allocations = self.optimize(performance_data)
            weights = {sym: a.weight for sym, a in allocations.items()}
        else:
            weights = self.DEFAULT_WEIGHTS.copy()

        # DD adjustment
        dd_factor = 1.0
        if current_dd > 0.07:
            dd_factor = 0.2  # Critical DD → reduce risk
        elif current_dd > 0.05:
            dd_factor = 0.5  # High DD → reduce risk
        elif current_dd > 0.03:
            dd_factor = 0.8  # Moderate DD → slight reduction

        # Calculate risk per symbol
        risk_allocation = {}
        for sym in symbols:
            base_weight = weights.get(sym, 0.2)
            risk_mult = base_weight * dd_factor * 2.5  # Scale to ~1.0 for equal weight
            risk_allocation[sym] = min(risk_mult, 1.5)  # Cap at 1.5

        return risk_allocation

    def get_portfolio_metrics(self, allocations: dict[str, Allocation]) -> dict:
        """Calcule les métriques du portfolio optimisé."""
        if not allocations:
            return {}

        total_weight = sum(a.weight for a in allocations.values())
        avg_confidence = np.mean([a.confidence for a in allocations.values()])

        # Portfolio correlation (simplified)
        symbols = list(allocations.keys())
        n = len(symbols)

        if n > 1:
            corr_sum = 0
            count = 0
            for i in range(n):
                for j in range(i + 1, n):
                    corr_sum += self._get_correlation(symbols[i], symbols[j])
                    count += 1
            avg_corr = corr_sum / count if count > 0 else 0
        else:
            avg_corr = 0

        return {
            "total_weight": total_weight,
            "num_symbols": n,
            "avg_confidence": avg_confidence,
            "avg_correlation": avg_corr,
            "diversification_score": max(0, 1 - avg_corr),
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_optimizer = PortfolioOptimizer()


def optimize(performance_data: dict[str, dict]) -> dict[str, Allocation]:
    """Optimise le portfolio (fonction convenience)."""
    return _default_optimizer.optimize(performance_data)


def get_risk_allocation(current_dd: float = 0.0) -> dict[str, float]:
    """Retourne l'allocation de risque (fonction convenience)."""
    return _default_optimizer.get_risk_allocation(current_dd)
