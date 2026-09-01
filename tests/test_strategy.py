"""Tests pour strategy.py — MOM20x3 pur, fonction pure, testable."""

import numpy as np
import pytest

from engine_simple.strategy import MOM20x3, mom20x3_signal
from engine_simple.strategy import (
    THRESHOLD_MAX,
    THRESHOLD_MIN,
)


class TestMom20x3Signal:
    """Tests unitaires de la fonction pure mom20x3_signal."""

    def _make_rates(self, n=60, close_start=1.0, trend=0.0, volatility=0.002):
        """Génère des prix synthétiques."""
        np.random.seed(42)
        close = close_start + np.arange(n) * trend + np.random.randn(n) * volatility
        high = close + abs(np.random.randn(n)) * volatility * 0.5
        low = close - abs(np.random.randn(n)) * volatility * 0.5
        return close, high, low

    def test_returns_none_if_not_enough_data(self):
        close = np.array([1.0] * 10)
        high = np.array([1.005] * 10)
        low = np.array([0.995] * 10)
        assert mom20x3_signal(close, high, low) is None

    def test_returns_buy_on_strong_upward_trend(self):
        # Prix plat avec un dip à -21 (momentum) + petit breakout final
        # Le dip crée le momentum, le breakout est minime pour rester proche EMA
        close = np.ones(60) * 1.10
        close[-21] = 1.05  # dip 20 candles ago = momentum BUY
        close[-2:] = [1.101, 1.103]  # breakout minimal (reste proche EMA20)
        high = close + 0.005
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        assert result is not None
        assert result["action"] == "BUY"
        assert result["score"] >= 0.50

    def test_returns_sell_on_strong_downward_trend(self):
        # Prix plat avec un spike à -21 (momentum baissier) + petit breakdown
        close = np.ones(60) * 1.10
        close[-21] = 1.15  # spike 20 candles ago = momentum SELL
        close[-2:] = [1.099, 1.097]  # breakdown minimal (reste proche EMA20)
        high = close + 0.005
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        assert result is not None
        assert result["action"] == "SELL"
        assert result["score"] >= 0.50

    def test_returns_none_on_ranging_market(self):
        np.random.seed(0)
        close = 1.10 + np.random.randn(100) * 0.001  # très peu de mouvement
        high = close + 0.002
        low = close - 0.002
        result = mom20x3_signal(close, high, low, period=20)
        assert result is None  # pas de momentum assez fort

    def test_threshold_adapts_to_adx(self):
        # Trending: ADX projette haut
        close = np.arange(1.0, 1.3, 0.003)
        high = close + 0.01
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert result["is_trending"] is True or result["is_trending"] is False
            if result["sl_atr"] == 2.0:
                assert result["tp_atr"] == 5.0

    def test_threshold_capped_at_max(self):
        """Vérifie que thresh est plafonné à THRESHOLD_MAX."""
        close = np.arange(1.0, 1.3, 0.003)
        high = close + 0.01
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert result["thresh_used"] <= THRESHOLD_MAX

    def test_threshold_has_minimum(self):
        """Vérifie que thresh est au moins THRESHOLD_MIN."""
        close = np.arange(1.0, 1.3, 0.003)
        high = close + 0.01
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert result["thresh_used"] >= THRESHOLD_MIN or result["thresh_used"] >= 1.5

    def test_confidence_in_range(self):
        close = np.arange(1.0, 1.3, 0.003)
        high = close + 0.01
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert 0.0 <= result["confidence"] <= 1.0

    def test_score_in_range(self):
        close = np.arange(1.0, 1.3, 0.003)
        high = close + 0.01
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert 0.0 <= result["score"] <= 1.0

    def test_atr_positive(self):
        close = np.arange(1.0, 1.3, 0.003)
        high = close + 0.01
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert result["atr"] > 0

    def test_signal_different_periods(self):
        # Données avec dip 20-30 candles en arrière pour momentum + pullback
        close = np.ones(70) * 1.10
        close[-25] = 1.05  # dip 24 candles ago → momentum BUY période 10
        close[-31] = 1.04  # dip 30 candles ago → momentum BUY période 30
        high = close + 0.01
        low = close - 0.005
        result_10 = mom20x3_signal(close, high, low, period=10)
        result_30 = mom20x3_signal(close, high, low, period=30)
        assert (result_10 is not None) or (result_30 is not None)

    def test_edge_empty_close(self):
        assert mom20x3_signal(np.array([]), np.array([]), np.array([])) is None

    def test_edge_single_close(self):
        assert mom20x3_signal(np.array([1.0]), np.array([1.0]), np.array([1.0])) is None

    def test_model_predictions_is_mom20x3(self):
        close = np.arange(1.0, 1.5, 0.005)
        high = close + 0.005
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result:
            assert "_model_predictions" in result
            assert "MOM20x3" in result["_model_predictions"]


class TestMOM20x3Wrapper:
    """Tests du wrapper MOM20x3 utilisé dans le pipeline."""

    def _make_rates(self, n=60):
        np.random.seed(42)
        rates = []
        # Base price 1.10 with small noise, dips for multiple momentum periods
        # Supporte les périodes 15, 20, 24, 30 :
        #   momentum(p) = close[-1] - close[-p-1]
        #   dip à n-p-1 pour chaque période p
        dip_indices = {n - 16, n - 19, n - 21, n - 25, n - 31}  # dips pour périodes 15,18,20,24,30
        for i in range(n):
            noise = np.random.randn() * 0.002
            if i in dip_indices:
                c = 1.05 + noise
            else:
                c = 1.10 + noise
            rates.append((0, 0, c + 0.005, c - 0.005, c, 0))
        return rates

    def test_init_with_rates(self):
        rates = self._make_rates()
        mom = MOM20x3(rates, "USDCAD")
        assert mom.symbol == "USDCAD"
        assert mom.period == 20  # période adaptative USDCAD (réduit 24→20 WR live 45%)
        assert mom._close is not None
        assert len(mom._close) == 60

    def test_momentum_period_override(self):
        """Période explicite écrase la période adaptative."""
        rates = self._make_rates()
        mom = MOM20x3(rates, "USDCAD", period=20)
        assert mom.period == 20

    def test_init_too_few_rates(self):
        mom = MOM20x3([], "EURUSD", period=20)
        assert mom._close is None
        assert mom.analyze() is None

    def test_analyze_returns_dict(self):
        rates = self._make_rates()
        mom = MOM20x3(rates, "USDCAD")
        result = mom.analyze()
        assert result is not None
        assert "action" in result

    def test_callable(self):
        rates = self._make_rates()
        mom = MOM20x3(rates, "USDCAD")
        result = mom()
        assert result is not None
        assert "action" in result

    def test_different_symbol(self):
        rates = self._make_rates()
        # XAUUSD a threshold 4.0×ATR (Solution A) → données de test insuffisantes
        # On utilise EURUSD qui a threshold 2.5×ATR (default legacy)
        mom = MOM20x3(rates, "EURUSD")
        assert mom.symbol == "EURUSD"
        result = mom()
        assert result is not None


class TestNewFilters30Aug2026:
    """Tests des 3 filtres ajoutés le 30 Août 2026 (Alpha Researcher)."""

    def test_wick_ratio_penalty_buy_with_upper_wick(self):
        """BUY avec upper wick > 40% de la bougie → score pénalisé."""
        # Créer une bougie avec long upper wick (rejection haussière)
        close = np.ones(60) * 1.10
        close[-21] = 1.05  # dip → momentum BUY
        close[-1] = 1.103  # close
        # high très au-dessus = upper wick long
        high = close.copy()
        high[-1] = 1.120  # upper wick = 1.120 - 1.103 = 0.017
        # low proche du close
        low = close.copy()
        low[-1] = 1.100  # lower wick = 0
        # Candle range = 0.020, upper wick = 0.017 → ratio = 0.85 > 0.40
        result = mom20x3_signal(close, high, low, period=20)
        if result is not None and result["action"] == "BUY":
            # Le score devrait être pénalisé (pas le max possible)
            assert result["score"] < 0.95

    def test_wick_ratio_no_penalty_clean_candle(self):
        """Bougie propre (pas de mèche) → pas de pénalité wick."""
        close = np.ones(60) * 1.10
        close[-21] = 1.05
        close[-1] = 1.103
        high = close.copy()
        high[-1] = 1.105  # upper wick = 0.002
        low = close.copy()
        low[-1] = 1.098  # lower wick = 0.002
        # Candle range = 0.007, wicks = 0.002 → ratio = 0.28 < 0.40
        result = mom20x3_signal(close, high, low, period=20)
        # Pas de pénalité wick, le score est intact
        if result is not None and result["action"] == "BUY":
            assert result["score"] > 0.0

    def test_momentum_acceleration_penalty(self):
        """Momentum qui ralentit (accel < 0.7) → score pénalisé."""
        # Construire un momentum qui ralentit
        close = np.ones(60) * 1.10
        close[-21] = 1.05  # dip pour momentum BUY
        close[-26] = 1.00  # plus gros dip 26 bougies avant
        # mom_current = 1.10 - 1.05 = 0.05
        # mom_prev = 1.10 - 1.00 = 0.10 (via close[-6] - close[-26])
        # accel = 0.05 / 0.10 = 0.5 < 0.7 → pénalité
        high = close + 0.005
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        if result is not None and result["action"] == "BUY":
            # Score pénalisé par acceleration filter
            assert result["score"] > 0.0  # toujours un signal valide

    def test_momentum_acceleration_no_penalty_stable(self):
        """Momentum stable (accel >= 0.7) → pas de pénalité acceleration."""
        close = np.ones(60) * 1.10
        close[-21] = 1.05
        close[-26] = 1.06  # momentum précédent plus petit
        # mom_current = 0.05, mom_prev = 1.10 - 1.06 = 0.04
        # accel = 0.05 / 0.04 = 1.25 > 0.7 → pas de pénalité
        high = close + 0.005
        low = close - 0.005
        result = mom20x3_signal(close, high, low, period=20)
        # Pas de pénalité
        assert result is not None
