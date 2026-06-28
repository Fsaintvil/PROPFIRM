"""Tests for DataLoader."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine_simple.backtest_core.data_loader import DataLoader


@pytest.fixture
def loader():
    return DataLoader()


@pytest.fixture
def sample_df():
    n = 100
    np.random.seed(42)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h"),
            "open": 1.10 + np.random.randn(n) * 0.002,
            "high": 1.10 + np.abs(np.random.randn(n)) * 0.003 + 0.001,
            "low": 1.10 - np.abs(np.random.randn(n)) * 0.003 - 0.001,
            "close": 1.10 + np.cumsum(np.random.randn(n) * 0.002),
            "volume": np.random.randint(100, 1000, n),
            "spread": np.random.randint(10, 30, n),
        }
    )


class TestDataLoader:
    def test_init_default(self, loader):
        assert str(loader.data_root) == "."

    def test_init_custom_path(self):
        loader = DataLoader("C:\\data")
        assert str(loader.data_root) == "C:\\data"

    def test_load_invalid_file(self, loader):
        with pytest.raises(FileNotFoundError):
            loader.load("ZZZZZZ", "H1")

    def test_normalize_columns_standard(self, loader):
        df = pd.DataFrame(
            {
                "Time": [datetime.utcnow()],
                "Open": [1.10],
                "High": [1.11],
                "Low": [1.09],
                "Close": [1.105],
                "Volume": [100],
            }
        )
        normalized = loader._normalize_columns(df)
        assert "timestamp" in normalized.columns
        assert "open" in normalized.columns
        assert "high" in normalized.columns
        assert "low" in normalized.columns
        assert "close" in normalized.columns
        assert "volume" in normalized.columns

    def test_normalize_columns_adds_missing(self, loader):
        df = pd.DataFrame(
            {
                "timestamp": [datetime.utcnow()],
                "open": [1.10],
                "high": [1.11],
                "low": [1.09],
                "close": [1.105],
            }
        )
        normalized = loader._normalize_columns(df)
        assert "volume" in normalized.columns
        assert "spread" in normalized.columns
        assert normalized["volume"].iloc[0] == 0
        assert normalized["spread"].iloc[0] == 0

    def test_normalize_columns_raises_on_missing_required(self, loader):
        df = pd.DataFrame({"timestamp": [1], "open": [2]})
        with pytest.raises(ValueError):
            loader._normalize_columns(df)

    def test_clean_removes_invalid_prices(self, loader, sample_df):
        df_bad = sample_df.copy()
        df_bad.loc[5, "open"] = -1.0
        df_bad.loc[10, "close"] = 0
        cleaned = loader.clean(df_bad, symbol="EURUSD")
        assert len(cleaned) < len(df_bad)
        assert (cleaned["open"] > 0).all()
        assert (cleaned["close"] > 0).all()

    def test_clean_removes_corrupt_bars(self, loader, sample_df):
        df_corrupt = sample_df.copy()
        df_corrupt.loc[5, "high"] = df_corrupt.loc[5, "low"] - 0.01
        cleaned = loader.clean(df_corrupt, remove_corrupt=True)
        assert len(cleaned) <= len(df_corrupt)

    def test_clean_fills_zero_spread(self, loader, sample_df):
        df_with_zero = sample_df.copy()
        df_with_zero.loc[10:15, "spread"] = 0
        cleaned = loader.clean(df_with_zero, fill_spread=True)
        assert (cleaned["spread"] > 0).all()

    def test_clean_removes_outliers(self, loader, sample_df):
        df_with_outlier = sample_df.copy()
        df_with_outlier.loc[50, "close"] = 1000.0
        cleaned = loader.clean(df_with_outlier, remove_outliers=True)
        assert 50 not in cleaned.index or len(cleaned) < len(df_with_outlier)

    def test_clean_removes_duplicates(self, loader, sample_df):
        df_dup = pd.concat([sample_df, sample_df.iloc[10:15]], ignore_index=True)
        cleaned = loader.clean(df_dup)
        assert len(cleaned) == len(sample_df)

    def test_clean_empty_df(self, loader):
        df = pd.DataFrame()
        cleaned = loader.clean(df)
        assert cleaned.empty

    def test_detect_sessions_adds_columns(self, loader, sample_df):
        df = loader.detect_sessions(sample_df)
        assert "session" in df.columns
        assert "hour_utc" in df.columns
        assert "is_asia" in df.columns
        assert "is_london" in df.columns
        assert "is_ny" in df.columns

    def test_detect_sessions_correct_labels(self, loader):
        df = pd.DataFrame(
            {
                "timestamp": [
                    datetime(2026, 6, 15, 3, 0),
                    datetime(2026, 6, 15, 10, 0),
                    datetime(2026, 6, 15, 15, 0),
                    datetime(2026, 6, 15, 23, 0),
                ],
            }
        )
        df = loader.detect_sessions(df)
        assert df["session"].iloc[0] == "asia"
        assert df["session"].iloc[1] == "london"
        assert df["session"].iloc[2] in ("ny", "overlap_london_ny")
        assert df["session"].iloc[3] == "closed"

    def test_add_indicators_adds_all(self, loader, sample_df):
        df = loader.add_indicators(sample_df)
        expected = [
            "atr_14",
            "adx_14",
            "rsi_14",
            "ema_12",
            "ema_26",
            "sma_20",
            "sma_50",
            "sma_200",
            "bb_upper",
            "bb_lower",
            "macd",
            "macd_signal",
            "volume_sma_20",
        ]
        for col in expected:
            assert col in df.columns, f"Missing column: {col}"

    def test_add_indicators_atr_computed(self, loader, sample_df):
        df = loader.add_indicators(sample_df)
        assert df["atr_14"].notna().sum() > 0

    def test_add_indicators_rsi_range(self, loader, sample_df):
        df = loader.add_indicators(sample_df)
        rsi_valid = df["rsi_14"].dropna()
        assert (rsi_valid >= 0).all()
        assert (rsi_valid <= 100).all()

    def test_compute_atr(self, loader):
        high = np.array([1.11, 1.12, 1.13, 1.12, 1.14])
        low = np.array([1.09, 1.08, 1.07, 1.08, 1.06])
        close = np.array([1.10, 1.11, 1.12, 1.11, 1.13])
        atr = DataLoader._compute_atr(high, low, close, period=3)
        assert len(atr) == 5
        assert np.isnan(atr[0])
        assert not np.isnan(atr[3])

    def test_compute_rsi(self, loader):
        close = np.linspace(1.0, 1.2, 30)
        rsi = DataLoader._compute_rsi(close, period=14)
        assert len(rsi) == 30
        assert not np.isnan(rsi[-1])
        assert rsi[-1] > 50

    def test_compute_sma(self, loader):
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        sma = DataLoader._compute_sma(data, period=3)
        assert np.isnan(sma[0])
        assert np.isnan(sma[1])
        assert sma[2] == pytest.approx(2.0)
        assert sma[4] == pytest.approx(4.0)

    def test_compute_ema(self, loader):
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
        ema = DataLoader._compute_ema(data, period=5)
        assert len(ema) == 12
        assert not np.isnan(ema[-1])

    def test_compute_bollinger(self, loader):
        data = np.linspace(1.0, 1.1, 30)
        upper, lower = DataLoader._compute_bollinger(data, period=20, std_dev=2.0)
        assert len(upper) == 30
        assert len(lower) == 30
        assert not np.isnan(upper[-1])
        assert upper[-1] > lower[-1]

    def test_compute_macd(self, loader):
        data = np.linspace(1.0, 1.2, 60)
        macd, signal = DataLoader._compute_macd(data, fast=12, slow=26, signal=9)
        assert len(macd) == 60
        assert len(signal) == 60

    def test_resample_to_tf(self, loader, sample_df):
        resampled = loader.resample_to_tf(sample_df, "D1")
        assert len(resampled) < len(sample_df)
        assert "timestamp" in resampled.columns
        assert "open" in resampled.columns

    def test_list_available_symbols_returns_list(self, loader):
        symbols = DataLoader.list_available_symbols("H1", "historical")
        assert isinstance(symbols, list)
