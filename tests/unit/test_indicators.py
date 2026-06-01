"""Test 2.1-2.10: Feature engineering indicators."""
import numpy as np
import pandas as pd
import pytest

from src.features.indicators import (
    atr,
    bollinger_bands,
    ema,
    hurst_exponent,
    macd,
    obv,
    order_flow_imbalance,
    realized_volatility,
    rsi,
    vwap,
)


@pytest.fixture
def sample_ohlcv():
    np.random.seed(42)
    n = 500
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.randint(1000, 10000, n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class TestRSI:
    def test_range(self, sample_ohlcv):
        result = rsi(sample_ohlcv["close"], 14)
        valid = result.dropna()
        assert valid.min() >= 0
        assert valid.max() <= 100

    def test_period_effect(self, sample_ohlcv):
        rsi_7 = rsi(sample_ohlcv["close"], 7)
        rsi_21 = rsi(sample_ohlcv["close"], 21)
        assert rsi_7.std() >= rsi_21.std()


class TestMACD:
    def test_output_columns(self, sample_ohlcv):
        result = macd(sample_ohlcv["close"])
        assert "macd" in result.columns
        assert "macd_signal" in result.columns
        assert "macd_histogram" in result.columns

    def test_histogram_is_diff(self, sample_ohlcv):
        result = macd(sample_ohlcv["close"])
        diff = result["macd"] - result["macd_signal"]
        np.testing.assert_allclose(result["macd_histogram"].dropna(), diff.dropna(), atol=1e-10)


class TestBollingerBands:
    def test_ordering(self, sample_ohlcv):
        result = bollinger_bands(sample_ohlcv["close"])
        valid = result.dropna()
        assert (valid["bb_upper"] >= valid["bb_middle"]).all()
        assert (valid["bb_middle"] >= valid["bb_lower"]).all()

    def test_width_positive(self, sample_ohlcv):
        result = bollinger_bands(sample_ohlcv["close"])
        valid = result["bb_width"].dropna()
        assert (valid > 0).all()


class TestATR:
    def test_positive(self, sample_ohlcv):
        result = atr(sample_ohlcv, 14)
        valid = result.dropna()
        assert (valid > 0).all()


class TestHurst:
    def test_range(self, sample_ohlcv):
        h = hurst_exponent(sample_ohlcv["close"])
        assert 0 <= h <= 1

    def test_trending_series(self):
        trending = pd.Series(np.arange(1000, dtype=float))
        h = hurst_exponent(trending)
        assert h > 0.5

    def test_mean_reverting_series(self):
        np.random.seed(99)
        n = 2000
        mean_rev = pd.Series(np.zeros(n))
        for i in range(1, n):
            mean_rev.iloc[i] = -0.5 * mean_rev.iloc[i - 1] + np.random.randn() * 0.1
        h = hurst_exponent(mean_rev, max_lag=50)
        assert h < 0.7


class TestRealizedVol:
    def test_positive(self, sample_ohlcv):
        result = realized_volatility(sample_ohlcv["close"], 20)
        valid = result.dropna()
        assert (valid >= 0).all()


class TestOBV:
    def test_not_constant(self, sample_ohlcv):
        result = obv(sample_ohlcv)
        assert result.std() > 0


class TestVWAP:
    def test_between_high_low(self, sample_ohlcv):
        result = vwap(sample_ohlcv)
        cum_high = sample_ohlcv["high"].expanding().max()
        cum_low = sample_ohlcv["low"].expanding().min()
        assert (result <= cum_high).all()
        assert (result >= cum_low).all()


class TestOrderFlowImbalance:
    def test_range(self, sample_ohlcv):
        result = order_flow_imbalance(sample_ohlcv)
        assert result.min() >= -1
        assert result.max() <= 1
