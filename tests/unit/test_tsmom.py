import numpy as np
import pandas as pd

from src.strategies.backtest import BacktestEngine
from src.strategies.tsmom import (
    build_tsmom_binary_signal,
    build_tsmom_exposure,
    compute_tsmom_snapshot,
    ohlcv_to_daily,
)


def test_ohlcv_to_daily():
    idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
    close = np.linspace(100, 110, 48)
    df = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0},
        index=idx,
    )
    d = ohlcv_to_daily(df)
    assert len(d) == 2
    assert "close" in d.columns


def test_tsmom_exposure_shape():
    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    daily = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0},
        index=idx,
    )
    exp = build_tsmom_exposure(daily, lookbacks=(10, 20, 40), min_votes=2)
    assert len(exp) == n
    assert exp.min() >= 0
    assert exp.max() <= 1.0


def test_compute_tsmom_snapshot():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    daily = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0},
        index=idx,
    )
    snap = compute_tsmom_snapshot(daily, lookbacks=(10, 20, 40), min_votes=2)
    assert "stance" in snap
    assert snap["votes_needed"] == 2
    assert len(snap["lookbacks_detail"]) == 3


def test_tsmom_backtest_engine():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    daily = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0},
        index=idx,
    )
    exp = build_tsmom_exposure(daily)
    eng = BacktestEngine(commission=0.0005, slippage=0.0002)
    r = eng.run_exposure(daily, exp, strategy_name="tsmom_test", periods_per_year=252)
    assert r.strategy_name == "tsmom_test"
    assert -1.0 <= r.max_drawdown <= 0.0
