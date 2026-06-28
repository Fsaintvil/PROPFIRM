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
    BATCH_INTERVAL_SEC: int = _cfg.trading.batch_interval_sec
    HISTORY_LOOKBACK_DAYS: int = _cfg.trading.history_lookback_days
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
    DANGER_HOURS: list[int] = _cfg.trading.danger_hours
    RECALIBRATION_FREQUENCY: int = _cfg.risk.recalibration_frequency
    AUTO_PAUSE_LOSSES: int = _cfg.risk.auto_pause_losses
    MAX_CORRELATED_EXPOSURE: float = _cfg.risk.max_correlated_exposure
    CIRCUIT_BREAKER_DD_PCT: float = _cfg.risk.circuit_breaker_dd_pct
    CYCLE_SECONDS: int = _cfg.robot.cycle_seconds
    SYMBOL_LIMITS: dict[str, dict] = {sym: lim.model_dump(exclude_none=True) for sym, lim in _cfg.symbol_limits.items()}
    SYMBOL_TIMEFRAMES: dict[str, str] = {sym: limits.get("timeframe", "H1") for sym, limits in SYMBOL_LIMITS.items()}
    # ML Pipeline config
    ML_EXPERIMENT_TRACKING: bool = _cfg.ml.experiment_tracking
    ML_TRACKING_URI: str = _cfg.ml.tracking_uri
    CONCEPT_DRIFT: dict = _cfg.ml.concept_drift.model_dump()
    RETRAINING: dict = _cfg.ml.retraining.model_dump()
    __version__: str = _cfg.robot.version
    NEWS_MINUTES_BEFORE: int = _cfg.news.minutes_before
    NEWS_MINUTES_AFTER: int = _cfg.news.minutes_after

    # ── Validation startup : détecter les dérives de configuration ──
    _expected_ranges = {
        "RISK_PER_TRADE": (0.001, 0.01, "risque par trade anormal"),
        "MAX_DAILY_LOSS_PCT": (0.005, 0.05, "daily loss max hors plage FTMO"),
        "MAX_DD_PCT": (0.03, 0.15, "drawdown max hors plage FTMO"),
        "MIN_SIGNAL_SCORE": (0.20, 0.85, "signal score min anormal"),  # Mode MAX: 0.30
        "MIN_RR_RATIO": (0.8, 5.0, "RR ratio min anormal"),  # Mode MAX: 1.0
        "COOLDOWN_MINUTES": (1, 120, "cooldown anormal"),  # Mode MAX: 5 min
    }
    for _var, (_min, _max, _msg) in _expected_ranges.items():
        _val = globals().get(_var)  # H-06: globals() seulement (locals() == globals() au module scope)
        if _val is not None and not (_min <= _val <= _max):
            logger.warning(f"[CONFIG] {_var}={_val} ({_msg}) — attendu entre {_min} et {_max}")

except Exception as e:
    logger.critical(f"Erreur chargement config YAML: {e}")
    logger.warning("Fallback: valeurs hardcodees minimales — ⚠️ RISQUE les valeurs YAML sont perdues")
    # C-04: Logger chaque valeur de fallback pour traçabilité
    _fb_log = lambda name, val: logger.warning(f"  [FALLBACK] {name} = {val}")
    # Fallback minimal — ne devrait jamais arriver en production
    MT5_LOGIN = 0
    _fb_log("MT5_LOGIN", 0)
    MT5_PASSWORD = ""
    _fb_log("MT5_PASSWORD", "(masqué)")
    MT5_SERVER = ""
    _fb_log("MT5_SERVER", "(vide)")
    SYMBOLS = [
        "XAUUSD",
        "BTCUSD",
        "US30.cash",  # AJOUTÉ 28 Juin 2026 — remplace EURUSD (Supreme Council)
        "USDJPY",  # RÉACTIVÉ 24 Juin 2026 — surveillance active
        # EURUSD désactivé 28 Juin 2026 (PF 0.75 après coûts, 6.7% WR live)
        # GBPUSD désactivé 26 Juin 2026 (WR 0.0% Phase 3 — toxique)
        # USDCAD désactivé 26 Juin 2026 (WR 33.3% Phase 3 — toxique)
    ]
    _fb_log("SYMBOLS", SYMBOLS)
    ROBOT_MAGIC = 999001
    _fb_log("ROBOT_MAGIC", 999001)
    MAX_POSITIONS = 20
    _fb_log("MAX_POSITIONS", 20)
    MAX_POSITIONS_PER_SYMBOL = 4
    _fb_log("MAX_POSITIONS_PER_SYMBOL", 4)
    MAX_TRADES_PER_DAY = 30
    _fb_log("MAX_TRADES_PER_DAY", 30)
    LOT_SIZE = 0.06
    _fb_log("LOT_SIZE", 0.06)
    MIN_TRADE_INTERVAL_SEC = 300
    _fb_log("MIN_TRADE_INTERVAL_SEC", 300)
    BATCH_INTERVAL_SEC = 1
    _fb_log("BATCH_INTERVAL_SEC", 1)
    MIN_SIGNAL_SCORE = 0.50
    _fb_log("MIN_SIGNAL_SCORE", 0.50)
    MAX_SIGNALS_PER_CYCLE = 10
    _fb_log("MAX_SIGNALS_PER_CYCLE", 10)
    MAX_ORDERS_PER_MINUTE = 6
    _fb_log("MAX_ORDERS_PER_MINUTE", 6)
    DAILY_PROFIT_LIMIT_PCT = 0.008
    _fb_log("DAILY_PROFIT_LIMIT_PCT", 0.008)
    RISK_PER_TRADE = 0.004
    _fb_log("RISK_PER_TRADE", 0.004)
    RISK_SHORT_MULT = 1.0
    _fb_log("RISK_SHORT_MULT", 1.0)
    MAX_DAILY_LOSS_PCT = 0.02
    _fb_log("MAX_DAILY_LOSS_PCT", 0.02)
    ZONE2_LOSS_PCT = 0.012
    _fb_log("ZONE2_LOSS_PCT", 0.012)
    ZONE3_LOSS_PCT = 0.017
    _fb_log("ZONE3_LOSS_PCT", 0.017)
    MAX_DD_PCT = 0.10
    _fb_log("MAX_DD_PCT", 0.10)
    PROFIT_TARGET_PCT = 0.10
    _fb_log("PROFIT_TARGET_PCT", 0.10)
    CONSISTENCY_MAX_PCT = 0.30
    _fb_log("CONSISTENCY_MAX_PCT", 0.30)
    MIN_RR_RATIO = 2.0
    _fb_log("MIN_RR_RATIO", 2.0)
    ATR_MULTIPLIER = 1.5
    _fb_log("ATR_MULTIPLIER", 1.5)
    COOLDOWN_MINUTES = 15
    _fb_log("COOLDOWN_MINUTES", 15)
    MIN_TRADING_DAYS = 10
    _fb_log("MIN_TRADING_DAYS", 10)
    MAX_TRADING_DAYS = 0
    _fb_log("MAX_TRADING_DAYS", 0)
    MAX_RISK_AMOUNT = 800.0
    _fb_log("MAX_RISK_AMOUNT", 800.0)
    MAX_SPREAD_POINTS = 120
    _fb_log("MAX_SPREAD_POINTS", 120)
    TRADING_START_HOUR = 0
    _fb_log("TRADING_START_HOUR", 0)
    TRADING_END_HOUR = 24
    _fb_log("TRADING_END_HOUR", 24)
    DANGER_HOURS = [7, 17]
    _fb_log("DANGER_HOURS", [7, 17])
    RECALIBRATION_FREQUENCY = 50
    _fb_log("RECALIBRATION_FREQUENCY", 50)
    AUTO_PAUSE_LOSSES = 5
    _fb_log("AUTO_PAUSE_LOSSES", 5)
    MAX_CORRELATED_EXPOSURE = 1.5
    _fb_log("MAX_CORRELATED_EXPOSURE", 1.5)
    CIRCUIT_BREAKER_DD_PCT = 0.08
    _fb_log("CIRCUIT_BREAKER_DD_PCT", 0.08)
    CYCLE_SECONDS = 15
    _fb_log("CYCLE_SECONDS", 15)
    HISTORY_LOOKBACK_DAYS = 7
    _fb_log("HISTORY_LOOKBACK_DAYS", 7)
    SYMBOL_LIMITS = {
        # XAUUSD H4 (fallback — synchronisé avec default.yaml 25 Juin 2026)
        "XAUUSD": dict(
            max_lot=0.06,
            risk_mult=1.10,
            max_spread_points=60,
            adx_thresh=22,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=18,
            sl_atr_trending=1.8,
            tp_atr_trending=5.0,
            sl_atr_ranging=1.5,
            tp_atr_ranging=3.5,
        ),
        # BTCUSD H1 (fallback — synchronisé 25 Juin 2026)
        "BTCUSD": dict(
            max_lot=0.06,
            risk_mult=0.30,
            max_spread_points=150,
            adx_thresh=20,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=22,
            sl_atr_trending=3.0,
            tp_atr_trending=7.0,
            sl_atr_ranging=2.5,
            tp_atr_ranging=5.0,
        ),
        # US500.cash H4 (fallback — désactivé 25 Juin 2026)
        "US500.cash": dict(
            max_lot=0.06,
            risk_mult=0.50,
            max_spread_points=40,
            adx_thresh=22,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=18,
            sl_atr_trending=1.5,
            tp_atr_trending=4.0,
            sl_atr_ranging=1.2,
            tp_atr_ranging=3.0,
        ),
        # US30.cash H1 (fallback — ajouté 28 Juin 2026 — remplace EURUSD)
        "US30.cash": dict(
            max_lot=0.03,
            risk_mult=0.50,
            max_spread_points=60,
            adx_thresh=22,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=20,
            sl_atr_trending=1.5,
            tp_atr_trending=4.5,
            sl_atr_ranging=1.2,
            tp_atr_ranging=3.0,
        ),
        # EURUSD désactivé 28 Juin 2026 (PF 0.75 après coûts, 6.7% WR live)
        # USDJPY H1 (fallback — synchronisé 25 Juin 2026)
        "USDJPY": dict(
            max_lot=0.17,
            risk_mult=1.00,
            max_spread_points=45,
            adx_thresh=22,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=20,
            sl_atr_trending=1.5,
            tp_atr_trending=4.5,
            sl_atr_ranging=1.2,
            tp_atr_ranging=3.0,
        ),
        # GBPUSD H1 (fallback — synchronisé 25 Juin 2026)
        "GBPUSD": dict(
            max_lot=0.17,
            risk_mult=0.90,
            max_spread_points=50,
            adx_thresh=22,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=20,
            sl_atr_trending=1.5,
            tp_atr_trending=4.5,
            sl_atr_ranging=1.2,
            tp_atr_ranging=3.0,
        ),
        # USDCAD H1 (fallback — synchronisé 25 Juin 2026)
        "USDCAD": dict(
            max_lot=0.17,
            risk_mult=0.85,
            max_spread_points=45,
            adx_thresh=22,
            allow_buys=True,
            allow_shorts=True,
            momentum_period=20,
            sl_atr_trending=1.5,
            tp_atr_trending=4.5,
            sl_atr_ranging=1.2,
            tp_atr_ranging=3.0,
        ),
    }
    SYMBOL_TIMEFRAMES = {
        "XAUUSD": "H4",
        "BTCUSD": "H1",
        "US30.cash": "H1",
        "USDJPY": "H1",
        "GBPUSD": "H1",
        "USDCAD": "H1",
        "EURUSD": "H1",
    }
    ML_EXPERIMENT_TRACKING = False
    ML_TRACKING_URI = ""
    CONCEPT_DRIFT = dict(
        enabled=True,
        window_size=100,
        psi_threshold_light=0.10,
        psi_threshold_moderate=0.20,
        psi_threshold_severe=0.25,
        auto_retrain=True,
        retrain_cooldown_hours=24,
    )
    RETRAINING = dict(
        days=90,
        min_samples=50,
        epochs=10,
        n_splits=5,
        schedule_trades=500,
        log_mlflow=True,
    )
    __version__ = "4.1.0"
    NEWS_MINUTES_BEFORE = 5
    NEWS_MINUTES_AFTER = 5


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
    global \
        AUTO_PAUSE_LOSSES, \
        MAX_CORRELATED_EXPOSURE, \
        CIRCUIT_BREAKER_DD_PCT, \
        CYCLE_SECONDS, \
        HISTORY_LOOKBACK_DAYS, \
        BATCH_INTERVAL_SEC
    global SYMBOL_LIMITS, SYMBOL_TIMEFRAMES, __version__, DANGER_HOURS
    global NEWS_MINUTES_BEFORE, NEWS_MINUTES_AFTER
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
    BATCH_INTERVAL_SEC = _cfg.trading.batch_interval_sec
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
    DANGER_HOURS = _cfg.trading.danger_hours
    RECALIBRATION_FREQUENCY = _cfg.risk.recalibration_frequency
    AUTO_PAUSE_LOSSES = _cfg.risk.auto_pause_losses
    MAX_CORRELATED_EXPOSURE = _cfg.risk.max_correlated_exposure
    CIRCUIT_BREAKER_DD_PCT = _cfg.risk.circuit_breaker_dd_pct
    CYCLE_SECONDS = _cfg.robot.cycle_seconds
    HISTORY_LOOKBACK_DAYS = _cfg.trading.history_lookback_days
    SYMBOL_LIMITS = {sym: lim.model_dump() for sym, lim in _cfg.symbol_limits.items()}
    SYMBOL_TIMEFRAMES = {sym: limits.get("timeframe", "H1") for sym, limits in SYMBOL_LIMITS.items()}
    __version__ = _cfg.robot.version
    NEWS_MINUTES_BEFORE = _cfg.news.minutes_before
    NEWS_MINUTES_AFTER = _cfg.news.minutes_after
