"""Test 2.9-2.10: Full feature pipeline."""
import numpy as np
import pandas as pd
import pytest

from src.features.pipeline import FeaturePipeline


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


class TestFeaturePipeline:
    def test_build_produces_features(self, sample_ohlcv):
        pipe = FeaturePipeline(warmup_periods=200)
        result = pipe.build(sample_ohlcv)
        assert len(result) > 0
        assert len(result.columns) >= 40

    def test_no_all_nan_columns(self, sample_ohlcv):
        pipe = FeaturePipeline(warmup_periods=200)
        result = pipe.build(sample_ohlcv)
        all_nan = result.columns[result.isna().all()]
        assert len(all_nan) == 0

    def test_normalize_zscore(self, sample_ohlcv):
        pipe = FeaturePipeline(warmup_periods=200)
        features = pipe.build(sample_ohlcv)
        normalized = pipe.normalize(features)
        z_cols = [c for c in normalized.columns if c.endswith("_z")]
        assert len(z_cols) > 0
        for col in z_cols[:5]:
            mean = normalized[col].mean()
            std = normalized[col].std()
            assert abs(mean) < 0.1
            assert abs(std - 1.0) < 0.2

    def test_empty_dataframe(self):
        pipe = FeaturePipeline()
        result = pipe.build(pd.DataFrame())
        assert result.empty
