"""Tests pour signal_pipeline.py — pipeline de filtrage multi-couches (P1)."""

from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pandas as pd
import pytest

from engine_simple.signal_pipeline import SignalPipeline, SignalResult


# ── SignalResult ─────────────────────────────────────────────────────────


class TestSignalResult:
    """Tests du dataclass SignalResult."""

    def test_creation(self):
        result = SignalResult(symbol="XAUUSD", signal={"action": "BUY"}, score=0.85)
        assert result.symbol == "XAUUSD"
        assert result.signal["action"] == "BUY"
        assert result.score == 0.85

    def test_default_score(self):
        result = SignalResult(symbol="BTCUSD", signal={"action": "SELL"}, score=0.0)
        assert result.score == 0.0
        assert result.symbol == "BTCUSD"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_mt5():
    m = MagicMock()

    # get_rates returns a list of tuples (time, open, high, low, close, volume)
    def _get_rates(symbol, tf, count=100):
        n = min(count, 100)
        base = 1.0 if "USD" in symbol else 50000.0
        return [
            (i, base + i * 0.001, base + i * 0.001 + 0.005, base + i * 0.001 - 0.005, base + i * 0.001, 1000, 10, 1000)
            for i in range(n)
        ]

    m.get_rates.side_effect = _get_rates
    m.get_tick.return_value = MagicMock(ask=1.105, bid=1.104)
    m.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, trade_stops_level=0, volume_step=0.01)
    return m


@pytest.fixture
def mock_ftmo():
    return MagicMock()


@pytest.fixture
def mock_mt5():
    m = MagicMock()
    # Génère un prix avec un momentum detectable par MOM20x3
    # On crée un « dip » à l'index -21 pour créer un momentum BUY
    np.random.seed(42)
    n = 100
    base = 1.10
    prices = np.ones(n) * base
    prices[-21] = base - 0.05  # dip il y a 20 bougies → momentum BUY
    prices[-2:] = [base + 0.001, base + 0.003]  # petit breakout récent
    noise = np.random.randn(n) * 0.0005
    prices = prices + noise

    def _get_rates(symbol, tf, count=100):
        k = min(count, n)
        return [
            (i, float(prices[i]), float(prices[i] + 0.005), float(prices[i] - 0.005), float(prices[i]), 1000, 10, 1000)
            for i in range(k)
        ]

    m.get_rates.side_effect = _get_rates
    m.get_tick.return_value = MagicMock(ask=1.105, bid=1.104)
    m.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, trade_stops_level=0, volume_step=0.01)
    return m


@pytest.fixture
def mock_strategy_selector():
    m = MagicMock()
    m.get_regime_for_signal.return_value = "RANGING"
    m.get_params.return_value = MagicMock(to_dict=lambda: {"sl_atr": 1.5, "tp_atr": 4.0})
    m.should_trade.return_value = (True, "OK")
    return m


@pytest.fixture
def mock_news_filter():
    m = MagicMock()
    m.is_news_blocked.return_value = (False, "")
    return m


@pytest.fixture
def mock_volume_profile():
    m = MagicMock()
    m.analyze.return_value = MagicMock(poc=None, vah=None, val=None)
    return m


@pytest.fixture
def mock_mtf_confirm():
    m = MagicMock()
    m.confirm.return_value = (True, 1.0)
    return m


@pytest.fixture
def mock_market_profile():
    m = MagicMock()
    m.analyze.return_value = {"score_adj": 1.0, "session_type": "normal"}
    return m


@pytest.fixture
def mock_risk_manager():
    m = MagicMock()
    m.pre_trade.return_value = (True, [])
    return m


@pytest.fixture
def mock_adaptive():
    m = MagicMock()
    m.learner.get_params.return_value = {"thresh": 2.5, "risk_mult": 0.75}
    return m


@pytest.fixture
def mock_config():
    """Mock du module config avec les attributs nécessaires."""

    class MockConfig:
        MIN_SIGNAL_SCORE = 0.40
        MAX_POSITIONS = 10
        SYMBOL_TIMEFRAMES = {"XAUUSD": "H4", "BTCUSD": "H1"}
        SYMBOL_LIMITS = {
            "XAUUSD": {"risk_mult": 1.0, "adx_thresh": 20},
            "BTCUSD": {"risk_mult": 0.65, "adx_thresh": 20},
        }
        LOT_SIZE = 0.01
        RISK_PER_TRADE = 0.004
        ROBOT_MAGIC = 999001
        # 🔧 14 Aout 2026: filtre M15 désactivé (alignement backtest validé)
        ENABLE_M15_CONFIRMATION = False

    return MockConfig()


@pytest.fixture
def pipeline(
    mock_mt5,
    mock_ftmo,
    mock_adaptive,
    mock_news_filter,
    mock_strategy_selector,
    mock_volume_profile,
    mock_mtf_confirm,
    mock_risk_manager,
    mock_config,
):
    """Crée une instance de SignalPipeline avec tous les mocks."""
    p = SignalPipeline(
        mt5=mock_mt5,
        ftmo=mock_ftmo,
        adaptive=mock_adaptive,
        news_filter=mock_news_filter,
        strategy_selector=mock_strategy_selector,
        volume_profile=mock_volume_profile,
        mtf_confirm=mock_mtf_confirm,
        risk_manager=mock_risk_manager,
        config=mock_config,
        symbol_limits=mock_config.SYMBOL_LIMITS,
        symbol_timeframes=mock_config.SYMBOL_TIMEFRAMES,
    )
    # Le filtre _check_m15_confirmation nécessite des données MT5 réelles (bougie M15 fermée).
    # Dans les tests unitaires, les données mockées ont close≈open → M15 toujours SELL.
    # On mocke la méthode pour qu'elle retourne True (confirmé) et simule les champs requis.
    p._check_m15_confirmation = MagicMock(return_value=True)
    return p


# ── Pipeline Init ────────────────────────────────────────────────────────


class TestSignalPipelineInit:
    """Tests d'initialisation du pipeline."""

    def test_init_stores_dependencies(self, pipeline):
        assert pipeline.mt5 is not None
        assert pipeline.ftmo is not None
        assert pipeline.adaptive is not None
        assert pipeline.cfg is not None
        assert pipeline._adaptive_params == {}

    def test_init_stores_all_deps(self, mock_ftmo):
        """Le pipeline s'initialise proprement."""
        assert mock_ftmo is not None


# ── Process (full pipeline) ──────────────────────────────────────────────


# ── Helper: mock MOM20x3 signal ────────────────────────────────────────


def _make_mock_mom20x3(analyze_return=None):
    """Crée un mock de MOM20x3 qui retourne un signal contrôlé."""
    if analyze_return is None:
        analyze_return = {
            "action": "BUY",
            "score": 0.85,
            "confidence": 0.80,
            "adx": 25,
            "atr": 0.01,
            "_regime": "TREND_UP",
            "plus_di": 30,
            "minus_di": 15,
            "adx_slope": 5,
        }
    instance = MagicMock()
    instance.analyze.return_value = analyze_return
    return instance


# ── Process (full pipeline) ──────────────────────────────────────────────


class TestPipelineProcess:
    """Tests du flux complet process()."""

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_returns_signal_result_on_success(self, mock_mom, pipeline):
        mock_mom.return_value = _make_mock_mom20x3()
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
        assert result is not None
        assert isinstance(result, SignalResult)
        assert result.symbol == "EURUSD"
        assert "action" in result.signal
        assert result.signal["max_per_symbol"] > 0

    # 🔧 14 Aout 2026 (REGRESSION): M15 désactivé par défaut — le backtest validé
    # (PF 1.18-1.25) n'inclut PAS le filtre M15. Il bloquait 100% des BUY en marché
    # baissier (bougie M15 rouge pendant que H1 signale BUY) → RÈGLE D'OR inatteignable.
    @patch("engine_simple.strategy.MOM20x3")
    def test_process_m15_disabled_by_default(self, mock_mom, pipeline):
        """Le filtre M15 ne doit PAS bloquer le signal quand ENABLE_M15_CONFIRMATION=False."""
        assert pipeline._enable_m15 is False
        # Même si la confirmation M15 renverrait False (bougie opposée), le signal passe.
        pipeline._check_m15_confirmation = MagicMock(return_value=False)
        mock_mom.return_value = _make_mock_mom20x3()
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
        assert result is not None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_m15_blocked_when_enabled(self, mock_mom, pipeline):
        """Quand ENABLE_M15_CONFIRMATION=True, un M15 opposé bloque bien le signal."""
        pipeline._enable_m15 = True
        pipeline._check_m15_confirmation = MagicMock(return_value=False)
        mock_mom.return_value = _make_mock_mom20x3()
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
        assert result is None

    def test_process_none_on_pre_trade_fail(self, pipeline, mock_risk_manager):
        mock_risk_manager.pre_trade.return_value = (
            False,
            [{"rule": "danger_hours", "pass": False, "reason": "Danger hours block"}],
        )
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
        assert result is None

    def test_process_none_on_mom20x3_fail(self, pipeline, mock_mt5):
        mock_mt5.get_rates.return_value = None
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
        assert result is None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_sets_degraded_flag(self, mock_mom, pipeline):
        mock_mom.return_value = _make_mock_mom20x3()
        result = pipeline.process(
            symbol="EURUSD",
            cycle_count=1,
            degraded_symbols={"EURUSD": 0},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"EURUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        assert result.signal.get("_degraded") is True

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_adx_filter_bypass_high_score(self, mock_mom, pipeline):
        """Score >= 0.80 doit bypasser le filtre ADX si ADX >= 15."""
        mock_mom.return_value = _make_mock_mom20x3()
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
        assert result is not None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_respects_position_direction_limit(self, mock_mom, pipeline):
        """Si la limite de direction est atteinte, process() retourne None."""
        mock_mom.return_value = _make_mock_mom20x3()
        result = pipeline.process(
            symbol="EURUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={("EURUSD", 0): 4},  # 4 BUY déjà
            sym_total_counts={"EURUSD": 4},
            config_limits={"EURUSD": 3},
            last_signals={},
            log_throttle={},
        )
        assert result is None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_respects_total_position_limit(self, mock_mom, pipeline):
        mock_mom.return_value = _make_mock_mom20x3()
        result = pipeline.process(
            symbol="EURUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={"EURUSD": 20},
            config_limits={"EURUSD": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_sets_max_per_symbol_by_confidence(self, mock_mom, pipeline):
        """conf=0.95, score=0.95 → central bypass (max_per_symbol=4)"""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.95,
                "confidence": 0.95,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_UP",
                "plus_di": 30,
                "minus_di": 15,
                "adx_slope": 5,
            }
        )
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
        assert result is not None
        # 🔧 FIX 22 Juillet 2026: score=0.95 + raw_mom=0.95 → central bypass → max=4
        assert result.signal["max_per_symbol"] == 4

    def test_process_handles_exception_gracefully(self, pipeline, mock_risk_manager):
        """Une exception dans pre_trade doit remonter (non catchée)."""
        mock_risk_manager.pre_trade.side_effect = RuntimeError("MT5 down")
        with pytest.raises(RuntimeError):
            pipeline.process(
                symbol="XAUUSD",
                cycle_count=1,
                degraded_symbols={},
                sym_dir_counts={},
                sym_total_counts={},
                config_limits={"XAUUSD": 4},
                last_signals={},
                log_throttle={},
            )


# ── Phase 1: MOM20x3 ─────────────────────────────────────────────────────


class TestPhase1MOM20x3:
    """Tests de la génération de signal MOM20x3."""

    def test_returns_none_on_insufficient_rates(self, pipeline, mock_mt5):
        pipeline._rates_cache.clear()
        # Forcer le mock à retourner 10 barres (side_effect effacé)
        mock_mt5.get_rates.side_effect = None
        mock_mt5.get_rates.return_value = [(i, 1.0, 1.01, 0.99, 1.0, 100, 5, 100) for i in range(10)]
        # Utiliser un symbole hors SYMBOL_CONFIG pour éviter les seuils élevés
        result = pipeline._phase1_mom20x3("TESTXXX")
        assert result is None

    def test_enriches_signal_with_metadata(self, pipeline, mock_mt5, mock_adaptive):
        # Vider le cache pour éviter les données périmées des tests précédents
        pipeline._rates_cache.clear()
        # Prix avec choc haussier brutal pour générer un signal MOM20x3
        # Flat à 1.1 pendant 60 bars, puis +25% en 15 bars pour seuil 2.5×ATR
        bars = []
        for i in range(65):
            bars.append((i, 1.099, 1.101, 1.099, 1.10, 1000, 10, 1000))
        for i in range(65, 80):
            price = 1.10 + (i - 64) * 0.025  # monte rapidement 1.10→1.475
            bars.append((i, price - 0.001, price + 0.002, price - 0.003, price, 1000, 10, 1000))
        for i in range(80, 100):
            price = 1.475 + (i - 79) * 0.001  # continue lentement
            bars.append((i, price - 0.001, price + 0.002, price - 0.002, price, 1000, 10, 1000))
        # Override side_effect (return_value ne suffit pas, side_effect prioritaire)
        mock_mt5.get_rates.side_effect = None
        mock_mt5.get_rates.return_value = bars
        signal = pipeline._phase1_mom20x3("XAUUSD")
        assert signal is not None
        assert signal["symbol"] == "XAUUSD"
        assert signal["timeframe"] == "H4"  # car XAUUSD est en H4 dans la config
        assert "risk_mult" in signal
        assert "rsi" in signal
        assert "higher_tf_conf" in signal

    @patch("engine_simple.strategy.MOM20x3")
    def test_enriches_signal_with_metadata_mocked_mom(self, mock_mom, pipeline, mock_mt5, mock_adaptive):
        mock_mom.return_value = _make_mock_mom20x3()
        signal = pipeline._phase1_mom20x3("XAUUSD")
        assert signal is not None
        assert signal["symbol"] == "XAUUSD"
        assert signal["timeframe"] == "H4"  # XAUUSD=H4 dans la config
        assert "risk_mult" in signal
        assert "rsi" in signal
        assert "higher_tf_conf" in signal

    @patch("engine_simple.strategy.MOM20x3")
    def test_risk_mult_combines_base_and_ol(self, mock_mom, pipeline, mock_mt5, mock_adaptive):
        mock_mt5.get_rates.return_value = [(i, 1.1, 1.11, 1.09, 1.1, 1000, 10, 1000) for i in range(100)]
        mock_adaptive.learner.get_params.return_value = {"thresh": 2.5, "risk_mult": 0.75}
        # 🛡️ 13 Août 2026: symbole changé BTCUSD → EURUSD. BTCUSD a risk_mult=0.0
        # (désactivé 12 Août 2026 — trou noir en mode preuve) → effective=0.0×0.75=0.0,
        # le test ne vérifierait plus la COMBINAISON static×OL. EURUSD (risk_mult=1.0,
        # débloqué) préserve l'intention : le OL risk_mult=0.75 × base 1.0 → 0.75.
        signal = pipeline._phase1_mom20x3("EURUSD")
        assert signal is not None
        assert abs(signal["risk_mult"] - 0.75) < 0.01

    @patch("engine_simple.strategy.MOM20x3")
    def test_ol_base_thresh_from_symbol_config_btcusd(self, mock_mom, pipeline, mock_mt5, mock_adaptive):
        """🔧 FIX 14 Août 2026: le base_thresh passé à l'OL doit venir de la config
        du symbole (strategy.py), PAS du hardcode 2.5. BTCUSD=2.5/2.0 (aligné backtest
        16 Août 2026, PF 1.18 / 6250 trades — l'ancien 5.0/5.0 n'a jamais été validé)
        → l'OL fallback doit recevoir 2.5 et non un autre seuil."""
        mock_mom.return_value = _make_mock_mom20x3()
        # Capture l'argument base_thresh passé à get_params
        captured = {}

        def _fake_get_params(symbol, base_thresh=2.5):
            captured["base_thresh"] = base_thresh
            return {"thresh": base_thresh, "risk_mult": 1.0}

        mock_adaptive.learner.get_params.side_effect = _fake_get_params
        pipeline._rates_cache.clear()
        signal = pipeline._phase1_mom20x3("BTCUSD")
        # Le signal peut être None (filtres aval), mais l'OL DOIT avoir reçu 2.5
        assert captured["base_thresh"] == 2.5, (
            f"OL base_thresh BTCUSD = {captured['base_thresh']} (attendu 2.5) "
            f"— source de vérité = strategy.py:SYMBOL_CONFIG (aligné backtest 16/08)"
        )

    @patch("engine_simple.strategy.MOM20x3")
    def test_ol_base_thresh_default_25_for_symbols_without_override(
        self, mock_mom, pipeline, mock_mt5, mock_adaptive
    ):
        """Les symboles sans override (défaut) doivent conserver base_thresh=2.5."""
        mock_mom.return_value = _make_mock_mom20x3()
        captured = {}

        def _fake_get_params(symbol, base_thresh=2.5):
            captured["base_thresh"] = base_thresh
            return {"thresh": base_thresh, "risk_mult": 1.0}

        mock_adaptive.learner.get_params.side_effect = _fake_get_params
        pipeline._rates_cache.clear()
        pipeline._phase1_mom20x3("TESTXXX")
        assert captured["base_thresh"] == 2.5


# ── Phase 2: ADX Filter ──────────────────────────────────────────────────


class TestPhase2ADXFilter:
    """Tests du filtre ADX."""

    def test_bypass_on_high_score(self, pipeline):
        signal = {"score": 0.85, "adx": 22}
        result = pipeline._phase2_adx_filter("XAUUSD", signal, 1, {})
        assert result is True  # bypass car score>=0.80 ET adx>=20

    def test_bypass_refused_when_adx_too_low(self, pipeline):
        signal = {"score": 0.85, "adx": 9}
        result = pipeline._phase2_adx_filter("XAUUSD", signal, 1, {})
        assert result is False  # score>=0.80 MAIS adx<10 (ADX_BYPASS_MIN=10)

    def test_rejects_low_adx_in_ranging(self, pipeline):
        signal = {"score": 0.60, "adx": 10, "_regime": "RANGING"}
        result = pipeline._phase2_adx_filter("XAUUSD", signal, 1, {})
        assert result is False


# ── Phase 3: Session Filter ──────────────────────────────────────────────


class TestPhase3SessionFilter:
    """Tests du filtre de session — RETIRÉ, toujours pass-through."""

    def test_always_passes(self, pipeline):
        """SessionFilter retiré — toujours True."""
        result = pipeline._phase3_session_filter("XAUUSD", {})
        assert result is True


# ── Phase 5: Direction = Régime ──────────────────────────────────────────


class TestPhase5RegimeRule:
    """Tests de la règle direction = régime."""

    def test_allows_aligned_trades(self, pipeline):
        signal = {"_regime": "TREND_UP", "action": "BUY"}
        assert pipeline._phase5_regime_rule(signal) is True

    def test_blocks_countertrend(self, pipeline):
        signal = {"_regime": "TREND_DOWN", "action": "BUY"}
        assert pipeline._phase5_regime_rule(signal) is False
        # SELL en TREND_UP avec allow_shorts=false → bloqué
        pipeline.symbol_limits["TESTSYM"] = {"allow_shorts": False}
        signal = {"_regime": "TREND_UP", "action": "SELL", "symbol": "TESTSYM", "score": 0.80}
        assert pipeline._phase5_regime_rule(signal) is False

    def test_sell_selectif_trend_up_penalty(self, pipeline):
        """🔧 FIX 28 Août 2026: SELL en TREND_UP avec allow_shorts=true → penalty 0.70."""
        pipeline.symbol_limits["SOLUSD"] = {"allow_shorts": True}
        signal = {"_regime": "TREND_UP", "action": "SELL", "symbol": "SOLUSD", "score": 0.80}
        assert pipeline._phase5_regime_rule(signal) is True
        assert signal["score"] == pytest.approx(0.56, abs=0.01)  # 0.80 × 0.70

    def test_allows_ranging_any_direction(self, pipeline):
        signal = {"_regime": "RANGING", "action": "BUY"}
        assert pipeline._phase5_regime_rule(signal) is True
        signal = {"_regime": "RANGING", "action": "SELL"}
        assert pipeline._phase5_regime_rule(signal) is True


# ── _to_dataframe ────────────────────────────────────────────────────────


class TestToDataFrame:
    """Tests du helper _to_dataframe."""

    def test_converts_tuple_list(self, pipeline):
        data = [(1, 1.1, 1.2, 1.0, 1.15, 100, 5, 90)]
        df = pipeline._to_dataframe(data)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1

    def test_returns_none_on_none(self, pipeline):
        assert pipeline._to_dataframe(None) is None

    def test_passthrough_dataframe(self, pipeline):
        df_in = pd.DataFrame({"a": [1, 2]})
        df_out = pipeline._to_dataframe(df_in)
        assert df_out is df_in  # même objet


# ── Dynamic position limits ──────────────────────────────────────────────


class TestDynamicPositionLimits:
    """Tests des limites de positions dynamiques dans process()."""

    @patch("engine_simple.strategy.MOM20x3")
    def test_high_confidence_gets_max_positions(self, mock_mom, pipeline):
        """conf=0.95, score=0.95 → central bypass (max_per_symbol=4)"""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.95,
                "confidence": 0.95,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_UP",
                "plus_di": 30,
                "minus_di": 15,
                "adx_slope": 5,
            }
        )
        result = pipeline.process(
            symbol="USDJPY",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"USDJPY": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # 🔧 FIX 22 Juillet 2026: score=0.95 + raw_mom=0.95 → central bypass → max=4
        assert result.signal["max_per_symbol"] == 4

    @patch("engine_simple.strategy.MOM20x3")
    def test_limit_respects_hard_cap(self, mock_mom, pipeline):
        """max_per_symbol — central bypass ignore le hard cap."""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.95,
                "confidence": 0.95,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_UP",
                "plus_di": 30,
                "minus_di": 15,
                "adx_slope": 5,
            }
        )
        result = pipeline.process(
            symbol="USDJPY",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"USDJPY": 2},  # hard cap = 2 (mais bypass le contourne)
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # 🔧 FIX 22 Juillet 2026: score=0.95 + raw_mom=0.95 → central bypass → max=4 (ignore hard cap)
        assert result.signal["max_per_symbol"] == 4

    @patch("engine_simple.strategy.MOM20x3")
    def test_bypass_cap_per_symbol_xauusd_capped(self, mock_mom, pipeline):
        """🔧 FIX 20 Août 2026: le cap du bypass central est PAR SYMBOLE.
        XAUUSD plafonné à 1 (SL 1.5×ATR serré → doublon = risque doublé,
        2 SL le 20/08 = -338$), les autres symboles conservent le défaut 4."""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.95,
                "confidence": 0.95,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_UP",
                "plus_di": 30,
                "minus_di": 15,
                "adx_slope": 5,
            }
        )
        # XAUUSD → plafonné à 1 position par signal malgré le bypass
        res_xau = pipeline.process(
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert res_xau is not None
        assert res_xau.signal["max_per_symbol"] == 1
        # USDJPY → non plafonné, défaut 4 conservé
        res_usd = pipeline.process(
            symbol="USDJPY",
            cycle_count=2,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"USDJPY": 4},
            last_signals={},
            log_throttle={},
        )
        assert res_usd is not None
        assert res_usd.signal["max_per_symbol"] == 4

    @patch("engine_simple.strategy.MOM20x3")
    def test_low_confidence_gets_one_position(self, mock_mom, pipeline):
        """conf < 0.70 → max_per_symbol = 1"""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.60,
                "confidence": 0.50,
                "adx": 22,
                "atr": 0.01,
                "_regime": "RANGING",
                "plus_di": 20,
                "minus_di": 18,
                "adx_slope": 2,
            }
        )
        result = pipeline.process(
            symbol="USDJPY",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"USDJPY": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.50 ≤ 0.70 → max_per_symbol=1
        assert result.signal["max_per_symbol"] == 1

    @patch("engine_simple.strategy.MOM20x3")
    def test_moderate_confidence_gets_two_positions(self, mock_mom, pipeline):
        """0.70 < conf < 0.85 → max_per_symbol = 2"""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "SELL",
                "score": 0.80,
                "confidence": 0.75,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_DOWN",
                "plus_di": 15,
                "minus_di": 30,
                "adx_slope": 5,
            }
        )
        result = pipeline.process(
            symbol="USDJPY",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"USDJPY": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.75 > 0.70, < 0.85 → max_per_symbol=2
        assert result.signal["max_per_symbol"] == 2

    @patch("engine_simple.strategy.MOM20x3")
    def test_good_confidence_gets_three_positions(self, mock_mom, pipeline):
        """conf=0.85, score=0.90 → central bypass (max_per_symbol=4)"""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.90,
                "confidence": 0.85,
                "adx": 25,
                "atr": 0.01,
                "_regime": "TREND_UP",
                "plus_di": 30,
                "minus_di": 15,
                "adx_slope": 5,
            }
        )
        result = pipeline.process(
            symbol="USDJPY",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"USDJPY": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # 🔧 FIX 22 Juillet 2026: score=0.90 + raw_mom=0.90 → central bypass → max=4
        assert result.signal["max_per_symbol"] == 4


# ── Phase 1e: Extension Filter (anti-fin-de-tendance) ────────────────────


class TestPhase1eExtension:
    """🔧 17 Août 2026 (Robot Manager): filtre anti-fin-de-tendance.

    Rejette les signaux dont le prix est trop étendu par rapport à l'EMA20
    (momentum épuisé). Audit AUDUSD: 4 losers entrés à 0.711-0.712 (achat à
    l'extension, WR 37.5%, PF 0.34) vs winners entrés à 0.708 (pullback).
    """

    def test_disabled_when_no_config(self, pipeline):
        """Sans max_extension_atr dans symbol_limits → toujours True (fail-open)."""
        # XAUUSD n'a pas max_extension_atr dans la fixture
        signal = {"action": "BUY", "atr": 0.01}
        assert pipeline._phase1e_extension_filter("XAUUSD", signal) is True

    def test_disabled_when_max_ext_none(self, pipeline):
        """max_extension_atr=None → filtre désactivé."""
        pipeline.symbol_limits["AUDUSD"] = {"max_extension_atr": None}
        signal = {"action": "BUY", "atr": 0.01}
        assert pipeline._phase1e_extension_filter("AUDUSD", signal) is True

    def test_rejects_buy_extension(self, pipeline, mock_mt5):
        """BUY trop étendu au-dessus de l'EMA20 → rejet (fin de tendance).

        Prix à +2.0×ATR au-dessus de l'EMA20 > max 1.5×ATR → signal rejeté.
        Correspond au cas réel AUDUSD: entrées losers à 0.711-0.712 quand
        l'EMA20 était ~0.7095 (extension 1.67-3.09×ATR)."""
        pipeline.symbol_limits["AUDUSD"] = {"max_extension_atr": 1.5}
        # Construire des rates où le dernier close est étendu de 2×ATR au-dessus de l'EMA20
        # EMA20 ~0.7000, ATR=0.001, dernier close=0.7020 → extension=2.0×ATR
        base = 0.7000
        atr = 0.0010
        rates = []
        for i in range(60):
            # bougies stables autour de base → EMA20 ≈ base
            rates.append((i, base, base + 0.0005, base - 0.0005, base, 100, 0, 0))
        # dernier close étendu: base + 2×ATR
        rates[-1] = (59, base, base + 0.0005, base - 0.0005, base + 2 * atr, 100, 0, 0)
        mock_mt5.get_rates.side_effect = lambda s, tf, count=100: rates
        signal = {"action": "BUY", "atr": atr}
        assert pipeline._phase1e_extension_filter("AUDUSD", signal) is False

    def test_allows_buy_pullback(self, pipeline, mock_mt5):
        """BUY en pullback (prix proche/sous EMA20) → passe.

        Correspond au cas réel AUDUSD: winners entrés à 0.708 quand l'EMA20
        était ~0.7095 (extension négative = retracement vers la moyenne)."""
        pipeline.symbol_limits["AUDUSD"] = {"max_extension_atr": 1.5}
        base = 0.7000
        atr = 0.0010
        rates = []
        for i in range(60):
            rates.append((i, base, base + 0.0005, base - 0.0005, base, 100, 0, 0))
        # dernier close en pullback: base - 0.5×ATR (sous l'EMA20)
        rates[-1] = (59, base, base + 0.0005, base - 0.0005, base - 0.5 * atr, 100, 0, 0)
        mock_mt5.get_rates.side_effect = lambda s, tf, count=100: rates
        signal = {"action": "BUY", "atr": atr}
        assert pipeline._phase1e_extension_filter("AUDUSD", signal) is True

    def test_allows_buy_moderate_extension(self, pipeline, mock_mt5):
        """BUY à +1.0×ATR (sous le seuil 1.5) → passe."""
        pipeline.symbol_limits["AUDUSD"] = {"max_extension_atr": 1.5}
        base = 0.7000
        atr = 0.0010
        rates = []
        for i in range(60):
            rates.append((i, base, base + 0.0005, base - 0.0005, base, 100, 0, 0))
        rates[-1] = (59, base, base + 0.0005, base - 0.0005, base + 1.0 * atr, 100, 0, 0)
        mock_mt5.get_rates.side_effect = lambda s, tf, count=100: rates
        signal = {"action": "BUY", "atr": atr}
        assert pipeline._phase1e_extension_filter("AUDUSD", signal) is True

    def test_fail_open_on_insufficient_rates(self, pipeline, mock_mt5):
        """Pas assez de rates → fail-open (True), ne bloque jamais le pipeline."""
        pipeline.symbol_limits["AUDUSD"] = {"max_extension_atr": 1.5}
        mock_mt5.get_rates.return_value = None
        signal = {"action": "BUY", "atr": 0.01}
        assert pipeline._phase1e_extension_filter("AUDUSD", signal) is True

    def test_process_rejects_extension(self, pipeline, mock_mt5):
        """Intégration: le signal étendu est rejeté dans process() avec counter."""
        pipeline.symbol_limits["AUDUSD"] = {"max_extension_atr": 1.5}
        # signal MOM20x3 BUY avec atr, et rates étendues
        base = 0.7000
        atr = 0.0010
        rates = []
        for i in range(200):
            rates.append((i, base, base + 0.0005, base - 0.0005, base, 100, 0, 0))
        rates[-1] = (199, base, base + 0.0005, base - 0.0005, base + 2 * atr, 100, 0, 0)
        mock_mt5.get_rates.side_effect = lambda s, tf, count=100: rates
        mock_mt5.get_tick.return_value = MagicMock(ask=base + 2 * atr, bid=base + 2 * atr)

        with patch("engine_simple.signal_pipeline.SignalPipeline._phase1_primary_strategy") as mock_p1:
            mock_p1.return_value = {
                "action": "BUY", "score": 0.8, "confidence": 0.7,
                "atr": atr, "_regime": "TREND_UP", "adx": 25,
                "symbol": "AUDUSD", "timeframe": "H1", "details": "MOM20x3_H1",
            }
            result = pipeline.process(
                symbol="AUDUSD",
                cycle_count=1,
                degraded_symbols={},
                sym_dir_counts={},
                sym_total_counts={},
                config_limits={"AUDUSD": 6},
                last_signals={},
                log_throttle={},
            )
        # Le signal étendu doit être rejeté par la phase 1e
        assert result is None
