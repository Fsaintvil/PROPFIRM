"""Tests pour le système de configuration YAML + Pydantic"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

import pytest
import yaml
from pydantic import ValidationError

from config.schema import (
    RiskConfig,
    RobotConfig,
    SymbolLimit,
    TradingConfig,
    check_config_changed,
    hot_reload,
    load_config,
)


def test_load_default_config():
    cfg = load_config("default")
    assert cfg.robot.magic == 999001
    # 8 actifs actifs (XAUUSD, BTCUSD, EURUSD, USDJPY, GBPUSD, AUDUSD, USDCAD, US500.cash)
    assert len(cfg.trading.symbols) == 8
    assert "XAUUSD" in cfg.trading.symbols
    assert "EURUSD" in cfg.trading.symbols
    assert "US500.cash" in cfg.trading.symbols  # réactivé 23 Juin H4
    assert "ETHUSD" not in cfg.trading.symbols  # désactivé 19 Juin
    assert cfg.risk.per_trade_pct == 0.004  # ↓ 0.5%→0.4% (22 Juin 2026, Supreme Council)
    assert cfg.risk.max_dd_pct == 0.10
    assert cfg.risk.min_rr_ratio == 2.0  # RR≥2.0 (validé backtest)


def test_load_production_config():
    cfg = load_config("production")
    assert cfg.robot.magic == 999001
    assert len(cfg.trading.symbols) >= 2


def test_as_flat_dict():
    cfg = load_config("default")
    flat = cfg.as_flat_dict()
    assert flat["ROBOT_MAGIC"] == 999001
    assert flat["RISK_PER_TRADE_PCT"] == 0.004  # ↓ 0.5%→0.4% (22 Juin 2026, Supreme Council)
    assert flat["TRADING_MAX_POSITIONS"] == 40  # 23 Juin: capacité multi-positions 8 symboles
    assert flat["RISK_MAX_DD_PCT"] == 0.10


def test_symbol_limits_defaults():
    cfg = load_config("default")
    assert "XAUUSD" in cfg.symbol_limits
    assert "BTCUSD" in cfg.symbol_limits
    assert cfg.symbol_limits["XAUUSD"].max_lot == 0.05  # ↓ 0.10→0.05 (23 Juin, contrainte utilisateur)
    assert cfg.symbol_limits["XAUUSD"].min_lot == 0.01
    assert cfg.symbol_limits["XAUUSD"].risk_mult == 1.10  # ↑ 10% (19 Juin 2026) WR 77.8% live


def test_symbol_limits_new_portfolio():
    """Le nouveau portefeuille 3 symboles production."""
    from config.schema import load_config

    cfg = load_config("default")
    btc = cfg.symbol_limits.get("BTCUSD", {})
    assert btc.risk_mult == 0.30  # RÉACTIVÉ 23 Juin (WR Phase 3 32.3%, risk prudent)
    assert btc.allow_buys is True
    assert btc.allow_shorts is True
    assert btc.max_lot == 0.05  # ↑ 0.03→0.05 (23 Juin, lot min pour tous)
    assert btc.min_score == 0.70  # ↑ 0.60→0.70 (23 Juin, min global 0.70)


def test_env_interpolation():
    with patch.dict(os.environ, {"MT5_LOGIN": "12345", "MT5_PASSWORD": "secret"}):
        cfg = load_config("default")
        assert cfg.secrets.mt5_login == "12345"
        assert cfg.secrets.mt5_password == "secret"


def test_mt5_login_int():
    load_config("default")
    with patch.dict(os.environ, {"MT5_LOGIN": "67890"}):
        cfg2 = load_config("default")
        assert cfg2.secrets.mt5_login_int == 67890


def test_symbol_limit_validation():
    lim = SymbolLimit(max_lot=1.0, risk_mult=2.0, max_spread_points=100)
    assert lim.max_lot == 1.0
    assert lim.risk_mult == 2.0
    assert lim.max_spread_points == 100


def test_symbol_limit_clamps_negative():
    with pytest.raises(ValidationError, match="max_lot"):
        SymbolLimit(max_lot=-1, risk_mult=5.0, max_spread_points=600)


def test_trading_end_after_start():
    with pytest.raises(ValidationError, match="doit etre >"):
        TradingConfig(trading_start_hour=10, trading_end_hour=5)


def test_robot_cycle_range():
    with pytest.raises(ValidationError, match="cycle_seconds"):
        RobotConfig(cycle_seconds=200)
    # lower boundary: 5 is valid
    cfg = RobotConfig(cycle_seconds=5)
    assert cfg.cycle_seconds == 5


def test_risk_per_trade_range():
    with pytest.raises(ValidationError, match="per_trade_pct"):
        RiskConfig(per_trade_pct=0.05)  # > 0.02
    # lower boundary: 0.001 is valid
    cfg = RiskConfig(per_trade_pct=0.001)
    assert cfg.per_trade_pct == 0.001


def test_config_simple_compat():
    import config_simple as cfg

    assert cfg.ROBOT_MAGIC == 999001
    assert cfg.RISK_PER_TRADE == 0.006  # ↑ 0.4%→0.6% (23 Juin 2026, override production)
    assert cfg.MAX_ORDERS_PER_MINUTE == 8  # 1 trade/min/symbole + marge (8 symboles)
    assert cfg.__version__ == "4.1.0"


def test_config_reload():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        default = config_dir / "default.yaml"
        default.write_text(
            yaml.safe_dump(
                {
                    "robot": {"magic": 999001, "cycle_seconds": 15, "version": "2.5.0"},
                    "trading": {"symbols": ["EURUSD"]},
                    "risk": {"per_trade_pct": 0.005},
                    "signal": {},
                }
            )
        )
        cfg = load_config("default", config_dir=config_dir)
        assert cfg.robot.magic == 999001
        assert cfg.trading.symbols == ["EURUSD"]
        assert cfg.risk.per_trade_pct == 0.005


def test_hot_reload_detects_change():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        default = config_dir / "default.yaml"
        default.write_text(
            yaml.safe_dump(
                {
                    "robot": {"magic": 999001, "cycle_seconds": 15, "version": "2.5.0"},
                    "trading": {"symbols": ["EURUSD"]},
                    "risk": {"per_trade_pct": 0.004},
                    "signal": {},
                }
            )
        )
        load_config("default", config_dir=config_dir)
        assert not check_config_changed("default", config_dir=config_dir)
        # Modify the file (force mtime change)
        import time

        time.sleep(0.05)
        default.write_text(
            yaml.safe_dump(
                {
                    "robot": {"magic": 999002, "cycle_seconds": 15, "version": "2.5.1"},
                    "trading": {"symbols": ["EURUSD"]},
                    "risk": {"per_trade_pct": 0.005},
                    "signal": {},
                }
            )
        )
        assert check_config_changed("default", config_dir=config_dir)
        cfg2 = hot_reload("default", config_dir=config_dir)
        assert cfg2 is not None
        assert cfg2.robot.magic == 999002
        assert cfg2.risk.per_trade_pct == 0.005


def test_config_fallback_on_error():
    """Si le YAML est corrompu, le fallback de config_simple doit marcher"""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        bad_yaml = config_dir / "default.yaml"
        bad_yaml.write_text("{broken: yaml: unclosed")
        from config.schema import _load_yaml

        with pytest.raises(yaml.YAMLError):
            _load_yaml(bad_yaml)
    # config_simple defaults should still be accessible
    import config_simple

    assert config_simple.ROBOT_MAGIC == 999001
