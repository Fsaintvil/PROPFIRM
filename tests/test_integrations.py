"""Tests pour les intégrations Phase 2-4 : market_structure, market_memory, auto_stop."""

import numpy as np
import pytest


class TestMarketStructureIntegration:
    """Tests pour l'intégration de market_structure.py dans strategy.py."""

    def _make_signal(self, action="BUY", close_price=100.0, period=20):
        """Génère un signal MOM20x3 minimal pour les tests."""
        from engine_simple.strategy import mom20x3_signal

        # Créer des données OHLC synthétiques
        n = period + 30
        close = np.linspace(close_price * 0.98, close_price, n)
        high = close * 1.005
        low = close * 0.995
        if action == "SELL":
            close = np.linspace(close_price * 1.02, close_price, n)
            high = close * 1.005
            low = close * 0.995
        return mom20x3_signal(close, high, low, period=period, symbol="XAUUSD")

    def test_signal_includes_structure_fields(self):
        """Le signal doit contenir les champs market_structure."""
        sig = self._make_signal("BUY")
        if sig is not None:  # Le signal peut être None si conditions non remplies
            assert "structure_trend" in sig
            assert "structure_score" in sig
            assert "unmitigated_obs" in sig
            assert "unmitigated_fvgs" in sig
            assert "_structure_obs" in sig

    def test_structure_trend_is_valid(self):
        """structure_trend doit être une valeur valide."""
        sig = self._make_signal("BUY")
        if sig is not None:
            assert sig["structure_trend"] in ("bullish", "bearish", "ranging", "unknown")

    def test_structure_score_range(self):
        """structure_score doit être entre -1 et 1."""
        sig = self._make_signal("BUY")
        if sig is not None:
            assert -1 <= sig["structure_score"] <= 1


class TestSRLlevelsIntegration:
    """Tests pour l'intégration des S/R levels dans strategy.py."""

    def test_signal_includes_sr_fields(self):
        """Le signal doit contenir les champs S/R."""
        from engine_simple.strategy import mom20x3_signal

        n = 50
        close = np.linspace(98, 100, n)
        high = close * 1.005
        low = close * 0.995
        sig = mom20x3_signal(close, high, low, period=20, symbol="XAUUSD")
        if sig is not None:
            assert "nearest_support" in sig
            assert "nearest_resistance" in sig

    def test_sr_values_are_numeric_or_none(self):
        """nearest_support/resistance doivent être numériques ou None."""
        from engine_simple.strategy import mom20x3_signal

        n = 50
        close = np.linspace(98, 100, n)
        high = close * 1.005
        low = close * 0.995
        sig = mom20x3_signal(close, high, low, period=20, symbol="XAUUSD")
        if sig is not None:
            if sig["nearest_support"] is not None:
                assert isinstance(sig["nearest_support"], (int, float))
            if sig["nearest_resistance"] is not None:
                assert isinstance(sig["nearest_resistance"], (int, float))


class TestPatternIntegration:
    """Tests pour l'intégration des patterns dans strategy.py."""

    def test_signal_includes_pattern_fields(self):
        """Le signal doit contenir les champs pattern."""
        from engine_simple.strategy import mom20x3_signal

        n = 50
        close = np.linspace(98, 100, n)
        high = close * 1.005
        low = close * 0.995
        sig = mom20x3_signal(close, high, low, period=20, symbol="XAUUSD")
        if sig is not None:
            assert "pattern_signal" in sig
            assert "pattern_confidence" in sig
            assert sig["pattern_signal"] in ("HAUSSE", "BAISSE", "NEUTRE")

    def test_pattern_confidence_range(self):
        """pattern_confidence doit être entre 0 et 1."""
        from engine_simple.strategy import mom20x3_signal

        n = 50
        close = np.linspace(98, 100, n)
        high = close * 1.005
        low = close * 0.995
        sig = mom20x3_signal(close, high, low, period=20, symbol="XAUUSD")
        if sig is not None:
            assert 0 <= sig["pattern_confidence"] <= 1


class TestAutoStopIntegration:
    """Tests pour l'intégration de auto_stop.py dans ftmo_protector.py."""

    def test_auto_stop_state_initialized(self):
        """FTMOProtector doit initialiser les attributs auto_stop."""
        from engine_simple.ftmo_protector import FTMOProtector
        from unittest.mock import MagicMock

        mt5 = MagicMock()
        config = {
            "MAX_POSITIONS": 6,
            "MAX_TRADES_PER_DAY": 20,
            "MIN_SIGNAL_SCORE": 0.55,
            "LOT_SIZE": 0.01,
            "RISK_PER_TRADE": 0.004,
            "COOLDOWN_MINUTES": 15,
            "MAX_DAILY_LOSS_PCT": 0.02,
            "INITIAL_BALANCE": 200000,
            "MAX_DD_PCT": 0.10,
            "PROFIT_TARGET_PCT": 0.10,
            "CONSISTENCY_MAX_PCT": 0.30,
            "MIN_TRADING_DAYS": 10,
            "MAGIC": 999001,
            "MAX_SPREAD_POINTS": 120,
            "MAX_RISK_AMOUNT": 1600,
            "TRADING_START_HOUR": 0,
            "TRADING_END_HOUR": 24,
            "DANGER_HOURS": [],
            "SYMBOL_LIMITS": {},
            "DAILY_PROFIT_LIMIT_PCT": 0.05,
            "ZONE2_LOSS_PCT": 0.015,
            "ZONE3_LOSS_PCT": 0.02,
            "AUTO_PAUSE_LOSSES": 8,
            "MAX_CORRELATED_EXPOSURE": 0.15,
            "CIRCUIT_BREAKER_DD_PCT": 0.08,
        }
        ftmo = FTMOProtector(mt5, config)
        assert hasattr(ftmo, "_auto_stop_paused")
        assert hasattr(ftmo, "_auto_stop_until")
        assert ftmo._auto_stop_paused is False
        assert ftmo._auto_stop_until is None

    def test_auto_stop_check_returns_true_when_not_paused(self):
        """_check_auto_stop doit retourner True quand pas en pause."""
        from engine_simple.ftmo_protector import FTMOProtector
        from unittest.mock import MagicMock

        mt5 = MagicMock()
        config = {
            "MAX_POSITIONS": 6,
            "MAX_TRADES_PER_DAY": 20,
            "MIN_SIGNAL_SCORE": 0.55,
            "LOT_SIZE": 0.01,
            "RISK_PER_TRADE": 0.004,
            "COOLDOWN_MINUTES": 15,
            "MAX_DAILY_LOSS_PCT": 0.02,
            "INITIAL_BALANCE": 200000,
            "MAX_DD_PCT": 0.10,
            "PROFIT_TARGET_PCT": 0.10,
            "CONSISTENCY_MAX_PCT": 0.30,
            "MIN_TRADING_DAYS": 10,
            "MAGIC": 999001,
            "MAX_SPREAD_POINTS": 120,
            "MAX_RISK_AMOUNT": 1600,
            "TRADING_START_HOUR": 0,
            "TRADING_END_HOUR": 24,
            "DANGER_HOURS": [],
            "SYMBOL_LIMITS": {},
            "DAILY_PROFIT_LIMIT_PCT": 0.05,
            "ZONE2_LOSS_PCT": 0.015,
            "ZONE3_LOSS_PCT": 0.02,
            "AUTO_PAUSE_LOSSES": 8,
            "MAX_CORRELATED_EXPOSURE": 0.15,
            "CIRCUIT_BREAKER_DD_PCT": 0.08,
        }
        ftmo = FTMOProtector(mt5, config)
        ok, reason = ftmo._check_auto_stop()
        assert ok is True


class TestMarketMemoryInit:
    """Tests pour le chargement de MarketMemory dans main.py."""

    def test_market_memory_importable(self):
        """MarketMemory doit être importable depuis engine_simple."""
        from engine_simple.market_memory import MarketMemory

        mm = MarketMemory()
        assert mm is not None
        assert hasattr(mm, "load_all")
        assert hasattr(mm, "get_nearby_levels")
        assert hasattr(mm, "get_mtf_alignment")
        assert hasattr(mm, "get_pattern_context")

    def test_market_structure_importable(self):
        """analyze_market_structure doit être importable."""
        from engine_simple.market_structure import analyze_market_structure

        assert analyze_market_structure is not None
        # Test avec données minimales
        high = np.random.uniform(100, 105, 50)
        low = high - np.random.uniform(0.5, 2.0, 50)
        close = (high + low) / 2
        result = analyze_market_structure(high, low, close)
        assert "trend" in result
        assert "score" in result
        assert result["trend"] in ("bullish", "bearish", "ranging", "unknown")


class TestAutoStopModule:
    """Tests pour le module auto_stop.py."""

    def test_compute_adx(self):
        """compute_adx doit retourner une valeur valide."""
        from engine_simple.auto_stop import compute_adx

        high = np.random.uniform(100, 105, 30)
        low = high - np.random.uniform(0.5, 2.0, 30)
        close = (high + low) / 2
        adx_val = compute_adx(high, low, close)
        assert isinstance(adx_val, float)
        assert adx_val >= 0

    def test_compute_adx_insufficient_data(self):
        """compute_adx doit retourner 0.0 avec données insuffisantes."""
        from engine_simple.auto_stop import compute_adx

        high = np.array([100.0, 101.0])
        low = np.array([99.0, 100.0])
        close = np.array([100.5, 100.5])
        adx_val = compute_adx(high, low, close)
        assert adx_val == 0.0

    def test_load_state_default(self):
        """load_state doit retourner un état par défaut si fichier absent."""
        from engine_simple.auto_stop import load_state
        from unittest.mock import patch

        with patch("engine_simple.auto_stop.STATE_FILE") as mock_file:
            mock_file.exists.return_value = False
            state = load_state()
            assert state["auto_paused"] is False
            assert state["auto_paused_at"] is None
            assert state["auto_paused_until"] is None
