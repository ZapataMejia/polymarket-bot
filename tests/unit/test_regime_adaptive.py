import numpy as np
import pandas as pd

from src.features.pipeline import FeaturePipeline
from src.strategies.backtest import BacktestEngine
from src.strategies.regime_adaptive import (
    RegimeAdaptiveConservative,
    RegimeAdaptiveUltraConservative,
)


def test_regime_adaptive_runs():
    np.random.seed(42)
    n = 800
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.randint(1000, 10000, n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    features = FeaturePipeline(warmup_periods=200).build(df)
    strat = RegimeAdaptiveConservative(allow_short=True)
    sig = strat.generate_signals(features)
    assert len(sig) == len(features)
    engine = BacktestEngine()
    r = engine.run(strat, features)
    assert r.total_trades >= 0
    assert -1.0 <= r.max_drawdown <= 0.0


def test_regime_ultra_runs():
    np.random.seed(43)
    n = 800
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.randint(1000, 10000, n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    features = FeaturePipeline(warmup_periods=200).build(df)
    strat = RegimeAdaptiveUltraConservative(allow_short=True)
    sig = strat.generate_signals(features)
    assert len(sig) == len(features)
    engine = BacktestEngine()
    r = engine.run(strat, features)
    assert r.total_trades >= 0
