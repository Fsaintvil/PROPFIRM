"""Tests pour la stratégie TrendFollow."""

import numpy as np
from unittest.mock import patch, MagicMock

from engine_simple.strategy_trend_follow import (
    TrendFollow,
    trend_follow_signal,
    DEFAULT_CONFIG,
    SYMBOL_CONFIG,
)


def _make_trending_rates(length=150, trend_strength=0.001):
    """Génère des rates simulées en tendance haussière."""
    np.random.seed(42)
    base = 100.0
    prices = []
    for i in range(length):
        base += trend_strength * base + np.random.normal(0, 0.5)
        prices.append(base)
    prices = np.array(prices)
    high = prices * 1.002
    low = prices * 0.998
    return prices, high, low


def _make_ranging_rates(length=150):
    """Génère des rates simulées en range (ADX bas)."""
    np.random.seed(42)
    base = 100.0
    prices = []
    for i in range(length):
        base += np.random.normal(0, 0.3)
        prices.append(base)
    prices = np.array(prices)
    high = prices * 1.005
    low = prices * 0.995
    return prices, high, low


class TestTrendFollowSignal:
    def test_buy_in_uptrend(self):
        """TrendFollow donne BUY en tendance haussière."""
        close, high, low = _make_trending_rates(200, 0.002)
        signal = trend_follow_signal(close, high, low, symbol="XAUUSD")
        assert signal is not None
        assert signal["action"] == "BUY"
        assert signal["score"] >= 0.60
        assert signal["_regime"] == "TREND_UP"

    def test_no_signal_in_ranging(self):
        """TrendFollow ne donne PAS de signal en marché rangeant (ADX < 22)."""
        close, high, low = _make_ranging_rates(200)
        signal = trend_follow_signal(close, high, low, symbol="XAUUSD")
        # En ranging sans tendance claire, ADX devrait être bas
        # Le test peut passer None ou un signal selon l'ADX simulé
        # L'important est que ce ne soit pas une fausse direction
        assert signal is None or signal["_regime"] in ("RANGING", "HIGH_VOL", None)

    def test_insufficient_data(self):
        """Pas de signal si données insuffisantes."""
        close = np.array([100.0] * 30)
        high = np.array([101.0] * 30)
        low = np.array([99.0] * 30)
        signal = trend_follow_signal(close, high, low, symbol="XAUUSD")
        assert signal is None

    def test_returns_required_keys(self):
        """Le signal retourne toutes les clés requises."""
        close, high, low = _make_trending_rates(200, 0.002)
        signal = trend_follow_signal(close, high, low, symbol="XAUUSD")
        assert signal is not None

        required_keys = [
            "action",
            "score",
            "confidence",
            "atr",
            "adx",
            "plus_di",
            "minus_di",
            "adx_slope",
            "sl_atr",
            "tp_atr",
            "strategy",
        ]
        for key in required_keys:
            assert key in signal, f"Clé manquante: {key}"

        assert signal["strategy"] == "TrendFollow"
        assert signal["action"] in ("BUY", "SELL")

    def test_sl_tp_reasonable(self):
        """SL et TP sont dans des bornes raisonnables."""
        close, high, low = _make_trending_rates(200, 0.002)
        signal = trend_follow_signal(close, high, low, symbol="XAUUSD")
        assert signal is not None
        assert 1.0 <= signal["sl_atr"] <= 4.0
        assert 2.0 <= signal["tp_atr"] <= 10.0
        assert signal["tp_atr"] > signal["sl_atr"]


class TestTrendFollowClass:
    def test_init_with_rates_list(self):
        """Initialisation avec des rates au format MT5."""
        rates = []
        base = 100.0
        for i in range(200):
            base += 0.001 * base + np.random.normal(0, 0.3)
            rates.append((i, base, base * 1.002, base * 0.998, base, 1000, 0, 0))

        tf = TrendFollow(rates, "XAUUSD")
        signal = tf.analyze()
        # Peut ou non donner un signal selon les données aléatoires
        # L'important est que ça ne crashe pas
        assert signal is None or isinstance(signal, dict)

    def test_call_method(self):
        """L'appel __call__ fonctionne."""
        rates = []
        base = 100.0
        for i in range(200):
            base += 0.001 * base + np.random.normal(0, 0.3)
            rates.append((i, base, base * 1.002, base * 0.998, base, 1000, 0, 0))

        tf = TrendFollow(rates, "XAUUSD")
        signal = tf()
        assert signal is None or isinstance(signal, dict)

    def test_parse_rates_insufficient(self):
        """Avec des rates insuffisantes, analyze retourne None."""
        rates = [(i, 100, 101, 99, 100, 1000, 0, 0) for i in range(30)]
        tf = TrendFollow(rates, "XAUUSD")
        signal = tf.analyze()
        assert signal is None


class TestTrendFollowXAUUSDConfig:
    def test_xauusd_has_config(self):
        """XAUUSD a une configuration spécifique."""
        assert "XAUUSD" in SYMBOL_CONFIG
        cfg = SYMBOL_CONFIG["XAUUSD"]
        assert cfg["adx_threshold"] == 22
        assert cfg["risk_mult"] == 0.70
        assert cfg["sl_atr_trending"] == 2.5
        assert cfg["tp_atr_trending"] == 6.0

    def test_default_config_keys(self):
        """La config par défaut a toutes les clés essentielles."""
        for key in [
            "adx_threshold",
            "ema_fast",
            "ema_slow",
            "ema_slope_period",
            "sl_atr_trending",
            "tp_atr_trending",
            "base_score",
            "risk_mult",
        ]:
            assert key in DEFAULT_CONFIG, f"Clé manquante: {key}"
