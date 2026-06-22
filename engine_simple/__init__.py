"""Engine Simple — modules de trading MT5 FTMO MOM20x3."""

from engine_simple.adaptive_intelligence import AdaptiveEngine, MarketRegime, OnlineLearner
from engine_simple.audit_trail import AuditTrail
from engine_simple.broker import Broker
from engine_simple.ftmo_protector import FTMOProtector
from engine_simple.market_memory import MarketMemory
from engine_simple.monitoring import HealthServer, MetricsCollector
from engine_simple.mt5_connector import MT5Connector
from engine_simple.notifier import Notifier
from engine_simple.position_tracker import PositionTracker
from engine_simple.regime import RegimeDetector
from engine_simple.risk_manager import RiskManager
from engine_simple.strategy import MOM20x3
from engine_simple.symbol_profile import SymbolInstitutionalProfile
from engine_simple.trade_executor import PerSymbolRateLimiter, TradeExecutor
from engine_simple.trade_journal import TradeJournal

__version__ = "4.1.0"

__all__ = [
    "AdaptiveEngine",
    "AuditTrail",
    "Broker",
    "FTMOProtector",
    "HealthServer",
    "MarketMemory",
    "MarketRegime",
    "MetricsCollector",
    "MOM20x3",
    "MT5Connector",
    "Notifier",
    "OnlineLearner",
    "PerSymbolRateLimiter",
    "PositionTracker",
    "RegimeDetector",
    "RiskManager",
    "SymbolInstitutionalProfile",
    "TradeExecutor",
    "TradeJournal",
]
