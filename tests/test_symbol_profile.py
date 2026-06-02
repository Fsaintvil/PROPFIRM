"""Tests pour SymbolProfile et connaissance institutionnelle des 4 paires"""
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


class TestSymbolProfileExists:
    def test_all_four_profiles_exist(self):
        for sym in ["USDCAD", "GBPUSD", "USDCHF", "EURUSD"]:
            assert sym in PROFILES, f"Missing profile for {sym}"

    def test_get_profile(self):
        for sym in ["USDCAD", "GBPUSD", "USDCHF", "EURUSD"]:
            p = get_profile(sym)
            assert p is not None
            assert p.symbol == sym
            assert p.nickname
            assert p.avg_atr_pips > 0

    def test_unknown_symbol_returns_none(self):
        assert get_profile("XXX") is None


class TestInstitutionalKnowledge:
    def test_gbpusd_most_volatile(self):
        gbpusd = get_profile("GBPUSD")
        eurusd = get_profile("EURUSD")
        assert gbpusd.avg_atr_pips > eurusd.avg_atr_pips

    def test_eurusd_tightest_spread(self):
        eurusd = get_profile("EURUSD")
        gbpusd = get_profile("GBPUSD")
        assert eurusd.typical_spread_pts < gbpusd.typical_spread_pts

    def test_usdchf_range_bound(self):
        usdchf = get_profile("USDCHF")
        assert usdchf.trend_persistence == "medium"
        assert usdchf.adx_trend_threshold == 18.0

    def test_eurusd_technical(self):
        eurusd = get_profile("EURUSD")
        assert eurusd.respects_levels is True
        assert eurusd.base_weight >= 1.0

    def test_symbol_sessions(self):
        for sym in ["USDCAD", "GBPUSD", "USDCHF", "EURUSD"]:
            p = get_profile(sym)
            assert len(p.best_sessions) >= 2
            assert len(p.peak_hours_utc) >= 1


class TestCorrelation:
    def test_positive_correlation(self):
        assert CORRELATION_MATRIX["USDCAD"]["USDCHF"] > 0
        assert CORRELATION_MATRIX["EURUSD"]["GBPUSD"] > 0

    def test_negative_correlation(self):
        assert CORRELATION_MATRIX["USDCAD"]["EURUSD"] < 0
        assert CORRELATION_MATRIX["USDCHF"]["GBPUSD"] < 0

    def test_get_correlation(self):
        assert get_correlation("USDCAD", "USDCHF") > 0
        assert get_correlation("EURUSD", "GBPUSD") > 0
        assert get_correlation("USDCAD", "EURUSD") < 0
        assert get_correlation("XXX", "YYY") == 0.0

    def test_get_same_group(self):
        siblings = get_same_group("USDCAD")
        assert "USDCHF" in siblings  # both in USD_LONG group
        siblings2 = get_same_group("EURUSD")
        assert "GBPUSD" in siblings2  # both in USD_SHORT group

    def test_get_opposite_group(self):
        opp = get_opposite_group("USDCAD")
        assert "EURUSD" in opp or "GBPUSD" in opp
        opp2 = get_opposite_group("EURUSD")
        assert "USDCAD" in opp2 or "USDCHF" in opp2


class TestTradingHelpers:
    def test_is_session_optimal(self):
        assert is_session_optimal("USDCAD", "london_ny_overlap")
        assert not is_session_optimal("USDCAD", "asia")

    def test_unknown_symbol_session(self):
        assert is_session_optimal("XXX", "asia")  # default True

    def test_get_symbol_weight_default(self):
        w = get_symbol_weight("USDCAD")
        assert w > 0

    def test_get_symbol_weight_trending(self):
        w = get_symbol_weight("GBPUSD", "TREND_UP")
        assert w > 1.0  # trend_persistence=high + base_weight=1.05

    def test_get_symbol_weight_ranging_low_respect(self):
        # Tous les symboles respectent les niveaux actuellement
        w = get_symbol_weight("EURUSD", "RANGING")
        assert w > 0

    def test_get_atr_scaling_normal(self):
        assert get_atr_scaling("EURUSD", 85) == 1.0

    def test_get_atr_scaling_high(self):
        scaling = get_atr_scaling("EURUSD", 200)
        assert scaling < 1.0

    def test_get_atr_scaling_low(self):
        scaling = get_atr_scaling("EURUSD", 20)
        assert scaling <= 1.0

    def test_validate_trade_unknown(self):
        ok, msg = validate_trade_with_profile("XXX", "asia", "RANGING", "BUY", 50)
        assert ok and msg == ""

    def test_validate_trade_avoid_session(self):
        ok, msg = validate_trade_with_profile("USDCAD", "asia", "RANGING", "BUY", 50)
        assert not ok
        assert "defavorable" in msg

    def test_validate_trade_high_atr(self):
        ok, msg = validate_trade_with_profile("EURUSD", "london", "RANGING", "BUY", 200)
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
                    assert levels[i][0] > levels[i-1][0], f"{sym}/{regime}: thresholds not increasing"
                    assert levels[i][1] < levels[i-1][1], f"{sym}/{regime}: distances not decreasing"

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
        all_symbols = set(PROFILES.keys())
        grouped = set()
        for group in POSITION_GROUPS:
            for sym in group:
                grouped.add(sym)
        assert grouped == all_symbols, f"Missing symbols in groups: {all_symbols - grouped}"
