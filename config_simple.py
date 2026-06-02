"""
Compatibilité — lecture depuis config YAML + Pydantic
Tous les imports existants `import config_simple as cfg` continuent de fonctionner.
Le module `config/schema.py` est la source de vérité unique.
"""
import logging

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("robot.config")

try:
    from config.schema import hot_reload, load_config

    _env = "production"
    _cfg = load_config(_env)

    # Exposer les credentials depuis secrets
    MT5_LOGIN: int = _cfg.secrets.mt5_login_int
    MT5_PASSWORD: str = _cfg.secrets.mt5_password
    MT5_SERVER: str = _cfg.secrets.mt5_server

    # Exposer tous les paramètres en flat UPPERCASE pour compatibilité
    SYMBOLS: list[str] = _cfg.trading.symbols
    ROBOT_MAGIC: int = _cfg.robot.magic
    MAX_POSITIONS: int = _cfg.trading.max_positions
    MAX_POSITIONS_PER_SYMBOL: int = _cfg.trading.max_positions_per_symbol
    MAX_TRADES_PER_DAY: int = _cfg.trading.max_trades_per_day
    LOT_SIZE: float = _cfg.trading.lot_size
    MIN_TRADE_INTERVAL_SEC: int = _cfg.trading.min_trade_interval_sec
    MIN_SIGNAL_SCORE: float = _cfg.signal.min_score
    MAX_SIGNALS_PER_CYCLE: int = _cfg.trading.max_signals_per_cycle
    MAX_ORDERS_PER_MINUTE: int = _cfg.trading.max_orders_per_minute
    DAILY_PROFIT_LIMIT_PCT: float = _cfg.signal.daily_profit_limit_pct
    RISK_PER_TRADE: float = _cfg.risk.per_trade_pct
    RISK_SHORT_MULT: float = _cfg.risk.short_mult
    MAX_DAILY_LOSS_PCT: float = _cfg.risk.max_daily_loss_pct
    ZONE2_LOSS_PCT: float = _cfg.risk.zone2_loss_pct
    ZONE3_LOSS_PCT: float = _cfg.risk.zone3_loss_pct
    MAX_DD_PCT: float = _cfg.risk.max_dd_pct
    PROFIT_TARGET_PCT: float = _cfg.risk.profit_target_pct
    CONSISTENCY_MAX_PCT: float = _cfg.risk.consistency_max_pct
    MIN_RR_RATIO: float = _cfg.risk.min_rr_ratio
    ATR_MULTIPLIER: float = _cfg.risk.atr_multiplier
    COOLDOWN_MINUTES: int = _cfg.risk.cooldown_minutes
    MIN_TRADING_DAYS: int = _cfg.risk.min_trading_days
    MAX_TRADING_DAYS: int = _cfg.risk.max_trading_days
    MAX_RISK_AMOUNT: float = _cfg.risk.max_risk_amount
    MAX_SPREAD_POINTS: int = _cfg.risk.max_spread_points
    TRADING_START_HOUR: int = _cfg.trading.trading_start_hour
    TRADING_END_HOUR: int = _cfg.trading.trading_end_hour
    RECALIBRATION_FREQUENCY: int = _cfg.risk.recalibration_frequency
    AUTO_PAUSE_LOSSES: int = _cfg.risk.auto_pause_losses
    CYCLE_SECONDS: int = _cfg.robot.cycle_seconds
    SYMBOL_LIMITS: dict[str, dict] = {
        sym: lim.model_dump(exclude_none=True) for sym, lim in _cfg.symbol_limits.items()
    }
    # ML Pipeline config
    ML_EXPERIMENT_TRACKING: bool = _cfg.ml.experiment_tracking
    ML_TRACKING_URI: str = _cfg.ml.tracking_uri
    CONCEPT_DRIFT: dict = _cfg.ml.concept_drift.model_dump()
    RETRAINING: dict = _cfg.ml.retraining.model_dump()
    __version__: str = _cfg.robot.version

except Exception as e:
    logger.critical(f"Erreur chargement config YAML: {e}")
    logger.warning("Fallback: valeurs hardcodees minimales")
    # Fallback minimal — ne devrait jamais arriver en production
    MT5_LOGIN = 0
    MT5_PASSWORD = ""
    MT5_SERVER = ""
    SYMBOLS = ["USDCAD", "GBPUSD", "USDCHF", "EURUSD", "AUDUSD"]
    ROBOT_MAGIC = 999001
    MAX_POSITIONS = 6
    MAX_POSITIONS_PER_SYMBOL = 2
    MAX_TRADES_PER_DAY = 5
    LOT_SIZE = 0.05
    MIN_TRADE_INTERVAL_SEC = 60
    MIN_SIGNAL_SCORE = 0.65
    MAX_SIGNALS_PER_CYCLE = 3
    MAX_ORDERS_PER_MINUTE = 6
    DAILY_PROFIT_LIMIT_PCT = 0.008
    RISK_PER_TRADE = 0.004
    RISK_SHORT_MULT = 1.0
    MAX_DAILY_LOSS_PCT = 0.02
    ZONE2_LOSS_PCT = 0.01
    ZONE3_LOSS_PCT = 0.015
    MAX_DD_PCT = 0.10
    PROFIT_TARGET_PCT = 0.10
    CONSISTENCY_MAX_PCT = 0.30
    MIN_RR_RATIO = 2.0
    ATR_MULTIPLIER = 1.5
    COOLDOWN_MINUTES = 30
    MIN_TRADING_DAYS = 10
    MAX_TRADING_DAYS = 0
    MAX_RISK_AMOUNT = 800.0
    MAX_SPREAD_POINTS = 50
    TRADING_START_HOUR = 0
    TRADING_END_HOUR = 24
    RECALIBRATION_FREQUENCY = 50
    AUTO_PAUSE_LOSSES = 3
    CYCLE_SECONDS = 15
    SYMBOL_LIMITS = {
        "USDCAD": dict(max_lot=0.55, risk_mult=1.0, max_spread_points=50, adx_thresh=20, min_score=0.55),
        "GBPUSD": dict(max_lot=0.55, risk_mult=1.0, max_spread_points=50, adx_thresh=22, min_score=0.55),
        "USDCHF": dict(max_lot=0.55, risk_mult=0.8, max_spread_points=50, adx_thresh=18, min_score=0.55),
        "EURUSD": dict(max_lot=0.35, risk_mult=0.8, max_spread_points=40, adx_thresh=18, min_score=0.65, allow_buys=True, allow_shorts=True, max_daily_trades=2, allow_ranging=False, dl_required=True),
        "AUDUSD": dict(max_lot=0.35, risk_mult=0.8, max_spread_points=40, adx_thresh=18, min_score=0.60, allow_buys=True, allow_shorts=True, max_daily_trades=2, allow_ranging=False, dl_required=True),
    }
    ML_EXPERIMENT_TRACKING = False
    ML_TRACKING_URI = ""
    CONCEPT_DRIFT = dict(
        enabled=True, window_size=100, psi_threshold_light=0.10,
        psi_threshold_moderate=0.20, psi_threshold_severe=0.25,
        auto_retrain=True, retrain_cooldown_hours=24,
    )
    RETRAINING = dict(
        days=90, min_samples=50, epochs=10, n_splits=5,
        schedule_trades=500, log_mlflow=True,
    )
    __version__ = "3.2.0"


def reload_config() -> bool:
    """Hot-reload: recharge la config depuis les fichiers YAML.
    Retourne True si la config a change."""
    global _cfg, _env
    try:
        new = hot_reload(_env)
        if new is None:
            return False
        _cfg = new
        # Re-exposer toutes les variables
        _re_export()
        logger.info("Config rechargée a chaud")
        return True
    except Exception as e:
        logger.error(f"Echec rechargement config: {e}")
        return False


def _re_export():
    global MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOLS, ROBOT_MAGIC
    global MAX_POSITIONS, MAX_POSITIONS_PER_SYMBOL, MAX_TRADES_PER_DAY
    global LOT_SIZE, MIN_TRADE_INTERVAL_SEC, MIN_SIGNAL_SCORE
    global MAX_SIGNALS_PER_CYCLE, MAX_ORDERS_PER_MINUTE, DAILY_PROFIT_LIMIT_PCT
    global RISK_PER_TRADE, RISK_SHORT_MULT, MAX_DAILY_LOSS_PCT
    global ZONE2_LOSS_PCT, ZONE3_LOSS_PCT, MAX_DD_PCT, PROFIT_TARGET_PCT
    global CONSISTENCY_MAX_PCT, MIN_RR_RATIO, ATR_MULTIPLIER, COOLDOWN_MINUTES
    global MIN_TRADING_DAYS, MAX_TRADING_DAYS, MAX_RISK_AMOUNT, MAX_SPREAD_POINTS
    global TRADING_START_HOUR, TRADING_END_HOUR, RECALIBRATION_FREQUENCY
    global AUTO_PAUSE_LOSSES, CYCLE_SECONDS, SYMBOL_LIMITS, __version__
    MT5_LOGIN = _cfg.secrets.mt5_login_int
    MT5_PASSWORD = _cfg.secrets.mt5_password
    MT5_SERVER = _cfg.secrets.mt5_server
    SYMBOLS = _cfg.trading.symbols
    ROBOT_MAGIC = _cfg.robot.magic
    MAX_POSITIONS = _cfg.trading.max_positions
    MAX_POSITIONS_PER_SYMBOL = _cfg.trading.max_positions_per_symbol
    MAX_TRADES_PER_DAY = _cfg.trading.max_trades_per_day
    LOT_SIZE = _cfg.trading.lot_size
    MIN_TRADE_INTERVAL_SEC = _cfg.trading.min_trade_interval_sec
    MIN_SIGNAL_SCORE = _cfg.signal.min_score
    MAX_SIGNALS_PER_CYCLE = _cfg.trading.max_signals_per_cycle
    MAX_ORDERS_PER_MINUTE = _cfg.trading.max_orders_per_minute
    DAILY_PROFIT_LIMIT_PCT = _cfg.signal.daily_profit_limit_pct
    RISK_PER_TRADE = _cfg.risk.per_trade_pct
    RISK_SHORT_MULT = _cfg.risk.short_mult
    MAX_DAILY_LOSS_PCT = _cfg.risk.max_daily_loss_pct
    ZONE2_LOSS_PCT = _cfg.risk.zone2_loss_pct
    ZONE3_LOSS_PCT = _cfg.risk.zone3_loss_pct
    MAX_DD_PCT = _cfg.risk.max_dd_pct
    PROFIT_TARGET_PCT = _cfg.risk.profit_target_pct
    CONSISTENCY_MAX_PCT = _cfg.risk.consistency_max_pct
    MIN_RR_RATIO = _cfg.risk.min_rr_ratio
    ATR_MULTIPLIER = _cfg.risk.atr_multiplier
    COOLDOWN_MINUTES = _cfg.risk.cooldown_minutes
    MIN_TRADING_DAYS = _cfg.risk.min_trading_days
    MAX_TRADING_DAYS = _cfg.risk.max_trading_days
    MAX_RISK_AMOUNT = _cfg.risk.max_risk_amount
    MAX_SPREAD_POINTS = _cfg.risk.max_spread_points
    TRADING_START_HOUR = _cfg.trading.trading_start_hour
    TRADING_END_HOUR = _cfg.trading.trading_end_hour
    RECALIBRATION_FREQUENCY = _cfg.risk.recalibration_frequency
    AUTO_PAUSE_LOSSES = _cfg.risk.auto_pause_losses
    CYCLE_SECONDS = _cfg.robot.cycle_seconds
    SYMBOL_LIMITS = {sym: lim.model_dump() for sym, lim in _cfg.symbol_limits.items()}
    __version__ = _cfg.robot.version
