"""Stratégies de trading pour le backtest institutionnel."""

from engine_simple.backtest_core.strategies.base import Strategy, Signal
from engine_simple.backtest_core.strategies.mom20x3 import MOM20x3
from engine_simple.backtest_core.strategies.trend_following import TrendFollowing
from engine_simple.backtest_core.strategies.breakout import Breakout
from engine_simple.backtest_core.strategies.mean_reversion import MeanReversion

__all__ = [
    "Strategy",
    "Signal",
    "MOM20x3",
    "TrendFollowing",
    "Breakout",
    "MeanReversion",
]
