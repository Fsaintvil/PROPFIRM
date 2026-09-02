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
    # 🔧 14 Aout 2026 - XAUUSD + PAIRES PRIMAIRES ACTIVES (decision utilisateur)
    # 13 symboles: 5 repositionnés (PF edge démontré) + XAUUSD + 7 paires FOREX majeures.
    # Garde-fous: BUY-only, risk_mult 1.0, min_score 0.65.
    # Backup: config/backup_default_20260814_avant_xauusd_forex.yaml
    assert len(cfg.trading.symbols) == 13
    assert "US100.cash" in cfg.trading.symbols  # 🔧 PF 1.20, 6/6 annees positives
    assert "US30.cash" in cfg.trading.symbols  # 🔧 PF 1.14, 8/8 annees positives
    assert "JP225.cash" in cfg.trading.symbols  # 🔧 PF 1.23, 6/6 annees positives
    assert "SOLUSD" in cfg.trading.symbols  # 🔧 PF 1.25, spread live 3 pts
    assert "BTCUSD" in cfg.trading.symbols  # 🔧 PF 1.18, groupe CRYPTO independant (13/08)
    # 🔧 14 Aout 2026 - REACTIVATION utilisateur (XAUUSD + paires primaires)
    assert "XAUUSD" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (decision utilisateur)
    assert "EURUSD" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert "USDJPY" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert "USDCAD" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert "AUDUSD" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert "NZDUSD" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert "USDCHF" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert "GBPUSD" in cfg.trading.symbols  # 🔧 REACTIVE 14 Aout 2026 (paire primaire)
    assert cfg.risk.per_trade_pct == 0.004  # défaut YAML (production override → 0.003)
    assert cfg.risk.max_dd_pct == 0.10
    assert cfg.risk.min_rr_ratio == 2.0  # conservé


def test_load_production_config():
    cfg = load_config("production")
    assert cfg.robot.magic == 999001
    assert len(cfg.trading.symbols) >= 2


def test_as_flat_dict():
    cfg = load_config("default")
    flat = cfg.as_flat_dict()
    assert flat["ROBOT_MAGIC"] == 999001
    assert flat["RISK_PER_TRADE_PCT"] == 0.004  # défaut YAML (production override → 0.003)
    assert flat["TRADING_MAX_POSITIONS"] == 30  # défaut YAML (production override → 8)
    assert flat["RISK_MAX_DD_PCT"] == 0.10


def test_symbol_limits_defaults():
    cfg = load_config("default")
    assert "XAUUSD" in cfg.symbol_limits
    assert "BTCUSD" in cfg.symbol_limits
    assert cfg.symbol_limits["XAUUSD"].max_lot == 0.03  # 🔧 FIX 30 Août 2026: 0.04→0.03 (XAUUSD p=0.0008, significativement perdant)
    assert cfg.symbol_limits["XAUUSD"].min_lot == 0.01
    # 🔧 1 Sept 2026: XAUUSD risk_mult 1.0→0.8 (WR 25% justifie réduction)
    assert (
        cfg.symbol_limits["XAUUSD"].risk_mult == 0.8
    )  # 🔧 1 Sept 2026: WR 25% → réduction exposure
    assert (
        cfg.symbol_limits["XAUUSD"].min_score == 0.80
    )  # 🔧 31 Aout 2026: 0.75→0.80. XAUUSD restrictif (WR 25%, PF 1.09)
    assert cfg.symbol_limits["XAUUSD"].allow_buys is True
    assert cfg.symbol_limits["XAUUSD"].allow_shorts is True  # 🔧 FIX 28 Août 2026: SELL sélectif réactivé


def test_usdcad_max_lot_preuve():
    """🐛 FIX 10 Août 2026: USDCAD max_lot 0.15 → 0.05 (plafond mode preuve strict).
    La config PIC 23 Juin (0.15) violait le veto risk-compliance du mode preuve
    (lignes 43-45 de default.yaml: 'Lots réduits 0.05 max').
    🔧 17 Août 2026: ×1.10 → 0.06 (décision utilisateur: augmentation lot 10%).
    🔧 19 Août 2026 (Council): 0.06 → 0.04 — USDCAD perdant structurel en période
    GR (PF 0.034 sur 3 trades), aligné sur le pattern AUDUSD (réduction des perdants)."""
    cfg = load_config("default")
    assert "USDCAD" in cfg.symbol_limits
    assert cfg.symbol_limits["USDCAD"].max_lot == 0.04
    assert cfg.symbol_limits["USDCAD"].allow_shorts is True  # 🔧 FIX 28 Août 2026: SELL sélectif réactivé


def test_symbol_limits_new_portfolio():
    """Le portefeuille PIC 23 Juin (7 symboles)."""
    from config.schema import load_config

    cfg = load_config("default")
    btc = cfg.symbol_limits.get("BTCUSD", {})
    assert btc.risk_mult == 1.0  # 🔓 DÉBLOQUÉ 13 Août 2026 (décision utilisateur — groupe CRYPTO indépendant)
    assert btc.allow_buys is True
    assert btc.allow_shorts is True  # 🔧 FIX 28 Août 2026: SELL sélectif réactivé (marché baissier)
    assert btc.max_lot == 0.12  # 🔧 2 Sept 2026: 0.08→0.12 (BTCUSD seul edge prouvé, maximiser collecte)
    # 🔧 28 Août 2026: min_score unifié à 0.65 pour tous les symboles
    assert btc.min_score == 0.65


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
    assert cfg.RISK_PER_TRADE == 0.003  # 🔧 MODE PREUVE 06 Aout 2026 (production override → 0.30%)
    assert cfg.MAX_ORDERS_PER_MINUTE == 6  # CONFIG PIC 23 Juin 2026
    assert cfg.__version__ == "4.1.0"
    assert cfg.MIN_SIGNAL_SCORE == 0.65  # 🔧 13 Août 2026: 0.70→0.65 (décision utilisateur — +~30% de signaux)


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
