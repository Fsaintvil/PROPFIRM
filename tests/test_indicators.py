"""Unit tests for indicators.py — pure NumPy, no MT5 dependency"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np

np.random.seed(42)

from engine_simple.indicators import (
    anchored_vwap,
    atr,
    bollinger_bands,
    ema,
    ema_alignment,
    fibonacci_retracement,
    macd,
    market_regime_features,
    obv,
    premium_discount_zones,
    rsi,
    rsi_divergence,
    sma,
    stochastic_rsi,
    volume_profile,
    vwap,
)


def _assert_shape(arr, expected_len, name="array"):
    assert isinstance(arr, np.ndarray), f"{name} should be ndarray, got {type(arr)}"
    assert len(arr) == expected_len, f"{name} len={len(arr)}, expected {expected_len}"


def _assert_not_all_nan(arr, name="array"):
    assert not np.all(np.isnan(arr)), f"{name} is all NaN"


# ── EMA ──

def test_ema_basic():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    result = ema(data, 3)
    _assert_shape(result, 10)
    assert not np.isnan(result[-1])


def test_ema_too_short():
    result = ema([1, 2], 5)
    assert np.all(np.isnan(result))


def test_ema_constant():
    data = [5] * 20
    result = ema(data, 5)
    assert np.isclose(result[-1], 5.0, atol=0.1)


def test_ema_same_as_sma_at_period():
    # At the period index, EMA == SMA
    data = list(range(1, 21))
    e = ema(data, 10)
    s = sma(data, 10)
    assert np.isclose(e[9], s[9]), f"EMA period should equal SMA: {e[9]} vs {s[9]}"


# ── SMA ──

def test_sma_basic():
    result = sma([1, 2, 3, 4, 5], 3)
    _assert_shape(result, 5)
    assert np.isclose(result[2], 2.0)  # (1+2+3)/3
    assert np.isclose(result[3], 3.0)


def test_sma_too_short():
    assert np.all(np.isnan(sma([1, 2], 3)))


# ── RSI ──

def test_rsi_uptrend():
    data = list(range(1, 30))
    r = rsi(data)
    _assert_shape(r, 29)
    assert not np.isnan(r[-1])
    assert r[-1] > 70, f"Uptrend RSI should be >70, got {r[-1]}"


def test_rsi_downtrend():
    data = list(range(30, 0, -1))
    r = rsi(data)
    assert r[-1] < 30, f"Downtrend RSI should be <30, got {r[-1]}"


def test_rsi_constant():
    data = [50] * 20
    r = rsi(data)
    # Constant data: avg_loss = 0 → RSI = 100
    if not np.all(np.isnan(r)):
        non_nan = r[~np.isnan(r)]
        if len(non_nan) > 0:
            assert np.allclose(non_nan, 100), f"Constant data RSI should be 100, got {non_nan}"


def test_rsi_too_short():
    assert np.all(np.isnan(rsi([1, 2, 3], 14)))


def test_rsi_50_period():
    data = list(range(1, 50))
    r = rsi(data, 14)
    assert not np.all(np.isnan(r)), "RSI with 50 points should produce non-nan values"


# ── MACD ──

def test_macd_basic():
    data = list(range(1, 120))
    line, sig, hist = macd(data)
    for name, arr in [("line", line), ("sig", sig), ("hist", hist)]:
        _assert_shape(arr, 119, name)
    # Last 20 values should all be non-nan
    for name, arr in [("line", line), ("sig", sig), ("hist", hist)]:
        assert not np.any(np.isnan(arr[-20:])), f"MACD {name} has NaN in last 20"


def test_macd_constant():
    data = [10] * 50
    line, sig, hist = macd(data)
    non_nan = line[~np.isnan(line)]
    if len(non_nan) > 0:
        assert np.allclose(non_nan, 0, atol=1e-6), "Constant MACD should be 0"


# ── Bollinger Bands ──

def test_bollinger_basic():
    data = list(range(1, 30))
    upper, mid, lower = bollinger_bands(data)
    for arr in [upper, mid, lower]:
        _assert_shape(arr, 29)
    assert not np.all(np.isnan(upper))
    assert np.all(upper[19:] >= mid[19:])  # Upper >= middle


def test_bollinger_ordering():
    data = np.random.default_rng(42).normal(100, 5, 30).tolist()
    upper, mid, lower = bollinger_bands(data)
    valid = ~np.isnan(upper)
    assert np.all(upper[valid] >= mid[valid])
    assert np.all(mid[valid] >= lower[valid])


# ── VWAP ──

def test_vwap_basic():
    high = [1, 2, 3]
    low = [0.5, 1, 2]
    close = [0.8, 1.5, 2.5]
    volume = [1000, 2000, 3000]
    result = vwap(high, low, close, volume)
    _assert_shape(result, 3)
    assert result[0] > 0


def test_vwap_constant():
    result = vwap([10, 10], [9, 9], [9.5, 9.5], [100, 200])
    assert np.isclose(result[0], result[1], atol=0.01)


# ── OBV ──

def test_obv_basic():
    close = [10, 11, 12, 11, 10]
    volume = [100, 200, 150, 180, 120]
    result = obv(close, volume)
    _assert_shape(result, 5)
    # Up → add, Down → subtract
    assert result[1] == result[0] + volume[1]  # price up
    assert result[3] == result[2] - volume[3]  # price down


def test_obv_constant_price():
    close = [10, 10, 10]
    volume = [100, 200, 300]
    result = obv(close, volume)
    assert result[0] == 100
    assert result[1] == 100  # no change
    assert result[2] == 100


# ── ATR ──

def test_atr_basic():
    high = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    low = [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    close = [0.8, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5]
    result = atr(high, low, close, 14)
    _assert_shape(result, 16)
    assert not np.isnan(result[-1])


def test_atr_too_short():
    assert np.all(np.isnan(atr([1, 2], [0, 1], [0.5, 1.5], 14)))


# ── Stochastic RSI ──

def test_stoch_rsi_basic():
    data = np.random.default_rng(42).normal(100, 5, 50).tolist()
    k, d = stochastic_rsi(data)
    _assert_shape(k, 50)
    _assert_shape(d, 50)
    non_nan = k[~np.isnan(k)]
    if len(non_nan) > 0:
        assert np.all(non_nan >= 0) and np.all(non_nan <= 100), "StochRSI should be 0-100"


# ── Volume Profile ──

def test_volume_profile_basic():
    prices = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    volumes = [100] * 10
    bins, profile, summary = volume_profile(prices, volumes, 10)
    assert len(bins) == 10
    assert len(profile) == 10
    assert bins[0] <= bins[-1]


def test_volume_profile_too_short():
    bins, profile, summary = volume_profile([1, 2], [100, 200], 10)
    assert len(bins) == 0


# ── EMA Alignment ──

def test_ema_alignment_bullish():
    # All EMAs in bullish order: fast > slow
    result = ema_alignment(10, 9.5, 9, 8.5, 10.5)
    assert result > 0, f"Bullish alignment should be >0, got {result}"


def test_ema_alignment_bearish():
    result = ema_alignment(8.5, 9, 9.5, 10, 8)
    assert result < 0, f"Bearish alignment should be <0, got {result}"


# ── Anchored VWAP ──

def test_anchored_vwap_basic():
    high = list(range(10, 110, 10))
    low = list(range(5, 105, 10))
    close = list(range(8, 108, 10))
    volume = [1000] * 10
    avwap, dist = anchored_vwap(high, low, close, volume, 0)
    assert avwap > 0


# ── Fibonacci ──

def test_fib_basic():
    levels = fibonacci_retracement(10, 5, current_price=7)
    assert "level_0" in levels
    assert "level_1" in levels
    assert levels["level_0"] == 5
    assert levels["level_1"] == 10


def test_fib_nearest():
    levels = fibonacci_retracement(10, 5, current_price=7.5)
    assert levels.get("nearest") is not None  # 7.5 is near 0.5 level (7.5)


def test_fib_invalid():
    assert fibonacci_retracement(5, 10) == {}  # low > high
    assert fibonacci_retracement(None, 10) == {}
    assert fibonacci_retracement(10, None) == {}


# ── Premium/Discount ──

def test_premium_discount():
    result = premium_discount_zones(110, 100, 120, 80)
    assert result["zone"] == "premium"
    assert result["distance"] > 0
    result2 = premium_discount_zones(90, 100, 120, 80)
    assert result2["zone"] == "discount"
    result3 = premium_discount_zones(100, 100)
    assert result3["zone"] == "at_vwap"


def test_premium_discount_none_vwap():
    result = premium_discount_zones(100, None)
    assert result["zone"] == "unknown"


# ── RSI Divergence ──

def test_divergence_short_data():
    result = rsi_divergence([1, 2, 3], [50, 55, 60], 20)
    assert result["bullish"] is False
    assert result["bearish"] is False


def test_divergence_no_div():
    c = list(range(1, 31))
    r = [50 + i * 0.5 for i in range(30)]
    result = rsi_divergence(c, r, 20)
    assert result["bullish"] is False
    assert result["bearish"] is False


# ── Market Regime Features ──

def test_market_regime_basic():
    data = np.random.default_rng(42).normal(100, 5, 60).tolist()
    features = market_regime_features(data, [x-1 for x in data], data, [1000]*60)
    assert isinstance(features, dict)
    assert len(features) > 0


def test_market_regime_too_short():
    features = market_regime_features([1, 2, 3], [0.5, 1, 2], [0.8, 1.5, 2.5], [100]*3, 50)
    assert features == {}


# ── Run all ──

def run():
    """Discover and run all test_* functions in this module"""
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    total = passed + failed
    print(f"\n{total} tests: {passed} passed, {failed} failed ({passed/total*100:.0f}%)")
    return failed == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)

