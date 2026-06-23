"""
Backtest Core — Moteur de backtest institutionnel.
Modules : costs, execution, trade, engine, metrics, ftmo, data_loader,
          strategies (4), robustness (3), reporting, visualization.
"""

from engine_simple.backtest_core.costs import CostModel
from engine_simple.backtest_core.execution import ExecutionEngine, FillResult
from engine_simple.backtest_core.trade import SimTrade
from engine_simple.backtest_core.engine import BacktestEngine, BacktestConfig, BacktestResult
from engine_simple.backtest_core.metrics import MetricsCalculator
from engine_simple.backtest_core.ftmo import (
    FTMOChallengeSimulator,
    FTMOVerdict,
    FTMOConfig,
    FTMOPortfolioSimulator,
)
from engine_simple.backtest_core.data_loader import DataLoader
from engine_simple.backtest_core.reporting import ReportGenerator
from engine_simple.backtest_core.visualization import ChartGenerator

__version__ = "1.0.0"
__all__ = [
    "CostModel",
    "ExecutionEngine",
    "FillResult",
    "SimTrade",
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "MetricsCalculator",
    "FTMOChallengeSimulator",
    "FTMOVerdict",
    "FTMOConfig",
    "FTMOPortfolioSimulator",
    "DataLoader",
    "ReportGenerator",
    "ChartGenerator",
]
