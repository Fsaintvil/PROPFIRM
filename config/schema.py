"""Configuration loader — YAML + Pydantic + env interpolation

Usage:
    from config.schema import load_config
    cfg = load_config()           # default.yaml + production.yaml
    cfg = load_config("production")  # spécifier l'environnement

    # Accès comme attributs
    cfg.robot.magic          # 999001
    cfg.trading.symbols      # ["USDCAD", "GBPUSD", "USDCHF"]
    cfg.risk.per_trade_pct   # 0.004
    cfg.symbol_limits.USDCAD.max_lot  # 0.55
    cfg.secrets.mt5_login    # lu depuis .env

Compatibilité avec config_simple.py:
    cfg = load_config()
    cfg.as_flat_dict()  # -> {"ROBOT_MAGIC": 999001, "RISK_PER_TRADE": 0.004, ...}
"""
import logging
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

load_dotenv()
logger = logging.getLogger("robot.config")


# ── Modèles Pydantic ──

class RobotConfig(BaseModel):
    magic: int = 999001
    cycle_seconds: int = Field(default=15, ge=5, le=120)
    version: str = "2.5.0"


class TradingConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["XAUUSD", "BTCUSD", "ETHUSD"])
    max_positions: int = Field(default=6, ge=1, le=20)
    max_positions_per_symbol: int = Field(default=2, ge=1, le=5)
    max_trades_per_day: int = Field(default=12, ge=1, le=200)  # 🔓 Mode MAX
    max_signals_per_cycle: int = Field(default=7, ge=1, le=15)
    max_orders_per_minute: int = Field(default=6, ge=1, le=30)
    lot_size: float = Field(default=0.05, ge=0.01, le=10)
    min_trade_interval_sec: int = Field(default=30, ge=5, le=3600)  # 🔓 Mode MAX
    batch_interval_sec: int = Field(default=300, ge=60, le=3600, description="Intervalle entre batches de signaux (secondes)")
    trading_start_hour: int = Field(default=0, ge=0, le=23)
    trading_end_hour: int = Field(default=24, ge=1, le=24)
    danger_hours: list[int] = Field(default_factory=list, description="Heures UTC à éviter (ex: [12] = 0% WR à 12:00)")
    history_lookback_days: int = Field(default=7, ge=1, le=365)

    @field_validator("trading_end_hour")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("trading_start_hour")
        if start is not None and v <= start:
            raise ValueError(f"trading_end_hour ({v}) doit etre > trading_start_hour ({start})")
        return v


class SignalConfig(BaseModel):
    min_score: float = Field(default=0.65, ge=0.0, le=1.0)
    daily_profit_limit_pct: float = Field(default=0.008, ge=0.0, le=0.05)


class RiskConfig(BaseModel):
    per_trade_pct: float = Field(default=0.004, ge=0.001, le=0.02)
    short_mult: float = Field(default=1.0, ge=0.1, le=2.0)
    max_daily_loss_pct: float = Field(default=0.02, ge=0.005, le=0.05)
    zone2_loss_pct: float = Field(default=0.01, ge=0.005, le=0.03)
    zone3_loss_pct: float = Field(default=0.015, ge=0.01, le=0.04)
    max_dd_pct: float = Field(default=0.10, ge=0.02, le=0.15)
    profit_target_pct: float = Field(default=0.10, ge=0.02, le=0.20)
    consistency_max_pct: float = Field(default=0.30, ge=0.1, le=0.5)
    min_rr_ratio: float = Field(default=2.0, ge=1.0, le=10.0)
    atr_multiplier: float = Field(default=1.5, ge=0.5, le=5.0)
    cooldown_minutes: int = Field(default=30, ge=5, le=240)
    min_trading_days: int = Field(default=10, ge=1, le=60)
    max_trading_days: int = Field(default=0, ge=0)
    max_risk_amount: float = Field(default=800.0, ge=0.0)
    max_spread_points: int = Field(default=50, ge=10, le=200)
    auto_pause_losses: int = Field(default=5, ge=1, le=10)
    recalibration_frequency: int = Field(default=50, ge=10, le=500)
    max_correlated_exposure: float = Field(default=1.5, ge=1.0, le=5.0,
        description="Limite d'exposition corrélée totale (somme des corrélations same-direction). "
                    "1.5 = ~2 trades EUR/USD/GBP ou 3 trades décorrélés. "
                    "Resserré Juin 2026 pour éviter pertes simultanées.")
    circuit_breaker_dd_pct: float = Field(default=0.08, ge=0.02, le=0.15,
        description="DD > ce seuil → shorts bloqués (protection drawdown FTMO)")


class SymbolLimit(BaseModel):
    model_config = ConfigDict(extra='allow')

    max_lot: float = Field(default=0.10, ge=0.01, le=10)
    min_lot: float = Field(default=0.01, ge=0.01, le=10)
    risk_mult: float = Field(default=1.0, ge=0.0, le=3.0)
    max_spread_points: int = Field(default=50, ge=10, le=500)
    allow_buys: bool = True
    allow_shorts: bool = True
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    adx_thresh: float | None = Field(default=None, ge=5, le=50)
    max_daily_trades: int | None = Field(default=None, ge=1, le=10)
    allow_ranging: bool | None = Field(default=None)
    dl_required: bool | None = Field(default=None)
    # Champs étendus (production calibration per-symbol)
    momentum_period: int | None = Field(default=None, ge=5, le=100)
    sl_atr_trending: float | None = Field(default=None, ge=0.5, le=6.0)
    tp_atr_trending: float | None = Field(default=None, ge=1.0, le=15.0)
    sl_atr_ranging: float | None = Field(default=None, ge=0.5, le=5.0)
    tp_atr_ranging: float | None = Field(default=None, ge=1.0, le=10.0)
    adx_slope_threshold: float | None = Field(default=None, ge=-20.0, le=0.0)
    adx_slope_threshold_strong: float | None = Field(default=None, ge=-20.0, le=0.0)
    max_daily_loss_pct_override: float | None = Field(default=None, ge=0.005, le=0.05)
    circuit_breaker_dd_pct_override: float | None = Field(default=None, ge=0.02, le=0.15)
    # Nouveaux champs calibration production (Juin 2026)
    threshold_trending: float | None = Field(default=None, ge=1.0, le=4.0,
        description="Seuil momentum en trending (×ATR). Ex: 2.5 = XAUUSD H4")
    threshold_ranging: float | None = Field(default=None, ge=1.0, le=4.0,
        description="Seuil momentum en ranging (×ATR). Ex: 1.5 = BTCUSD H1")
    pullback_band_trending: float | None = Field(default=None, ge=0.1, le=2.0,
        description="Bande pullback en trending (×ATR). Ex: 0.5 = XAUUSD H4")
    pullback_band_ranging: float | None = Field(default=None, ge=0.1, le=2.0,
        description="Bande pullback en ranging (×ATR). Ex: 0.3 = XAUUSD H4")
    preferred_hours: list[int] | None = Field(default=None,
        description="Heures de trading préférées (UTC). Ex: [13-22] = XAUUSD London+NY")
    news_minutes_before: int | None = Field(default=None, ge=0, le=60)
    news_minutes_after: int | None = Field(default=None, ge=0, le=60)


class SecretsConfig(BaseModel):
    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def mt5_login_int(self) -> int:
        try:
            return int(self.mt5_login)
        except (ValueError, TypeError):
            return 0


class RetrainingConfig(BaseModel):
    days: int = Field(default=90, ge=7, le=365)
    min_samples: int = Field(default=50, ge=10, le=5000)
    epochs: int = Field(default=10, ge=1, le=100)
    n_splits: int = Field(default=5, ge=2, le=20)
    schedule_trades: int = Field(default=500, ge=50, le=10000)
    log_mlflow: bool = True


class ConceptDriftConfig(BaseModel):
    enabled: bool = True
    window_size: int = Field(default=100, ge=20, le=1000)
    psi_threshold_light: float = Field(default=0.10, ge=0.01, le=0.5)
    psi_threshold_moderate: float = Field(default=0.20, ge=0.05, le=0.8)
    psi_threshold_severe: float = Field(default=0.25, ge=0.10, le=1.0)
    auto_retrain: bool = True
    retrain_cooldown_hours: int = Field(default=24, ge=1, le=168)


class MLConfig(BaseModel):
    experiment_tracking: bool = True
    tracking_uri: str = ""
    retraining: RetrainingConfig = RetrainingConfig()
    concept_drift: ConceptDriftConfig = ConceptDriftConfig()


class NewsConfig(BaseModel):
    enabled: bool = True
    minutes_before: int = Field(default=15, ge=0, le=60)
    minutes_after: int = Field(default=15, ge=0, le=60)
    manual_file: str = "config/economic_events.json"


class ConfigSchema(BaseModel):
    robot: RobotConfig = RobotConfig()
    trading: TradingConfig = TradingConfig()
    signal: SignalConfig = SignalConfig()
    risk: RiskConfig = RiskConfig()
    ml: MLConfig = MLConfig()
    news: NewsConfig = NewsConfig()
    symbol_limits: dict[str, SymbolLimit] = Field(default_factory=dict)
    secrets: SecretsConfig = SecretsConfig()
    _env_name: str = "default"
    _loaded_at: str = ""

    @model_validator(mode="after")
    def ensure_default_symbols(self):
        if not self.symbol_limits:
            defaults = {
                "XAUUSD": SymbolLimit(max_lot=0.10, risk_mult=1.00, max_spread_points=60,
                                      momentum_period=20, sl_atr_trending=1.8, tp_atr_trending=5.0,
                                      sl_atr_ranging=1.5, tp_atr_ranging=3.5,
                                      threshold_trending=2.5, threshold_ranging=2.0,
                                      pullback_band_trending=0.5, pullback_band_ranging=0.3),
                "BTCUSD": SymbolLimit(max_lot=0.03, risk_mult=0.65, max_spread_points=150,
                                      momentum_period=24, sl_atr_trending=3.0, tp_atr_trending=7.0,
                                      sl_atr_ranging=2.5, tp_atr_ranging=5.0,
                                      threshold_trending=2.0, threshold_ranging=1.5,
                                      pullback_band_trending=0.8, pullback_band_ranging=0.5),
                "US500.cash": SymbolLimit(max_lot=0.10, risk_mult=0.80, max_spread_points=40,
                                          momentum_period=20, sl_atr_trending=1.5, tp_atr_trending=4.0,
                                          sl_atr_ranging=1.2, tp_atr_ranging=3.0,
                                          threshold_trending=2.0, threshold_ranging=1.5,
                                          pullback_band_trending=0.3, pullback_band_ranging=0.2),
            }
            self.symbol_limits = defaults
        for sym in self.trading.symbols:
            if sym not in self.symbol_limits:
                logger.warning(f"Symbole {sym} non dans symbol_limits, ajout avec defauts")
                self.symbol_limits[sym] = SymbolLimit()
        return self

    def as_flat_dict(self) -> dict[str, Any]:
        dictionnaire = {}
        _flatten("", self.model_dump(), dictionnaire)
        return {k.upper(): v for k, v in dictionnaire.items()}

    def reload(self):
        return load_config(self._env_name)


def _flatten(prefix, d, out):
    if isinstance(d, dict):
        for k, v in d.items():
            p = f"{prefix}_{k}" if prefix else k
            if isinstance(v, dict):
                _flatten(p, v, out)
            else:
                out[p] = v
    else:
        out[prefix] = d


# ── Interpolation d'environnement ──

_env_pattern = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        def _replacer(m):
            var_name = m.group(1)
            return os.getenv(var_name, "")
        return _env_pattern.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


# ── Loader ──

@lru_cache(maxsize=4)
def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_CONFIG_CACHE = {}
_CONFIG_MTIME = {}


def load_config(env: str = "production", config_dir: Path | None = None) -> ConfigSchema:
    if config_dir is None:
        config_dir = Path(__file__).parent
    default_path = config_dir / "default.yaml"
    env_path = config_dir / f"{env}.yaml"
    if not default_path.exists():
        raise FileNotFoundError(f"Configuration par defaut introuvable: {default_path}")
    raw = _load_yaml(default_path)
    raw = _interpolate(raw)
    if env_path.exists():
        overrides = _load_yaml(env_path)
        overrides = _interpolate(overrides)
        raw = _deep_merge(raw, overrides)
    cfg = ConfigSchema(**raw)
    cfg._env_name = env
    cfg._loaded_at = datetime.utcnow().isoformat()
    _CONFIG_CACHE[env] = cfg
    _CONFIG_MTIME[env] = {
        "default": default_path.stat().st_mtime,
        "env": env_path.stat().st_mtime if env_path.exists() else 0,
    }
    logger.info(f"Config chargee: {env} (robot v{cfg.robot.version})")
    return cfg


def check_config_changed(env: str = "production", config_dir: Path | None = None) -> bool:
    if config_dir is None:
        config_dir = Path(__file__).parent
    cached_mtimes = _CONFIG_MTIME.get(env)
    if not cached_mtimes:
        return False
    default_path = config_dir / "default.yaml"
    env_path = config_dir / f"{env}.yaml"
    if default_path.stat().st_mtime != cached_mtimes.get("default", 0):
        return True
    return bool(env_path.exists() and env_path.stat().st_mtime != cached_mtimes.get("env", 0))


def hot_reload(env: str = "production", config_dir: Path | None = None) -> ConfigSchema | None:
    if check_config_changed(env, config_dir):
        _load_yaml.cache_clear()
        logger.info("Configuration rechargée (hot-reload)")
        return load_config(env, config_dir)
    return None
