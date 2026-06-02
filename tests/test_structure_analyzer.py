import numpy as np

from engine_simple.structure_analyzer import (
    detect_bos,
    detect_choch,
    multi_tf_alignment,
    structure_exit_signal,
)


class TestMultiTFAlignment:

    def test_bullish_alignment(self):
        d = np.linspace(1.10, 1.20, 60, dtype=float)
        h4 = np.linspace(1.10, 1.18, 60, dtype=float)
        h1 = np.linspace(1.15, 1.18, 60, dtype=float)
        direction, score = multi_tf_alignment(d, h4, h1)
        assert direction == "BUY"
        assert score >= 2

    def test_bearish_alignment(self):
        d = np.linspace(1.20, 1.10, 60, dtype=float)
        h4 = np.linspace(1.18, 1.10, 60, dtype=float)
        h1 = np.linspace(1.15, 1.10, 60, dtype=float)
        direction, score = multi_tf_alignment(d, h4, h1)
        assert direction == "SELL"
        assert score <= -2

    def test_no_trade_on_conflict(self):
        d = np.linspace(1.10, 1.20, 60, dtype=float)
        h4 = np.linspace(1.20, 1.10, 60, dtype=float)
        h1 = np.linspace(1.15, 1.18, 60, dtype=float)
        direction, score = multi_tf_alignment(d, h4, h1)
        assert direction == "NO_TRADE"
        assert abs(score) < 2  # conflit, pas de majorité claire

    def test_insufficient_data_returns_no_trade(self):
        d = np.array([1.10] * 20, dtype=float)
        h4 = np.array([1.10] * 20, dtype=float)
        h1 = np.array([1.10] * 20, dtype=float)
        direction, score = multi_tf_alignment(d, h4, h1)
        assert direction == "NO_TRADE"

    def test_neutral_flat_market(self):
        d = np.array([1.10] * 60, dtype=float)
        h4 = np.array([1.10] * 60, dtype=float)
        h1 = np.array([1.10] * 60, dtype=float)
        direction, score = multi_tf_alignment(d, h4, h1)
        assert direction == "NO_TRADE"


class TestDetectBOS:

    def test_bearish_bos(self):
        h = np.linspace(1.15, 1.10, 20, dtype=float)
        l = h - 0.005
        c = (h + l) / 2
        bos_type, level = detect_bos(h, l, c, window=5)
        assert bos_type == "BEARISH"
        assert level is not None

    def test_bullish_bos(self):
        h = np.linspace(1.10, 1.15, 20, dtype=float)
        l = h - 0.005
        c = (h + l) / 2
        bos_type, level = detect_bos(h, l, c, window=5)
        assert bos_type == "BULLISH"
        assert level is not None

    def test_no_bos_on_short_data(self):
        h = np.array([1.10, 1.11], dtype=float)
        l = np.array([1.09, 1.10], dtype=float)
        c = np.array([1.095, 1.105], dtype=float)
        bos_type, level = detect_bos(h, l, c, window=5)
        assert bos_type is None

    def test_no_bos_on_noisy_data(self):
        np.random.seed(42)
        h = 1.10 + np.random.randn(30) * 0.01
        l = h - 0.005
        c = (h + l) / 2
        bos_type, level = detect_bos(h, l, c, window=5)
        assert bos_type is None


class TestDetectCHoCH:

    def test_bearish_choch(self):
        h = np.array([1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17,
                      1.18, 1.17, 1.16, 1.15, 1.14, 1.13, 1.12, 1.11,
                      1.10, 1.09, 1.08, 1.07, 1.06, 1.05, 1.04, 1.03,
                      1.02, 1.01, 1.00, 0.99, 0.98, 0.97], dtype=float)
        l = h - 0.01
        c = (h + l) / 2
        choch_type, level = detect_choch(h, l, c, window=5)
        assert choch_type == "BEARISH"

    def test_bullish_choch(self):
        h = np.array([1.18, 1.17, 1.16, 1.15, 1.14, 1.13, 1.12, 1.11,
                      1.10, 1.09, 1.08, 1.07, 1.06, 1.05, 1.04,
                      1.05, 1.06, 1.07, 1.08, 1.09, 1.10, 1.11, 1.12,
                      1.13, 1.14, 1.15, 1.16, 1.17, 1.18, 1.19], dtype=float)
        l = h - 0.01
        c = (h + l) / 2
        choch_type, level = detect_choch(h, l, c, window=5)
        assert choch_type == "BULLISH"

    def test_no_choch_on_trend(self):
        h = np.linspace(1.10, 1.15, 60, dtype=float)
        l = h - 0.005
        c = (h + l) / 2
        choch_type, level = detect_choch(h, l, c, window=5)
        assert choch_type is None

    def test_no_choch_on_short_data(self):
        h = np.array([1.10, 1.11], dtype=float)
        l = np.array([1.09, 1.10], dtype=float)
        c = np.array([1.095, 1.105], dtype=float)
        choch_type, level = detect_choch(h, l, c, window=5)
        assert choch_type is None


class TestStructureExitSignal:

    def test_exit_on_bearish_bos_for_buy(self):
        h = np.linspace(1.15, 1.10, 20, dtype=float)
        l = h - 0.005
        c = (h + l) / 2
        should_exit, reason = structure_exit_signal(0, h, l, c, window=5)
        assert should_exit is True
        assert "BEARISH_BOS" in reason

    def test_exit_on_bullish_bos_for_sell(self):
        h = np.linspace(1.10, 1.15, 20, dtype=float)
        l = h - 0.005
        c = (h + l) / 2
        should_exit, reason = structure_exit_signal(1, h, l, c, window=5)
        assert should_exit is True
        assert "BULLISH_BOS" in reason

    def test_no_exit_on_aligned_trend(self):
        h = np.linspace(1.10, 1.15, 20, dtype=float)
        l = h - 0.005
        c = (h + l) / 2
        should_exit, _ = structure_exit_signal(0, h, l, c, window=5)
        assert should_exit is False

    def test_no_exit_on_short_data(self):
        h = np.array([1.10, 1.11], dtype=float)
        l = np.array([1.09, 1.10], dtype=float)
        c = np.array([1.095, 1.105], dtype=float)
        should_exit, _ = structure_exit_signal(0, h, l, c, window=5)
        assert should_exit is False
