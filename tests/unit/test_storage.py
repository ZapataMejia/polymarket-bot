"""Test 1.6-1.7: Parquet storage."""
import numpy as np
import pandas as pd
import pytest

from src.data.storage import ParquetStorage


@pytest.fixture
def storage(tmp_path):
    return ParquetStorage(str(tmp_path))


@pytest.fixture
def sample_df():
    idx = pd.date_range("2024-01-01", periods=100, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": np.random.randn(100) + 100,
            "high": np.random.randn(100) + 101,
            "low": np.random.randn(100) + 99,
            "close": np.random.randn(100) + 100,
            "volume": np.random.randint(1000, 10000, 100).astype(float),
        },
        index=idx,
    )


class TestParquetStorage:
    def test_save_and_load(self, storage, sample_df):
        storage.save(sample_df, "BTC/USDT", "1h")
        loaded = storage.load("BTC/USDT", "1h")
        assert len(loaded) == len(sample_df)
        pd.testing.assert_frame_equal(loaded, sample_df, check_freq=False)

    def test_append_deduplicates(self, storage, sample_df):
        storage.save(sample_df[:50], "BTC/USDT", "1h")
        storage.save(sample_df[40:], "BTC/USDT", "1h")
        loaded = storage.load("BTC/USDT", "1h")
        assert len(loaded) == len(sample_df)

    def test_load_nonexistent(self, storage):
        result = storage.load("FAKE/PAIR", "1m")
        assert result.empty

    def test_list_files(self, storage, sample_df):
        storage.save(sample_df, "BTC/USDT", "1h")
        storage.save(sample_df, "ETH/USDT", "1h")
        files = storage.list_files()
        assert len(files) == 2

    def test_empty_df_skipped(self, storage):
        storage.save(pd.DataFrame(), "BTC/USDT", "1m")
        assert storage.load("BTC/USDT", "1m").empty
