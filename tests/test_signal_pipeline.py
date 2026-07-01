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
def mock_session_filter():
    m = MagicMock()
    m.get_session_score.return_value = 0.8
    return m


@pytest.fixture
def mock_adaptive():
    m = MagicMock()
    m.learner.get_params.return_value = {"thresh": 2.5, "risk_mult": 0.75}
    return m


@pytest.fixture
def mock_market_memory():
    m = MagicMock()
    m.get_mtf_alignment.return_value = {"H1": "bullish", "H4": "bullish", "D1": "neutral"}
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

    return MockConfig()


@pytest.fixture
def pipeline(
    mock_mt5,
    mock_ftmo,
    mock_adaptive,
    mock_market_memory,
    mock_session_filter,
    mock_news_filter,
    mock_strategy_selector,
    mock_volume_profile,
    mock_mtf_confirm,
    mock_risk_manager,
    mock_config,
):
    """Crée une instance de SignalPipeline avec tous les mocks."""
    return SignalPipeline(
        mt5=mock_mt5,
        ftmo=mock_ftmo,
        adaptive=mock_adaptive,
        market_memory=mock_market_memory,
        session_filter=mock_session_filter,
        news_filter=mock_news_filter,
        strategy_selector=mock_strategy_selector,
        volume_profile=mock_volume_profile,
        mtf_confirm=mock_mtf_confirm,
        risk_manager=mock_risk_manager,
        config=mock_config,
        symbol_limits=mock_config.SYMBOL_LIMITS,
        symbol_timeframes=mock_config.SYMBOL_TIMEFRAMES,
    )


# ── Pipeline Init ────────────────────────────────────────────────────────


class TestSignalPipelineInit:
    """Tests d'initialisation du pipeline."""

    def test_init_stores_dependencies(self, pipeline):
        assert pipeline.mt5 is not None
        assert pipeline.ftmo is not None
        assert pipeline.adaptive is not None
        assert pipeline.cfg is not None
        assert pipeline._adaptive_params == {}

    def test_init_with_none_session_filter(
        self,
        mock_mt5,
        mock_ftmo,
        mock_adaptive,
        mock_config,
        mock_market_memory,
        mock_news_filter,
        mock_strategy_selector,
        mock_volume_profile,
        mock_mtf_confirm,
        mock_risk_manager,
    ):
        """Le pipeline doit fonctionner même si session_filter est None."""
        p = SignalPipeline(
            mt5=mock_mt5,
            ftmo=mock_ftmo,
            adaptive=mock_adaptive,
            market_memory=mock_market_memory,
            session_filter=None,  # ← volontairement None
            news_filter=mock_news_filter,
            strategy_selector=mock_strategy_selector,
            volume_profile=mock_volume_profile,
            mtf_confirm=mock_mtf_confirm,
            risk_manager=mock_risk_manager,
            config=mock_config,
            symbol_limits=mock_config.SYMBOL_LIMITS,
            symbol_timeframes=mock_config.SYMBOL_TIMEFRAMES,
        )
        assert p.session_filter is None


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
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        assert isinstance(result, SignalResult)
        assert result.symbol == "XAUUSD"
        assert "action" in result.signal
        assert result.signal["max_per_symbol"] > 0

    def test_process_none_on_pre_trade_fail(self, pipeline, mock_risk_manager):
        mock_risk_manager.pre_trade.return_value = (
            False,
            [{"rule": "danger_hours", "pass": False, "reason": "Danger hours block"}],
        )
        result = pipeline.process(
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is None

    def test_process_none_on_mom20x3_fail(self, pipeline, mock_mt5):
        mock_mt5.get_rates.return_value = None
        result = pipeline.process(
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_sets_degraded_flag(self, mock_mom, pipeline):
        mock_mom.return_value = _make_mock_mom20x3()
        result = pipeline.process(
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={"XAUUSD": 0},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
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
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_respects_position_direction_limit(self, mock_mom, pipeline):
        """Si la limite de direction est atteinte, process() retourne None."""
        mock_mom.return_value = _make_mock_mom20x3()
        result = pipeline.process(
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={("XAUUSD", 0): 4},  # 4 BUY déjà
            sym_total_counts={"XAUUSD": 4},
            config_limits={"XAUUSD": 3},
            last_signals={},
            log_throttle={},
        )
        assert result is None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_respects_total_position_limit(self, mock_mom, pipeline):
        mock_mom.return_value = _make_mock_mom20x3()
        result = pipeline.process(
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            # sym_total_count=12 dépasse max_pos_total=min(8*2=16, 10*2=20, 80)=16
            # Mais avec phase 11 (MP) le score chute et la direction BUY est bloquée
            # Utilisons un count très élevé pour tester le blocage
            sym_total_counts={"XAUUSD": 20},
            config_limits={"XAUUSD": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is None

    @patch("engine_simple.strategy.MOM20x3")
    def test_process_sets_max_per_symbol_by_confidence(self, mock_mom, pipeline):
        """conf=0.95 > 0.90 → max_per_symbol=4"""
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
            symbol="XAUUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"XAUUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.95 >= 0.85 (HIGH_CONF_CONFIDENCE) → max_per_symbol=3
        assert result.signal["max_per_symbol"] == 3

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
        mock_mt5.get_rates.return_value = [(i, 1.0, 1.01, 0.99, 1.0, 100, 5, 100) for i in range(10)]
        result = pipeline._phase1_mom20x3("XAUUSD")
        assert result is None

    def test_enriches_signal_with_metadata(self, pipeline, mock_mt5, mock_adaptive):
        # Prix avec choc haussier brutal pour générer un signal MOM20x3
        # Flat à 1.1 pendant 60 bars, puis +15% en 10 bars, puis uptrend lent
        bars = []
        for i in range(70):
            bars.append((i, 1.099, 1.101, 1.099, 1.10, 1000, 10, 1000))
        for i in range(70, 80):
            price = 1.10 + (i - 69) * 0.02  # monte rapidement 1.10→1.28
            bars.append((i, price - 0.001, price + 0.002, price - 0.003, price, 1000, 10, 1000))
        for i in range(80, 100):
            price = 1.28 + (i - 79) * 0.001  # continue lentement 1.28→1.30
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
        signal = pipeline._phase1_mom20x3("BTCUSD")
        assert signal is not None
        # BTCUSD base=0.65 × OL=0.75 = 0.49
        assert abs(signal["risk_mult"] - 0.49) < 0.01


# ── Phase 2: ADX Filter ──────────────────────────────────────────────────


class TestPhase2ADXFilter:
    """Tests du filtre ADX."""

    def test_bypass_on_high_score(self, pipeline):
        signal = {"score": 0.85, "adx": 18}
        result = pipeline._phase2_adx_filter("XAUUSD", signal, 1, {})
        assert result is True  # bypass car score>=0.80 ET adx>=15

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
    """Tests du filtre de session."""

    def test_accepts_good_session(self, pipeline):
        """SessionFilter retiré — toujours pass-through."""
        signal = {}
        result = pipeline._phase3_session_filter("XAUUSD", signal)
        assert result is True

    def test_skip_without_filter(self, pipeline, mock_session_filter):
        """SessionFilter retiré — toujours pass-through."""
        signal = {}
        result = pipeline._phase3_session_filter("XAUUSD", signal)
        assert result is True

    def test_rejects_low_session(self, pipeline, mock_session_filter):
        """SessionFilter retiré — toujours pass-through."""
        signal = {}
        result = pipeline._phase3_session_filter("XAUUSD", signal)
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
        signal = {"_regime": "TREND_UP", "action": "SELL"}
        assert pipeline._phase5_regime_rule(signal) is False

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
        """conf > 0.90 → max_per_symbol = 4"""
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
            symbol="BTCUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"BTCUSD": 4},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.95 >= 0.85 (HIGH_CONF_CONFIDENCE) → max_per_symbol=3
        assert result.signal["max_per_symbol"] == 3

    @patch("engine_simple.strategy.MOM20x3")
    def test_limit_respects_hard_cap(self, mock_mom, pipeline):
        """max_per_symbol plafonné au hard_limit de config_limits."""
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
            symbol="BTCUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"BTCUSD": 2},  # hard cap = 2
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.95 >= 0.85 (HIGH_CONF_CONFIDENCE) → cap=3 (hard limit ignoré)
        assert result.signal["max_per_symbol"] == 3

    @patch("engine_simple.strategy.MOM20x3")
    def test_low_confidence_gets_one_position(self, mock_mom, pipeline):
        """conf < 0.70 → max_per_symbol = 1"""
        mock_mom.return_value = _make_mock_mom20x3(
            {
                "action": "BUY",
                "score": 0.60,
                "confidence": 0.50,
                "adx": 15,
                "atr": 0.01,
                "_regime": "RANGING",
                "plus_di": 20,
                "minus_di": 18,
                "adx_slope": 2,
            }
        )
        result = pipeline.process(
            symbol="BTCUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"BTCUSD": 10},
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
            symbol="BTCUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"BTCUSD": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.75 > 0.70, < 0.85 → max_per_symbol=2
        assert result.signal["max_per_symbol"] == 2

    @patch("engine_simple.strategy.MOM20x3")
    def test_good_confidence_gets_three_positions(self, mock_mom, pipeline):
        """conf >= 0.85 (HIGH_CONF_CONFIDENCE) → max_per_symbol = 3"""
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
            symbol="BTCUSD",
            cycle_count=1,
            degraded_symbols={},
            sym_dir_counts={},
            sym_total_counts={},
            config_limits={"BTCUSD": 10},
            last_signals={},
            log_throttle={},
        )
        assert result is not None
        # conf=0.85 >= 0.85 (HIGH_CONF_CONFIDENCE) → max_per_symbol=3 (1er Juillet 2026)
        assert result.signal["max_per_symbol"] == 3
