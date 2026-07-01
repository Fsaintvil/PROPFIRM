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

# Symboles actifs 23 Juin 2026
ACTIVE_SYMBOLS = ["XAUUSD", "BTCUSD", "US500.cash", "EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD"]


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
    def test_correlation_gold_vs_equity(self):
        assert CORRELATION_MATRIX["XAUUSD"]["US500.cash"] < 0  # 避险 vs risk-on

    def test_get_correlation(self):
        assert get_correlation("XAUUSD", "US500.cash") < 0
        assert get_correlation("XXX", "YYY") == 0.0

    def test_get_same_group(self):
        # POSITION_GROUPS restauré (25 Juin 2026) — BTCUSD dans CRYPTO
        siblings = get_same_group("BTCUSD")
        assert len(siblings) >= 1  # retourne les autres symboles du groupe CRYPTO
        assert "ETHUSD" in siblings  # BTCUSD et ETHUSD sont dans CRYPTO

    def test_get_opposite_group(self):
        # POSITION_GROUPS restauré (25 Juin 2026)
        # get_opposite_group utilise other_idx=1-i — avec 4 groupes, seul le groupe à l'index 1 a un opposé
        opp = get_opposite_group("BTCUSD")  # BTCUSD à l'index 1 (CRYPTO), other_idx=0 (FOREX_MAJORS)
        assert len(opp) >= 1  # retourne FOREX_MAJORS


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
        """POSITION_GROUPS restauré (25 Juin 2026 — Risk & Compliance)."""
        assert len(POSITION_GROUPS) >= 4  # 4 groupes: FOREX_MAJORS, CRYPTO, INDICES, COMMODITIES
        for group in POSITION_GROUPS:
            assert len(group) >= 1  # chaque groupe a au moins 1 symbole

    def test_pip_factor_by_type(self):
        """Verify pip_factor by symbol type"""
        expected = {
            "EURUSD": 10000.0,  # Forex non-JPY
            "GBPUSD": 10000.0,  # Forex non-JPY
            "USDJPY": 100.0,  # Forex JPY (1 pip = 0.01 yen)
            "AUDUSD": 10000.0,  # Forex non-JPY
            "USDCAD": 10000.0,  # Forex non-JPY
            "NZDUSD": 10000.0,  # Forex non-JPY
            "USDCHF": 10000.0,  # Forex non-JPY
            "EURJPY": 100.0,  # Forex JPY
            "GBPJPY": 100.0,  # Forex JPY
            "EURGBP": 10000.0,  # Forex non-JPY
            "AUDJPY": 100.0,  # Forex JPY
            "XAUUSD": 1.0,  # Gold (prix en unités)
            "XAGUSD": 1.0,  # Silver (prix en unités)
            "BTCUSD": 1.0,  # Crypto (prix en unités)
            "ETHUSD": 1.0,  # Crypto
            "SOLUSD": 1.0,  # Crypto
            "LNKUSD": 1.0,  # Crypto
            "BNBUSD": 1.0,  # Crypto
            "US500.cash": 1.0,  # Index (prix en unités)
            "US30.cash": 1.0,  # Index
            "US100.cash": 1.0,  # Index
            "JP225.cash": 1.0,  # Index
            "GER40.cash": 1.0,  # Index
            "UK100.cash": 1.0,  # Index
            "USOIL.cash": 1.0,  # Commodity
            "UKOIL.cash": 1.0,  # Commodity
            "NATGAS.cash": 1.0,  # Commodity
        }
        for sym, p in PROFILES.items():
            exp = expected.get(sym)
            assert exp is not None, f"{sym}: no expected pip_factor defined in test"
            assert p.pip_factor == exp, f"{sym}: expected pip_factor={exp}, got {p.pip_factor}"
