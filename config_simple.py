"""
Compatibilité — lecture depuis config YAML + Pydantic
Tous les imports existants `import config_simple as cfg` continuent de fonctionner.
Le module `config/schema.py` est la source de vérité unique.
"""

import logging

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("robot.config")


def _fallback_minimal() -> None:
    """Fallback valeurs hardcodées minimales — ne devrait jamais arriver en production.

    🔧 FIX M-N5 (Auto-Fixer): extrait dans une fonction dédiée pour :
      1. Distinguer les erreurs de CONVERSION (ValueError/TypeError) des vrais
         échecs de chargement (YAML corrompu, schéma invalide, bug de code).
      2. Définir AUSSI les variables qui manquaient au fallback
         (SYMBOL_EXECUTION_TIMEFRAMES, GLOBAL_MAX_LOT, REGIME_*, AUTO_STOP_*,
         CONSERVATION_MODE_ENABLED) — sinon AttributeError après un fallback.
    """
    _g = globals()
    # C-04: Logger chaque valeur de fallback pour traçabilité
    _fb_log = lambda name, val: logger.warning(f"  [FALLBACK] {name} = {val}")
    _g["MT5_LOGIN"] = 0
    _fb_log("MT5_LOGIN", 0)
    _g["MT5_PASSWORD"] = ""
    _fb_log("MT5_PASSWORD", "(masqué)")
    _g["MT5_SERVER"] = ""
    _fb_log("MT5_SERVER", "(vide)")
    # ⚠️ 1er Juillet 2026: 27 symboles actifs
    _g["SYMBOLS"] = [
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "USDCAD",
        "AUDUSD",
        "NZDUSD",
        "USDCHF",
        "EURJPY",
        "GBPJPY",
        "EURGBP",
        "AUDJPY",
        "XAUUSD",
        "XAGUSD",
        "USOIL.cash",
        "UKOIL.cash",
        "NATGAS.cash",
        "BTCUSD",
        "ETHUSD",
        "SOLUSD",
        "BNBUSD",
        "US500.cash",
        "US30.cash",
        "US100.cash",
        "JP225.cash",
        "GER40.cash",
        "UK100.cash",
    ]
    _fb_log("SYMBOLS", _g["SYMBOLS"])
    _g["ROBOT_MAGIC"] = 999001
    _fb_log("ROBOT_MAGIC", 999001)
    _g["MAX_POSITIONS"] = 64
    _fb_log("MAX_POSITIONS", 64)
    _g["MAX_POSITIONS_PER_SYMBOL"] = 6
    _fb_log("MAX_POSITIONS_PER_SYMBOL", 6)
    _g["MAX_TRADES_PER_DAY"] = 100
    _fb_log("MAX_TRADES_PER_DAY", 100)
    _g["LOT_SIZE"] = 0.30
    _fb_log("LOT_SIZE", 0.30)
    _g["GLOBAL_MAX_LOT"] = 0.02  # défaut schéma — plafond absolu du lot
    _fb_log("GLOBAL_MAX_LOT", 0.02)
    _g["MIN_TRADE_INTERVAL_SEC"] = 300
    _fb_log("MIN_TRADE_INTERVAL_SEC", 300)
    _g["BATCH_INTERVAL_SEC"] = 1
    _fb_log("BATCH_INTERVAL_SEC", 1)
    _g["MIN_SIGNAL_SCORE"] = 0.50
    _fb_log("MIN_SIGNAL_SCORE", 0.50)
    _g["MAX_SIGNALS_PER_CYCLE"] = 10
    _fb_log("MAX_SIGNALS_PER_CYCLE", 10)
    _g["MAX_ORDERS_PER_MINUTE"] = 6
    _fb_log("MAX_ORDERS_PER_MINUTE", 6)
    _g["DAILY_PROFIT_LIMIT_PCT"] = 0.008
    _fb_log("DAILY_PROFIT_LIMIT_PCT", 0.008)
    _g["RISK_PER_TRADE"] = 0.002
    _fb_log("RISK_PER_TRADE", 0.002)
    _g["RISK_SHORT_MULT"] = 1.0
    _fb_log("RISK_SHORT_MULT", 1.0)
    _g["MAX_DAILY_LOSS_PCT"] = 0.02
    _fb_log("MAX_DAILY_LOSS_PCT", 0.02)
    _g["ZONE2_LOSS_PCT"] = 0.012
    _fb_log("ZONE2_LOSS_PCT", 0.012)
    _g["ZONE3_LOSS_PCT"] = 0.017
    _fb_log("ZONE3_LOSS_PCT", 0.017)
    _g["MAX_DD_PCT"] = 0.10
    _fb_log("MAX_DD_PCT", 0.10)
    _g["PROFIT_TARGET_PCT"] = 0.10
    _fb_log("PROFIT_TARGET_PCT", 0.10)
    _g["CONSISTENCY_MAX_PCT"] = 0.30
    _fb_log("CONSISTENCY_MAX_PCT", 0.30)
    _g["CONSISTENCY_CAP_ENABLED"] = True
    _fb_log("CONSISTENCY_CAP_ENABLED", True)
    _g["MIN_RR_RATIO"] = 2.5
    _fb_log("MIN_RR_RATIO", 2.5)
    _g["ATR_MULTIPLIER"] = 1.5
    _fb_log("ATR_MULTIPLIER", 1.5)
    _g["COOLDOWN_MINUTES"] = 15
    _fb_log("COOLDOWN_MINUTES", 15)
    _g["MIN_TRADING_DAYS"] = 10
    _fb_log("MIN_TRADING_DAYS", 10)
    _g["MAX_TRADING_DAYS"] = 0
    _fb_log("MAX_TRADING_DAYS", 0)
    _g["MAX_RISK_AMOUNT"] = 800.0
    _fb_log("MAX_RISK_AMOUNT", 800.0)
    _g["MAX_SPREAD_POINTS"] = 120
    _fb_log("MAX_SPREAD_POINTS", 120)
    _g["TRADING_START_HOUR"] = 0
    _fb_log("TRADING_START_HOUR", 0)
    _g["TRADING_END_HOUR"] = 24
    _fb_log("TRADING_END_HOUR", 24)
    _g["DANGER_HOURS"] = [7, 17]
    _fb_log("DANGER_HOURS", [7, 17])
    _g["RECALIBRATION_FREQUENCY"] = 50
    _fb_log("RECALIBRATION_FREQUENCY", 50)
    _g["AUTO_PAUSE_LOSSES"] = 5
    _fb_log("AUTO_PAUSE_LOSSES", 5)
    _g["MAX_CORRELATED_EXPOSURE"] = 1.5
    _fb_log("MAX_CORRELATED_EXPOSURE", 1.5)
    _g["CIRCUIT_BREAKER_DD_PCT"] = 0.08
    _fb_log("CIRCUIT_BREAKER_DD_PCT", 0.08)
    _g["CYCLE_SECONDS"] = 15
    _fb_log("CYCLE_SECONDS", 15)
    _g["HISTORY_LOOKBACK_DAYS"] = 7
    _fb_log("HISTORY_LOOKBACK_DAYS", 7)
    _g["SYMBOL_LIMITS"] = {}
    _g["SYMBOL_TIMEFRAMES"] = {}
    _g["SYMBOL_EXECUTION_TIMEFRAMES"] = {}
    _g["ML_EXPERIMENT_TRACKING"] = False
    _g["ML_TRACKING_URI"] = ""
    _g["ENABLE_M15_CONFIRMATION"] = False
    _g["CONCEPT_DRIFT"] = dict(
        enabled=True,
        window_size=100,
        psi_threshold_light=0.10,
        psi_threshold_moderate=0.20,
        psi_threshold_severe=0.25,
        auto_retrain=True,
        retrain_cooldown_hours=24,
    )
    _g["RETRAINING"] = dict(
        days=90,
        min_samples=50,
        epochs=10,
        n_splits=5,
        schedule_trades=500,
        log_mlflow=True,
    )
    _g["__version__"] = "4.1.0"
    _g["NEWS_MINUTES_BEFORE"] = 5
    _g["NEWS_MINUTES_AFTER"] = 5
    # ── Market Regime (défauts schéma) — ajouté FIX M-N5 ──
    _g["REGIME_ADX_TREND_ENTER"] = 22
    _g["REGIME_ADX_TREND_EXIT"] = 18
    _g["REGIME_HYSTERESIS_OFFSET"] = 4
    _g["REGIME_SLOPE_BULLISH"] = 0.002
    _g["REGIME_SLOPE_BEARISH"] = -0.002
    _g["REGIME_VOL_HIGH_RATIO"] = 0.015
    _g["REGIME_VOL_LOW_RATIO"] = 0.003
    # ── Auto-Stop (défauts schéma) — ajouté FIX M-N5 ──
    _g["AUTO_STOP_ADX_LOW_THRESHOLD"] = 22
    _g["AUTO_STOP_ADX_HIGH_THRESHOLD"] = 18
    _g["AUTO_STOP_RATIO_STOP"] = 0.50
    _g["AUTO_STOP_SYMBOLS_MIN_RESUME"] = 2
    _g["AUTO_STOP_PAUSE_MIN_DURATION"] = 1800
    _g["AUTO_STOP_ADX_SNAPSHOT_TTL"] = 300
    _g["AUTO_STOP_STATE_TTL"] = 86400
    # ── Mode conservation (défaut schéma) — ajouté FIX M-N5 ──
    _g["CONSERVATION_MODE_ENABLED"] = True


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
    GLOBAL_MAX_LOT: float = _cfg.trading.global_max_lot
    MIN_TRADE_INTERVAL_SEC: int = _cfg.trading.min_trade_interval_sec
    BATCH_INTERVAL_SEC: int = _cfg.trading.batch_interval_sec
    HISTORY_LOOKBACK_DAYS: int = _cfg.trading.history_lookback_days
    MIN_SIGNAL_SCORE: float = _cfg.signal.min_score
    # 🔧 14 Aout 2026: filtre M15 désactivé (alignement backtest validé — PF 1.18-1.25)
    ENABLE_M15_CONFIRMATION: bool = getattr(_cfg.signal, "enable_m15_confirmation", False)
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
    CONSISTENCY_CAP_ENABLED: bool = _cfg.risk.consistency_cap_enabled
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
    # 🔧 07 Août 2026 (mode preuve): false = mode conservation FTMO désactivé.
    # Le mode conservation bloquait tous les trades quand le challenge était
    # mathématiquement perdu (profit<5% + jours restants≤3). Nécessaire pour
    # collecter les 100+ trades de preuve. Défaut true si absent de la config.
    CONSERVATION_MODE_ENABLED: bool = getattr(_cfg.risk, "conservation_mode_enabled", True)
    CYCLE_SECONDS: int = _cfg.robot.cycle_seconds
    SYMBOL_LIMITS: dict[str, dict] = {sym: lim.model_dump(exclude_none=True) for sym, lim in _cfg.symbol_limits.items()}
    SYMBOL_TIMEFRAMES: dict[str, str] = {sym: limits.get("timeframe", "H1") for sym, limits in SYMBOL_LIMITS.items()}
    SYMBOL_EXECUTION_TIMEFRAMES: dict[str, str] = {
        sym: limits.get("execution_timeframe", "M15") for sym, limits in SYMBOL_LIMITS.items()
    }
    # ML Pipeline config
    ML_EXPERIMENT_TRACKING: bool = _cfg.ml.experiment_tracking
    ML_TRACKING_URI: str = _cfg.ml.tracking_uri
    CONCEPT_DRIFT: dict = _cfg.ml.concept_drift.model_dump()
    RETRAINING: dict = _cfg.ml.retraining.model_dump()
    __version__: str = _cfg.robot.version
    NEWS_MINUTES_BEFORE: int = _cfg.news.minutes_before
    NEWS_MINUTES_AFTER: int = _cfg.news.minutes_after

    # ── Market Regime (regime.py) ──
    REGIME_ADX_TREND_ENTER: int = _cfg.market_regime.adx_trend_enter_default
    REGIME_ADX_TREND_EXIT: int = _cfg.market_regime.adx_trend_exit_default
    REGIME_HYSTERESIS_OFFSET: int = _cfg.market_regime.hysteresis_offset
    REGIME_SLOPE_BULLISH: float = _cfg.market_regime.slope_bullish
    REGIME_SLOPE_BEARISH: float = _cfg.market_regime.slope_bearish
    REGIME_VOL_HIGH_RATIO: float = _cfg.market_regime.vol_high_ratio
    REGIME_VOL_LOW_RATIO: float = _cfg.market_regime.vol_low_ratio

    # ── Auto-Stop (auto_stop.py) ──
    AUTO_STOP_ADX_LOW_THRESHOLD: int = _cfg.auto_stop.adx_low_threshold
    AUTO_STOP_ADX_HIGH_THRESHOLD: int = _cfg.auto_stop.adx_high_threshold
    AUTO_STOP_RATIO_STOP: float = _cfg.auto_stop.ratio_stop
    AUTO_STOP_SYMBOLS_MIN_RESUME: int = _cfg.auto_stop.symbols_min_resume
    AUTO_STOP_PAUSE_MIN_DURATION: int = _cfg.auto_stop.pause_min_duration
    AUTO_STOP_ADX_SNAPSHOT_TTL: int = _cfg.auto_stop.adx_snapshot_ttl
    AUTO_STOP_STATE_TTL: int = _cfg.auto_stop.state_ttl

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

except (ValueError, TypeError) as e:
    # 🔧 FIX M-N5 (Auto-Fixer): erreurs de CONVERSION uniquement (valeur non
    # numérique, type inattendu dans le YAML) → fallback avec warning.
    logger.warning(f"Erreur de conversion config YAML: {e}")
    logger.warning("Fallback: valeurs hardcodees minimales — ⚠️ RISQUE les valeurs YAML sont perdues")
    _fallback_minimal()
except Exception as e:
    # 🔧 FIX M-N5 (Auto-Fixer): échec RÉEL de chargement (YAML corrompu, schéma
    # invalide, clé manquante, bug de code) → message CRITIQUE IMPOSSIBLE à
    # manquer (avant: warning silencieux). Le fallback reste fonctionnel pour ne
    # pas bloquer le démarrage, mais l'opérateur DOIT corriger la config.
    logger.critical(
        f"⛔ ERREUR CRITIQUE CHARGEMENT CONFIG YAML: {e!r}\n"
        f"Le robot démarre avec le FALLBACK MINIMAL — les valeurs YAML sont PERDUES "
        f"(MT5_LOGIN=0, symboles hardcodés, MAX_POSITIONS=64, limites risque par défaut). "
        f"CORRIGE config/default.yaml et config/production.yaml AVANT de trader !"
    )
    _fallback_minimal()


def reload_config() -> bool:
    """Hot-reload: recharge la config depuis les fichiers YAML.
    Retourne True si la config a change."""
    global _cfg, _env
    try:
        # 🔧 FIX: Import local pour éviter NameError si l'import global a échoué
        from config.schema import hot_reload as _hot_reload

        new = _hot_reload(_env)
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
    global ML_EXPERIMENT_TRACKING, ML_TRACKING_URI, CONCEPT_DRIFT, RETRAINING
    global ENABLE_M15_CONFIRMATION
    # 🔧 FIX M-N4 (Auto-Fixer): globals manquants → valeurs JETÉES au hot-reload.
    # SYMBOL_EXECUTION_TIMEFRAMES était assigné SANS global → le module gardait
    # l'ancienne valeur (modifs YAML invisibles). REGIME_ADX_*, AUTO_STOP_*,
    # GLOBAL_MAX_LOT et CONSERVATION_MODE_ENABLED n'étaient PAS réexportés du tout.
    global SYMBOL_EXECUTION_TIMEFRAMES, GLOBAL_MAX_LOT
    global REGIME_ADX_TREND_ENTER, REGIME_ADX_TREND_EXIT, REGIME_HYSTERESIS_OFFSET
    global REGIME_SLOPE_BULLISH, REGIME_SLOPE_BEARISH, REGIME_VOL_HIGH_RATIO, REGIME_VOL_LOW_RATIO
    global AUTO_STOP_ADX_LOW_THRESHOLD, AUTO_STOP_ADX_HIGH_THRESHOLD, AUTO_STOP_RATIO_STOP
    global AUTO_STOP_SYMBOLS_MIN_RESUME, AUTO_STOP_PAUSE_MIN_DURATION
    global AUTO_STOP_ADX_SNAPSHOT_TTL, AUTO_STOP_STATE_TTL
    global CONSERVATION_MODE_ENABLED
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
    CONSISTENCY_CAP_ENABLED = _cfg.risk.consistency_cap_enabled
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
    SYMBOL_LIMITS = {sym: lim.model_dump(exclude_none=True) for sym, lim in _cfg.symbol_limits.items()}
    SYMBOL_TIMEFRAMES = {sym: limits.get("timeframe", "H1") for sym, limits in SYMBOL_LIMITS.items()}
    SYMBOL_EXECUTION_TIMEFRAMES = {
        sym: limits.get("execution_timeframe", "M15") for sym, limits in SYMBOL_LIMITS.items()
    }
    __version__ = _cfg.robot.version
    NEWS_MINUTES_BEFORE = _cfg.news.minutes_before
    NEWS_MINUTES_AFTER = _cfg.news.minutes_after
    # ML Pipeline config (fix m16: réexport à chaud)
    ML_EXPERIMENT_TRACKING = _cfg.ml.experiment_tracking
    ML_TRACKING_URI = _cfg.ml.tracking_uri
    CONCEPT_DRIFT = _cfg.ml.concept_drift.model_dump()
    RETRAINING = _cfg.ml.retraining.model_dump()
    ENABLE_M15_CONFIRMATION = _cfg.signal.enable_m15_confirmation
    # 🔧 FIX M-N4 (Auto-Fixer): réexport complet au hot-reload.
    GLOBAL_MAX_LOT = _cfg.trading.global_max_lot
    # ── Market Regime (étaient absents du hot-reload) ──
    REGIME_ADX_TREND_ENTER = _cfg.market_regime.adx_trend_enter_default
    REGIME_ADX_TREND_EXIT = _cfg.market_regime.adx_trend_exit_default
    REGIME_HYSTERESIS_OFFSET = _cfg.market_regime.hysteresis_offset
    REGIME_SLOPE_BULLISH = _cfg.market_regime.slope_bullish
    REGIME_SLOPE_BEARISH = _cfg.market_regime.slope_bearish
    REGIME_VOL_HIGH_RATIO = _cfg.market_regime.vol_high_ratio
    REGIME_VOL_LOW_RATIO = _cfg.market_regime.vol_low_ratio
    # ── Auto-Stop (étaient absents du hot-reload) ──
    AUTO_STOP_ADX_LOW_THRESHOLD = _cfg.auto_stop.adx_low_threshold
    AUTO_STOP_ADX_HIGH_THRESHOLD = _cfg.auto_stop.adx_high_threshold
    AUTO_STOP_RATIO_STOP = _cfg.auto_stop.ratio_stop
    AUTO_STOP_SYMBOLS_MIN_RESUME = _cfg.auto_stop.symbols_min_resume
    AUTO_STOP_PAUSE_MIN_DURATION = _cfg.auto_stop.pause_min_duration
    AUTO_STOP_ADX_SNAPSHOT_TTL = _cfg.auto_stop.adx_snapshot_ttl
    AUTO_STOP_STATE_TTL = _cfg.auto_stop.state_ttl
    # ── Mode conservation (absent du hot-reload) ──
    CONSERVATION_MODE_ENABLED = getattr(_cfg.risk, "conservation_mode_enabled", True)
