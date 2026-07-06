"""Tests for strategy_selector.py — StrategySelector"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine_simple.strategy_selector import (
    StrategyParams,
    StrategySelector,
    REGIME_PARAMS,
    SYMBOL_ADJUSTMENTS,
    get_strategy_params,
    should_trade,
)


@pytest.fixture
def selector():
    return StrategySelector()


# ============================================================================
# StrategyParams
# ============================================================================
class TestStrategyParams:
    def test_defaults(self):
        p = StrategyParams()
        assert p.threshold_mult == 1.0
        assert p.sl_mult == 1.0
        assert p.tp_mult == 1.0
        assert p.risk_mult == 1.0
        assert p.trailing_first_lock == 1.0
        assert p.trailing_n1 == 0.50
        assert p.max_positions == 2
        assert p.min_score == 0.60
        assert p.description == ""

    def test_custom_values(self):
        p = StrategyParams(
            threshold_mult=0.5,
            sl_mult=2.0,
            tp_mult=3.0,
            risk_mult=0.8,
            max_positions=1,
            min_score=0.8,
            description="test",
        )
        assert p.threshold_mult == 0.5
        assert p.sl_mult == 2.0
        assert p.tp_mult == 3.0
        assert p.risk_mult == 0.8
        assert p.max_positions == 1
        assert p.min_score == 0.8
        assert p.description == "test"


# ============================================================================
# REGIME_PARAMS
# ============================================================================
class TestRegimeParams:
    def test_all_regimes_present(self):
        expected = [
            "STRONG_UPTREND",
            "WEAK_UPTREND",
            "RANGING",
            "WEAK_DOWNTREND",
            "STRONG_DOWNTREND",
            "HIGH_VOL",
            "LOW_VOL",
        ]
        for regime in expected:
            assert regime in REGIME_PARAMS, f"Missing regime: {regime}"

    def test_each_regime_is_strategy_params(self):
        for regime, params in REGIME_PARAMS.items():
            assert isinstance(params, StrategyParams), f"{regime} is not StrategyParams"

    def test_ranging_min_score_lowered(self):
        assert REGIME_PARAMS["RANGING"].min_score == 0.55

    def test_high_vol_max_positions_1(self):
        assert REGIME_PARAMS["HIGH_VOL"].max_positions == 1

    def test_strong_trend_threshold_85(self):
        assert REGIME_PARAMS["STRONG_UPTREND"].threshold_mult == 0.85
        assert REGIME_PARAMS["STRONG_DOWNTREND"].threshold_mult == 0.85


# ============================================================================
# SYMBOL_ADJUSTMENTS
# ============================================================================
class TestSymbolAdjustments:
    def test_expected_symbols_present(self):
        for sym in ("XAUUSD", "BTCUSD", "US500.cash"):
            assert sym in SYMBOL_ADJUSTMENTS, f"Missing symbol: {sym}"

    def test_xauusd_high_vol_risk(self):
        adj = SYMBOL_ADJUSTMENTS["XAUUSD"]["HIGH_VOL"]
        assert adj["risk_mult"] == 0.6
        assert adj["sl_mult"] == 1.5

    def test_xauusd_strong_uptrend_tp(self):
        adj = SYMBOL_ADJUSTMENTS["XAUUSD"]["STRONG_UPTREND"]
        assert adj["tp_mult"] == 1.3

    def test_btcusd_high_vol_extreme(self):
        adj = SYMBOL_ADJUSTMENTS["BTCUSD"]["HIGH_VOL"]
        assert adj["risk_mult"] == 0.5
        assert adj["sl_mult"] == 1.5

    def test_btcusd_low_vol_threshold(self):
        adj = SYMBOL_ADJUSTMENTS["BTCUSD"]["LOW_VOL"]
        assert adj["threshold_mult"] == 1.1

    def test_us500_high_vol(self):
        adj = SYMBOL_ADJUSTMENTS["US500.cash"]["HIGH_VOL"]
        assert adj["risk_mult"] == 0.6

    def test_us500_strong_uptrend(self):
        adj = SYMBOL_ADJUSTMENTS["US500.cash"]["STRONG_UPTREND"]
        assert adj["tp_mult"] == 1.2


# ============================================================================
# StrategySelector — get_params
# ============================================================================
class TestGetParams:
    def test_default_ranging(self, selector):
        params = selector.get_params("EURUSD", "RANGING")
        assert params.threshold_mult == 1.1  # RANGING base
        assert params.sl_mult == 1.2
        assert params.tp_mult == 0.9
        assert params.max_positions == 2

    def test_strong_uptrend(self, selector):
        params = selector.get_params("EURUSD", "STRONG_UPTREND")
        assert params.threshold_mult == 0.85
        assert params.tp_mult == 1.2

    def test_strong_downtrend(self, selector):
        params = selector.get_params("EURUSD", "STRONG_DOWNTREND")
        assert params.threshold_mult == 0.85
        assert params.tp_mult == 1.2

    def test_high_vol(self, selector):
        params = selector.get_params("EURUSD", "HIGH_VOL")
        assert params.max_positions == 1
        assert params.risk_mult == 0.7
        assert params.sl_mult == 1.3

    def test_low_vol(self, selector):
        params = selector.get_params("EURUSD", "LOW_VOL")
        assert params.threshold_mult == 0.9
        assert params.sl_mult == 0.9
        assert params.tp_mult == 0.9

    def test_weak_uptrend(self, selector):
        params = selector.get_params("EURUSD", "WEAK_UPTREND")
        assert params.threshold_mult == 0.95
        assert params.risk_mult == 0.9
        assert params.min_score == 0.60

    def test_weak_downtrend(self, selector):
        params = selector.get_params("EURUSD", "WEAK_DOWNTREND")
        assert params.threshold_mult == 0.95
        assert params.risk_mult == 0.9
        assert params.min_score == 0.60

    def test_unknown_regime_defaults_to_ranging(self, selector):
        params = selector.get_params("EURUSD", "UNKNOWN_REGIME")
        assert params.threshold_mult == 1.1  # RANGING base
        assert params.sl_mult == 1.2

    @pytest.mark.parametrize(
        "symbol,regime,key,expected",
        [
            ("XAUUSD", "HIGH_VOL", "risk_mult", 0.6),
            ("XAUUSD", "HIGH_VOL", "sl_mult", 1.5),
            ("XAUUSD", "STRONG_UPTREND", "tp_mult", 1.3),
            ("BTCUSD", "HIGH_VOL", "risk_mult", 0.5),
            ("BTCUSD", "HIGH_VOL", "sl_mult", 1.5),
            ("BTCUSD", "LOW_VOL", "threshold_mult", 1.1),
            ("US500.cash", "HIGH_VOL", "risk_mult", 0.6),
            ("US500.cash", "STRONG_UPTREND", "tp_mult", 1.2),
        ],
    )
    def test_symbol_specific_adjustments(self, selector, symbol, regime, key, expected):
        params = selector.get_params(symbol, regime)
        assert getattr(params, key) == expected

    def test_symbol_no_adjustment_uses_base(self, selector):
        """Symbole sans ajustement → reprend les valeurs du régime."""
        params = selector.get_params("EURJPY", "HIGH_VOL")
        assert params.risk_mult == 0.7  # HIGH_VOL base, pas d'override EURJPY
        assert params.sl_mult == 1.3

    def test_adx_gt_30_more_aggressive(self, selector):
        params = selector.get_params("EURUSD", "RANGING", adx=35)
        assert params.risk_mult == pytest.approx(1.0 * 1.1)  # 1.1
        assert params.tp_mult == pytest.approx(0.9 * 1.1)  # 0.99

    def test_adx_lt_18_more_conservative(self, selector):
        params = selector.get_params("EURUSD", "RANGING", adx=15)
        assert params.risk_mult == pytest.approx(1.0 * 0.8)  # 0.8
        assert params.min_score == pytest.approx(0.55 + 0.05)  # 0.6

    def test_adx_boundary_18(self, selector):
        """ADX exactement 18 → pas d'ajustement."""
        params = selector.get_params("EURUSD", "RANGING", adx=18)
        assert params.risk_mult == 1.0
        assert params.min_score == 0.55

    def test_adx_boundary_30(self, selector):
        """ADX exactement 30 → pas d'ajustement."""
        params = selector.get_params("EURUSD", "RANGING", adx=30)
        assert params.risk_mult == 1.0
        assert params.tp_mult == 0.9

    def test_atr_pct_gt_1_reduces_risk(self, selector):
        params = selector.get_params("EURUSD", "RANGING", atr_pct=1.5)
        assert params.risk_mult == pytest.approx(1.0 * 0.8)  # 0.8
        assert params.sl_mult == pytest.approx(1.2 * 1.2)  # 1.44

    def test_atr_pct_boundary_1(self, selector):
        """ATR% exactement 1.0 → pas d'ajustement."""
        params = selector.get_params("EURUSD", "RANGING", atr_pct=1.0)
        assert params.risk_mult == 1.0
        assert params.sl_mult == 1.2

    def test_combined_adx_and_atr_adjustments(self, selector):
        """ADX>30 + ATR>1.0 → ajustements cumulés."""
        params = selector.get_params("EURUSD", "RANGING", adx=35, atr_pct=1.5)
        # ATR: risk *= 0.8, sl *= 1.2
        # ADX: risk *= 1.1, tp *= 1.1
        # Combined: risk = 1.0 * 0.8 * 1.1 = 0.88
        assert params.risk_mult == pytest.approx(0.88)
        assert params.sl_mult == pytest.approx(1.2 * 1.2)  # 1.44
        assert params.tp_mult == pytest.approx(0.9 * 1.1)  # 0.99

    def test_symbol_adjustment_takes_priority(self, selector):
        """Symbol override écrase la valeur de base du régime."""
        params = selector.get_params("XAUUSD", "HIGH_VOL")
        assert params.risk_mult == 0.6  # Override XAUUSD, pas 0.7
        assert params.sl_mult == 1.5  # Override XAUUSD, pas 1.3

    def test_symbol_adjustment_with_adx_tuning(self, selector):
        """Ajustement symbole + ADX sont cumulatifs."""
        params = selector.get_params("XAUUSD", "HIGH_VOL", adx=35)
        # Base HIGH_VOL risk=0.7, override XAUUSD → 0.6, ADX>30 → 0.6 * 1.1 = 0.66
        assert params.risk_mult == pytest.approx(0.66)
        assert params.sl_mult == 1.5  # Override XAUUSD, pas d'ajustement ADX


# ============================================================================
# StrategySelector — get_regime_for_signal
# ============================================================================
class TestGetRegimeForSignal:
    def test_buy_against_downtrend_returns_ranging(self, selector):
        assert selector.get_regime_for_signal("TREND_DOWN", "BUY") == "RANGING"

    def test_sell_against_uptrend_returns_ranging(self, selector):
        assert selector.get_regime_for_signal("TREND_UP", "SELL") == "RANGING"

    def test_buy_with_uptrend_unchanged(self, selector):
        assert selector.get_regime_for_signal("TREND_UP", "BUY") == "TREND_UP"

    def test_sell_with_downtrend_unchanged(self, selector):
        assert selector.get_regime_for_signal("TREND_DOWN", "SELL") == "TREND_DOWN"

    def test_ranging_regime_unchanged(self, selector):
        assert selector.get_regime_for_signal("RANGING", "BUY") == "RANGING"
        assert selector.get_regime_for_signal("RANGING", "SELL") == "RANGING"

    def test_high_vol_unchanged(self, selector):
        assert selector.get_regime_for_signal("HIGH_VOL", "BUY") == "HIGH_VOL"
        assert selector.get_regime_for_signal("HIGH_VOL", "SELL") == "HIGH_VOL"

    def test_buy_against_weak_downtrend_unchanged(self, selector):
        """Seul TREND_DOWN exact déclenche le ranging, pas WEAK_DOWNTREND."""
        assert selector.get_regime_for_signal("WEAK_DOWNTREND", "BUY") == "WEAK_DOWNTREND"


# ============================================================================
# StrategySelector — should_trade
# ============================================================================
class TestShouldTrade:
    def test_should_trade_ok(self, selector):
        should, reason = selector.should_trade("EURUSD", "RANGING", 0.70)
        assert should is True
        assert reason == "OK"

    def test_score_below_min(self, selector):
        should, reason = selector.should_trade("EURUSD", "RANGING", 0.50)
        assert should is False
        assert "Score" in reason
        assert "min" in reason

    def test_high_vol_adx_gt_35(self, selector):
        should, reason = selector.should_trade("EURUSD", "HIGH_VOL", 0.70, adx=40)
        assert should is False
        assert "ADX" in reason
        assert "35" in reason

    def test_high_vol_adx_boundary_35(self, selector):
        """ADX exactement 35 en HIGH_VOL → autorisé."""
        should, reason = selector.should_trade("EURUSD", "HIGH_VOL", 0.70, adx=35)
        assert should is True
        assert reason == "OK"

    def test_score_exactly_min(self, selector):
        """Score exactement égal au minimum → autorisé."""
        should, reason = selector.should_trade("EURUSD", "RANGING", 0.55)
        assert should is True

    def test_min_score_depends_on_regime(self, selector):
        """RANGING a min_score=0.55, STRONG_UPTREND=0.55, HIGH_VOL=0.60."""
        s1, _ = selector.should_trade("EURUSD", "RANGING", 0.55)
        assert s1 is True
        s2, _ = selector.should_trade("EURUSD", "HIGH_VOL", 0.55)
        assert s2 is False  # HIGH_VOL min_score = 0.60
        s3, _ = selector.should_trade("EURUSD", "HIGH_VOL", 0.60)
        assert s3 is True

    def test_adx_fine_tuning_affects_min_score(self, selector):
        """ADX<18 augmente min_score de 0.05."""
        should, _ = selector.should_trade("EURUSD", "RANGING", 0.59, adx=15)
        assert should is False  # min_score = 0.55 + 0.05 = 0.60 > 0.59


# ============================================================================
# Convenience functions
# ============================================================================
class TestConvenienceFunctions:
    def test_get_strategy_params(self):
        params = get_strategy_params("EURUSD", "RANGING")
        assert isinstance(params, StrategyParams)
        assert params.threshold_mult == 1.1

    def test_get_strategy_params_with_overrides(self):
        params = get_strategy_params("XAUUSD", "HIGH_VOL", adx=25, atr_pct=0.5)
        assert params.risk_mult == 0.6  # XAUUSD override

    def test_should_trade_convenience(self):
        should, reason = should_trade("EURUSD", "RANGING", 0.70)
        assert should is True
        assert reason == "OK"

    def test_should_trade_convenience_blocked(self):
        should, reason = should_trade("EURUSD", "RANGING", 0.40)
        assert should is False
