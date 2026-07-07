"""Tests pour le Strategy Registry."""

from engine_simple.strategy_registry import (
    get_strategy_for,
    set_strategy_for,
    get_all_strategies,
    validate_registry,
    SYMBOL_STRATEGY_MAP,
    STRATEGY_CLASSES,
)


class TestGetStrategyFor:
    def test_default_is_mom20x3(self):
        """Un symbole non listé retourne MOM20x3."""
        assert get_strategy_for("UNKNOWN_SYMBOL") == "MOM20x3"

    def test_xauusd_is_trend_follow(self):
        """XAUUSD a été activé en TrendFollow."""
        assert get_strategy_for("XAUUSD") == "TrendFollow"

    def test_eurusd_is_mom20x3(self):
        """EURUSD reste sur MOM20x3."""
        assert get_strategy_for("EURUSD") == "MOM20x3"

    def test_usdjpy_is_mom20x3(self):
        """USDJPY reste sur MOM20x3."""
        assert get_strategy_for("USDJPY") == "MOM20x3"

    def test_all_active_symbols_have_strategy(self):
        """Tous les symboles actifs ont une stratégie valide."""
        errors = validate_registry()
        assert len(errors) == 0, f"Erreurs registry: {errors}"


class TestSetStrategyFor:
    def test_set_and_get(self):
        """set_strategy_for modifie la stratégie à chaud."""
        old = get_strategy_for("TEST_SET")
        set_strategy_for("TEST_SET", "TrendFollow")
        assert get_strategy_for("TEST_SET") == "TrendFollow"
        # Restore
        set_strategy_for("TEST_SET", old)

    def test_set_invalid_does_not_crash(self):
        """set_strategy_for avec un nom invalide ne crashe pas."""
        set_strategy_for("TEST_INVALID", "NonExistent")
        assert get_strategy_for("TEST_INVALID") == "NonExistent"
        # Clean up
        SYMBOL_STRATEGY_MAP.pop("TEST_INVALID", None)


class TestValidateRegistry:
    def test_all_strategies_exist(self):
        """Toutes les stratégies référencées existent dans STRATEGY_CLASSES."""
        for symbol, strategy in SYMBOL_STRATEGY_MAP.items():
            assert strategy in STRATEGY_CLASSES, f"{symbol}: stratégie '{strategy}' inconnue"

    def test_valid_returns_empty(self):
        """validate_registry retourne une liste vide si tout est OK."""
        errors = validate_registry()
        assert isinstance(errors, list)
        # Sauvegarder l'état
        saved = dict(SYMBOL_STRATEGY_MAP)

        # Ajouter volontairement une stratégie invalide
        SYMBOL_STRATEGY_MAP["_TEST_VALIDATE_"] = "NonExistent"
        errors = validate_registry()
        assert len(errors) > 0
        assert any("NonExistent" in e for e in errors)

        # Restore
        del SYMBOL_STRATEGY_MAP["_TEST_VALIDATE_"]


class TestGetAllStrategies:
    def test_returns_dict(self):
        strategies = get_all_strategies()
        assert isinstance(strategies, dict)
        assert "XAUUSD" in strategies
        assert "EURUSD" in strategies
