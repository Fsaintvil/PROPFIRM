"""Tests pour MeanReversion — stratégie RSI en marché RANGING (ADX<18).

Teste :
1. _generate_mr_signal() : déclenchement RSI < 30 / > 70
2. Bypass Phase 2 (ADX) : MR ne doit pas être filtré par l'ADX threshold
3. Bypass Phase 6 (Strategy Selector) : MR ne doit pas être rejeté
4. Bypass Phase 7 (Volume Profile) : MR ne doit pas être modifié
5. Bypass Phase 7b (RVOL/CMF) : MR ne doit pas être pénalisé
6. Bypass Phase 7c (OBV) : MR ne doit pas être pénalisé
7. Bypass Phase 9 (MTF) : MR ne doit pas être filtré
8. Pipeline complet : MR doit produire un SignalResult
9. MOM20x3 prime sur MR
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from engine_simple.signal_pipeline import SignalPipeline, SignalResult


# ── Mock factories (pas des fixtures pytest pour éviter les conflits) ──


def _make_mock_mt5():
    """Mock MT5 basique avec des prix neutres."""
    m = MagicMock()
    n = 100
    base_prices = 100.0 + np.random.RandomState(42).randn(n) * 0.5

    def _get_rates(symbol, tf, count=100):
        k = min(count, n)
        return [
            (
                i,
                float(base_prices[i] - 0.1),
                float(base_prices[i] + 0.5),
                float(base_prices[i] - 0.5),
                float(base_prices[i]),
                1000,
                10,
                1000,
            )
            for i in range(k)
        ]

    m.get_rates.side_effect = _get_rates
    m.get_tick.return_value = MagicMock(ask=100.1, bid=100.0)
    m.get_symbol_info.return_value = MagicMock(point=0.01, digits=2, trade_stops_level=0, volume_step=0.01)
    return m


def _make_mock_ftmo():
    return MagicMock()


def _make_mock_strategy_selector():
    m = MagicMock()
    m.get_regime_for_signal.return_value = "RANGING"
    m.get_params.return_value = MagicMock(to_dict=lambda: {"desc": "RANGING"})
    m.should_trade.return_value = (True, "OK")
    return m


def _make_mock_news_filter():
    m = MagicMock()
    m.is_news_blocked.return_value = (False, "")
    return m


def _make_mock_volume_profile():
    m = MagicMock()
    m.analyze.return_value = MagicMock(poc=None, vah=None, val=None)
    return m


def _make_mock_mtf_confirm():
    m = MagicMock()
    m.confirm.return_value = (True, 1.0)
    return m


def _make_mock_risk_manager():
    m = MagicMock()
    m.pre_trade.return_value = (True, [])
    return m


def _make_mock_adaptive():
    m = MagicMock()
    m.learner.get_params.return_value = {"thresh": 2.5, "risk_mult": 0.75}
    return m


def _make_mock_config():
    class MockConfig:
        MIN_SIGNAL_SCORE = 0.40
        MAX_POSITIONS = 10
        SYMBOL_TIMEFRAMES = {"XAUUSD": "H4", "BTCUSD": "H1", "SOLUSD": "H1"}
        SYMBOL_LIMITS = {
            "XAUUSD": {"risk_mult": 1.0, "adx_thresh": 20},
            "BTCUSD": {"risk_mult": 0.65, "adx_thresh": 20},
        }
        LOT_SIZE = 0.01
        RISK_PER_TRADE = 0.004
        ROBOT_MAGIC = 999001

    return MockConfig()


def _make_pipeline(mock_mt5=None, mock_config=None, **overrides):
    """Helper pour créer un pipeline avec les mocks par défaut."""
    if mock_mt5 is None:
        mock_mt5 = _make_mock_mt5()
    if mock_config is None:
        mock_config = _make_mock_config()
    defaults = {
        "mt5": mock_mt5,
        "ftmo": _make_mock_ftmo(),
        "adaptive": _make_mock_adaptive(),
        "news_filter": _make_mock_news_filter(),
        "strategy_selector": _make_mock_strategy_selector(),
        "volume_profile": _make_mock_volume_profile(),
        "mtf_confirm": _make_mock_mtf_confirm(),
        "risk_manager": _make_mock_risk_manager(),
        "config": mock_config,
        "symbol_limits": mock_config.SYMBOL_LIMITS,
        "symbol_timeframes": mock_config.SYMBOL_TIMEFRAMES,
    }
    defaults.update(overrides)
    return SignalPipeline(**defaults)


# ── Tests _generate_mr_signal ────────────────────────────────────────
# Note: _generate_mr_signal appelle ind_rsi() et ind_adx() de
# engine_simple.indicators. On patch ces deux fonctions pour contrôler
# précisément les valeurs retournées.


class TestMRGenerateSignal:
    """Tests de la méthode _generate_mr_signal."""

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_oversold_buy_signal(self, mock_adx, mock_rsi):
        """RSI < 30 et ADX < 18 → signal BUY."""
        # Simuler RSI=[... , 25.0] et ADX=(12.0, 15.0, 10.0)
        rsi_arr = np.full(100, 50.0)
        rsi_arr[-1] = 25.0
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (12.0, 15.0, 10.0)  # (adx, +di, -di)

        pipeline = _make_pipeline()
        signal = pipeline._generate_mr_signal("SOLUSD")

        assert signal is not None, "MR devrait générer un signal BUY (oversold)"
        assert signal["action"] == "BUY"
        assert signal["_strategy"] == "MR"
        assert signal["rsi"] == 25.0
        assert signal["adx"] == 12.0
        assert signal["score"] == 0.60
        assert signal["confidence"] == 0.50
        assert signal["risk_mult"] == 0.75
        assert signal["sl_atr"] == 1.0
        assert signal["tp_atr"] == 1.5

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_overbought_sell_signal(self, mock_adx, mock_rsi):
        """RSI > 70 et ADX < 18 → signal SELL."""
        rsi_arr = np.full(100, 50.0)
        rsi_arr[-1] = 75.0
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (15.0, 20.0, 8.0)

        pipeline = _make_pipeline()
        signal = pipeline._generate_mr_signal("SOLUSD")

        assert signal is not None, "MR devrait générer un signal SELL (overbought)"
        assert signal["action"] == "SELL"
        assert signal["_strategy"] == "MR"
        assert signal["rsi"] == 75.0
        assert signal["adx"] == 15.0
        assert signal["score"] == 0.60

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_neutral_no_signal(self, mock_adx, mock_rsi):
        """RSI entre 30 et 70 → pas de signal MR."""
        rsi_arr = np.full(100, 50.0)  # RSI neutre
        rsi_arr[-1] = 50.0
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (12.0, 15.0, 10.0)

        pipeline = _make_pipeline()
        signal = pipeline._generate_mr_signal("SOLUSD")
        assert signal is None, "MR ne devrait pas générer de signal (RSI neutre)"

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_trending_no_signal(self, mock_adx, mock_rsi):
        """ADX >= 18 → pas de signal MR (marché pas rangeant)."""
        rsi_arr = np.full(100, 25.0)  # RSI oversold
        rsi_arr[-1] = 25.0
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (25.0, 30.0, 10.0)  # ADX=25 → trending

        pipeline = _make_pipeline()
        signal = pipeline._generate_mr_signal("SOLUSD")
        assert signal is None, "MR ne devrait pas générer de signal (ADX >= 18)"


# ── Tests des bypass MR dans les phases ──────────────────────────────


class TestMRBypassPhases:
    """Vérifie que les signaux MR bypassent correctement chaque phase."""

    def _make_mr_signal(self):
        """Crée un signal MR factice pour tester les bypass."""
        return {
            "action": "BUY",
            "score": 0.60,
            "confidence": 0.50,
            "atr": 1.0,
            "sl_atr": 1.0,
            "tp_atr": 1.5,
            "risk_mult": 0.75,
            "entry_price": 100.0,
            "_regime": "RANGING",
            "_strategy": "MR",
            "strategy": "MeanReversion",
            "details": "MeanReversion_H1",
            "timeframe": "H1",
            "symbol": "SOLUSD",
            "adx": 12.0,
            "rsi": 25.0,
        }

    def _make_mom_signal(self):
        """Crée un signal MOM20x3 factice."""
        return {
            "action": "BUY",
            "score": 0.85,
            "confidence": 0.80,
            "atr": 0.01,
            "_regime": "TREND_UP",
            "adx": 25,
            "adx_slope": 5,
            "plus_di": 30,
            "minus_di": 15,
        }

    def test_phase2_adx_bypass(self):
        """Phase 2 (ADX) ne doit PAS filtrer un signal MR (même ADX bas)."""
        pipeline = _make_pipeline()
        signal = self._make_mr_signal()
        result = pipeline._phase2_adx_filter("SOLUSD", signal, 1, {})
        assert result, "Phase 2 ADX devrait bypass MR"

    def test_phase6_selector_bypass(self):
        """Phase 6 (Strategy Selector) ne doit PAS filtrer un signal MR."""
        pipeline = _make_pipeline()
        signal = self._make_mr_signal()
        result = pipeline._phase6_strategy_selector("SOLUSD", signal)
        assert result, "Phase 6 Strategy Selector devrait bypass MR"
        assert signal.get("strat_params", {}).get("description", "").startswith("MeanReversion")

    def test_phase7_volume_profile_bypass(self):
        """Phase 7 (Volume Profile) ne doit PAS modifier un signal MR."""
        pipeline = _make_pipeline()
        signal = self._make_mr_signal()
        original_score = signal["score"]
        result = pipeline._phase7_volume_profile("SOLUSD", signal)
        assert result, "Phase 7 Volume Profile devrait passer"
        assert signal["score"] == original_score, "VP ne devrait pas modifier le score MR"
        assert signal.get("vp_boost") == "bypass_MR"

    def test_phase7b_rvol_cmf_bypass(self):
        """Phase 7b (RVOL/CMF) ne doit PAS modifier un signal MR."""
        pipeline = _make_pipeline()
        signal = self._make_mr_signal()
        original_score = signal["score"]
        result = pipeline._phase7b_rvol_cmf("SOLUSD", signal)
        assert result, "Phase 7b RVOL/CMF devrait passer"
        assert signal["score"] == original_score, "RVOL/CMF ne devrait pas modifier le score MR"
        assert signal.get("rvol_adj") == 1.0
        assert signal.get("cmf_adj") == 1.0
        assert signal.get("rvol_note") == "bypass_MR"

    def test_phase7c_obv_bypass(self):
        """Phase 7c (OBV Divergence) ne doit PAS modifier un signal MR."""
        pipeline = _make_pipeline()
        signal = self._make_mr_signal()
        original_score = signal["score"]
        pipeline._phase7c_obv_divergence("SOLUSD", signal)
        assert signal["score"] == original_score, "OBV ne devrait pas modifier le score MR"
        assert signal.get("obv_div") == "bypass_MR"

    def test_phase9_mtf_bypass(self):
        """Phase 9 (MTF) ne doit PAS filtrer un signal MR."""
        pipeline = _make_pipeline()
        signal = self._make_mr_signal()
        result = pipeline._phase9_mtf_confirm("SOLUSD", signal)
        assert result, "Phase 9 MTF devrait bypass MR"


# ── Tests du pipeline complet ────────────────────────────────────────


class TestMRPipelineFull:
    """Tests du flux complet process() avec signaux MR."""

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_mr_oversold_flow(self, mock_adx, mock_rsi):
        """Un MR oversold doit passer tout le pipeline et retourner un SignalResult."""
        rsi_arr = np.full(100, 25.0)
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (12.0, 15.0, 10.0)

        pipeline = _make_pipeline()
        pipeline._phase1_mom20x3 = MagicMock(return_value=None)

        result = pipeline.process(
            symbol="SOLUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"SOLUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None, "MR oversold devrait produire un SignalResult"
        assert isinstance(result, SignalResult)
        assert result.signal["_strategy"] == "MR"
        assert result.signal["action"] == "BUY"
        assert result.signal["rsi"] < 30

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_mr_overbought_flow(self, mock_adx, mock_rsi):
        """Un MR overbought doit passer tout le pipeline."""
        rsi_arr = np.full(100, 75.0)
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (15.0, 20.0, 8.0)

        pipeline = _make_pipeline()
        pipeline._phase1_mom20x3 = MagicMock(return_value=None)

        result = pipeline.process(
            symbol="SOLUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"SOLUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None, "MR overbought devrait produire un SignalResult"
        assert isinstance(result, SignalResult)
        assert result.signal["_strategy"] == "MR"
        assert result.signal["action"] == "SELL"

    @patch("engine_simple.indicators.rsi")
    def test_mr_neutral_flow(self, mock_rsi):
        """RSI neutre → pas de MR, pas de MOM → None."""
        rsi_arr = np.full(100, 50.0)
        mock_rsi.return_value = rsi_arr

        pipeline = _make_pipeline()
        pipeline._phase1_mom20x3 = MagicMock(return_value=None)

        result = pipeline.process(
            symbol="SOLUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"SOLUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is None, "RSI neutre + pas de MOM → pas de trade"

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_mr_trending_flow(self, mock_adx, mock_rsi):
        """ADX >= 18 → pas de MR (marché pas rangeant)."""
        rsi_arr = np.full(100, 25.0)
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (25.0, 30.0, 10.0)  # ADX=25 → trending

        pipeline = _make_pipeline()
        pipeline._phase1_mom20x3 = MagicMock(return_value=None)

        result = pipeline.process(
            symbol="SOLUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"SOLUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is None, "Marché pas rangeant → pas de MR"

    @patch("engine_simple.indicators.rsi")
    @patch("engine_simple.indicators.adx")
    def test_mr_does_not_override_mom(self, mock_adx, mock_rsi):
        """Si MOM20x3 donne un signal, MR ne doit PAS le remplacer."""
        rsi_arr = np.full(100, 25.0)
        mock_rsi.return_value = rsi_arr
        mock_adx.return_value = (12.0, 15.0, 10.0)

        pipeline = _make_pipeline()
        # MOM retourne un signal valide
        with patch("engine_simple.strategy.MOM20x3") as mock_mom_cls:
            mock_mom = MagicMock()
            mock_mom.analyze.return_value = {
                "action": "SELL",
                "score": 0.85,
                "confidence": 0.80,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_DOWN",
            }
            mock_mom_cls.return_value = mock_mom

            result = pipeline.process(
                symbol="SOLUSD",
                cycle_count=1,
                degraded_symbols={},
                sym_dir_counts={},
                sym_total_counts={},
                config_limits={"SOLUSD": 4},
                last_signals={},
                log_throttle={},
            )
        # MOM doit prendre le pas sur MR
        assert result is not None
        assert result.signal["action"] == "SELL", "MOM devrait primer sur MR"
        assert result.signal.get("_strategy") != "MR"


# ── Tests de régression ──────────────────────────────────────────────


class TestMRRegression:
    """Tests de non-régression : les signaux existants ne sont pas affectés."""

    def test_mom_still_works(self):
        """MOM20x3 doit toujours fonctionner normalement (patch non appliqué)."""
        # Ce test vérifie que le pipeline normal passe sans aucun patch
        # → utilise le mock MT5 par défaut qui génère des prix pour MOM
        pipeline = _make_pipeline()
        with patch("engine_simple.strategy.MOM20x3") as mock_mom_cls:
            mock_mom = MagicMock()
            mock_mom.analyze.return_value = {
                "action": "BUY",
                "score": 0.85,
                "confidence": 0.80,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_UP",
                "plus_di": 30,
                "minus_di": 15,
            }
            mock_mom_cls.return_value = mock_mom

            result = pipeline.process(
                symbol="EURUSD",
                cycle_count=1,
                degraded_symbols={},
                sym_dir_counts={},
                sym_total_counts={},
                config_limits={"EURUSD": 4},
                last_signals={},
                log_throttle={},
            )
        assert result is not None, "MOM20x3 doit fonctionner normalement"
        assert result.signal["action"] == "BUY"
        assert result.signal.get("_strategy") != "MR"
