"""Tests pour SymbolProfile et connaissance institutionnelle des 4 symboles actifs"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

from engine_simple.symbol_profile import (
    CORRELATION_MATRIX,
    POSITION_GROUPS,
    PROFILES,
    get_atr_scaling,
    get_correlation,
    get_opposite_group,
    get_profile,
    get_same_group,
    get_symbol_weight,
    is_session_optimal,
    validate_trade_with_profile,
)

# Symboles actifs Juin 2026
ACTIVE_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "US500.cash"]


class TestSymbolProfileExists:
    def test_all_four_profiles_exist(self):
        for sym in ACTIVE_SYMBOLS:
            assert sym in PROFILES, f"Missing profile for {sym}"

    def test_get_profile(self):
        for sym in ACTIVE_SYMBOLS:
            p = get_profile(sym)
            assert p is not None
            assert p.symbol == sym
            assert p.nickname
            assert p.avg_atr_pips > 0

    def test_unknown_symbol_returns_none(self):
        assert get_profile("XXX") is None


class TestInstitutionalKnowledge:
    def test_xauusd_more_volatile_than_us500(self):
        xau = get_profile("XAUUSD")
        us500 = get_profile("US500.cash")
        assert xau.avg_atr_pips > us500.avg_atr_pips

    def test_btcusd_highest_volatility(self):
        btc = get_profile("BTCUSD")
        for sym in ACTIVE_SYMBOLS:
            p = get_profile(sym)
            # BTC has highest avg_atr_pips (800) among all
            assert btc.avg_atr_pips >= p.avg_atr_pips or sym == "BTCUSD"

    def test_xauusd_techincal(self):
        xau = get_profile("XAUUSD")
        assert xau.respects_levels is True
        assert xau.trend_persistence == "high"
        assert xau.base_weight >= 1.0

    def test_btcusd_crypto_profile(self):
        btc = get_profile("BTCUSD")
        assert btc.respects_levels is False  # crypto peu technique
        assert btc.pip_factor == 1.0
        assert btc.spread_cost_factor >= 1.5

    def test_symbol_sessions(self):
        for sym in ACTIVE_SYMBOLS:
            p = get_profile(sym)
            assert len(p.best_sessions) >= 1
            assert len(p.peak_hours_utc) >= 1


class TestCorrelation:
    def test_positive_correlation_crypto(self):
        assert CORRELATION_MATRIX["BTCUSD"]["ETHUSD"] > 0.8  # corrélé 0.89

    def test_negative_correlation_gold_vs_equity(self):
        assert CORRELATION_MATRIX["XAUUSD"]["US500.cash"] < 0  # 避险 vs risk-on

    def test_get_correlation(self):
        assert get_correlation("BTCUSD", "ETHUSD") > 0.8
        assert get_correlation("XAUUSD", "US500.cash") < 0
        assert get_correlation("XXX", "YYY") == 0.0

    def test_get_same_group(self):
        # POSITION_GROUPS vidé volontairement (mode agressif)
        siblings = get_same_group("BTCUSD")
        assert siblings == []
        siblings2 = get_same_group("ETHUSD")
        assert siblings2 == []

    def test_get_opposite_group(self):
        # POSITION_GROUPS vidé volontairement (mode agressif)
        opp = get_opposite_group("BTCUSD")
        assert opp == []


class TestTradingHelpers:
    def test_is_session_optimal(self):
        assert is_session_optimal("BTCUSD", "asia")  # crypto 24/7
        assert is_session_optimal("XAUUSD", "london_ny_overlap")

    def test_unknown_symbol_session(self):
        assert is_session_optimal("XXX", "asia")  # default True

    def test_get_symbol_weight_default(self):
        w = get_symbol_weight("BTCUSD")
        assert w > 0

    def test_get_symbol_weight_xauusd_trending(self):
        w = get_symbol_weight("XAUUSD", "TREND_UP")
        assert w > 1.0  # trend_persistence=high + base_weight=1.10

    def test_get_atr_scaling_normal(self):
        # BTCUSD: ATR brut=800 (unités prix), avg_atr_pips=800, pip_factor=1.0 → 800/800 = 1.0
        assert get_atr_scaling("BTCUSD", 800.0) == 1.0

    def test_get_atr_scaling_high(self):
        # BTCUSD: ATR brut=1200, 1200*1.0=1200 → 1200/800 = 1.5 → scaling 0.7
        scaling = get_atr_scaling("BTCUSD", 1200.0)
        assert scaling < 1.0

    def test_get_atr_scaling_low(self):
        # BTCUSD: ATR brut=300, 300*1.0=300 → 300/800 = 0.375 → scaling 0.85
        scaling = get_atr_scaling("BTCUSD", 300.0)
        assert scaling <= 1.0

    def test_validate_trade_unknown(self):
        ok, msg = validate_trade_with_profile("XXX", "asia", "RANGING", "BUY", 50)
        assert ok and msg == ""

    def test_validate_trade_avoid_session(self):
        ok, msg = validate_trade_with_profile("XAUUSD", "asia", "RANGING", "BUY", 50)
        assert not ok
        assert "defavorable" in msg

    def test_validate_trade_high_atr(self):
        ok, msg = validate_trade_with_profile("XAUUSD", "london", "RANGING", "BUY", 200)
        assert not ok
        assert "seuil haut" in msg


class TestPerSymbolParameters:
    """Verifie que les parametres institutionnels sont coherents."""

    def test_sl_less_than_tp(self):
        for sym, p in PROFILES.items():
            assert p.sl_atr_ranging < p.tp_atr_ranging, f"{sym}: SL > TP in ranging"
            assert p.sl_atr_trending < p.tp_atr_trending, f"{sym}: SL > TP in trending"

    def test_trailing_levels_monotonic(self):
        for sym, p in PROFILES.items():
            for regime, levels in p.trailing_profile.items():
                for i in range(1, len(levels)):
                    assert levels[i][0] > levels[i - 1][0], f"{sym}/{regime}: thresholds not increasing"
                    assert levels[i][1] < levels[i - 1][1], f"{sym}/{regime}: distances not decreasing"

    def test_adx_threshold_range(self):
        for sym, p in PROFILES.items():
            assert 10 <= p.adx_trend_threshold <= 30, f"{sym}: ADX threshold {p.adx_trend_threshold} out of range"

    def test_rsi_extremes(self):
        for _sym, p in PROFILES.items():
            assert p.rsi_oversold < 50
            assert p.rsi_overbought > 50
            assert p.rsi_oversold < p.rsi_overbought

    def test_all_peak_hours_in_range(self):
        for _sym, p in PROFILES.items():
            for start, end in p.peak_hours_utc:
                assert 0 <= start < 24
                assert 0 < end <= 24
                assert start < end

    def test_position_group_completeness(self):
        """POSITION_GROUPS vidé volontairement (mode agressif)."""
        assert POSITION_GROUPS == []  # Mode agressif: pas de restriction de groupe

    def test_pip_factor_by_type(self):
        """Forex profiles should have pip_factor=10000, crypto/gold/index=1.0"""
        for sym, p in PROFILES.items():
            if sym in ("USDCAD", "GBPUSD", "USDCHF", "EURUSD"):
                assert p.pip_factor == 10000.0, f"{sym}: forex should have pip_factor=10000"
            else:
                assert p.pip_factor == 1.0, f"{sym}: non-forex should have pip_factor=1.0"
