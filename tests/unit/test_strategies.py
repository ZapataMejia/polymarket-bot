"""Test 3.1-3.10: Strategy signals and backtesting."""
import numpy as np
import pandas as pd
import pytest

from src.features.pipeline import FeaturePipeline
from src.strategies.backtest import BacktestEngine, walk_forward_backtest
from src.strategies.base import SignalDirection
from src.strategies.breakout import VolumeBreakout
from src.strategies.mean_reversion import BollingerMeanReversion
from src.strategies.trend_following import EMACrossover


@pytest.fixture
def featured_data():
    np.random.seed(42)
    n = 1000
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
    pipe = FeaturePipeline(warmup_periods=200)
    return pipe.build(df)


class TestBacktestEngine:
    def test_generates_trades(self, featured_data):
        engine = BacktestEngine()
        strat = EMACrossover()
        result = engine.run(strat, featured_data)
        assert result.total_trades >= 0
        assert result.equity_curve is not None
        assert len(result.equity_curve) == len(featured_data)

    def test_metrics_reasonable(self, featured_data):
        engine = BacktestEngine()
        strat = EMACrossover()
        result = engine.run(strat, featured_data)
        assert -1.0 <= result.max_drawdown <= 0.0
        assert 0.0 <= result.win_rate <= 1.0
        assert result.profit_factor >= 0.0

    def test_commissions_reduce_profit(self, featured_data):
        no_cost = BacktestEngine(commission=0.0, slippage=0.0)
        with_cost = BacktestEngine(commission=0.002, slippage=0.001)
        strat = EMACrossover()
        r1 = no_cost.run(strat, featured_data)
        r2 = with_cost.run(strat, featured_data)
        assert r2.total_return <= r1.total_return


class TestMeanReversion:
    def test_generates_signals(self, featured_data):
        strat = BollingerMeanReversion()
        signals = strat.generate_signals(featured_data)
        assert len(signals) == len(featured_data)
        unique = set(signals.unique())
        assert SignalDirection.FLAT in unique

    def test_backtest_runs(self, featured_data):
        engine = BacktestEngine()
        result = engine.run(BollingerMeanReversion(), featured_data)
        assert result.strategy_name == "bb_mean_reversion"


class TestTrendFollowing:
    def test_generates_signals(self, featured_data):
        strat = EMACrossover()
        signals = strat.generate_signals(featured_data)
        assert len(signals) == len(featured_data)

    def test_backtest_runs(self, featured_data):
        engine = BacktestEngine()
        result = engine.run(EMACrossover(), featured_data)
        assert result.strategy_name == "ema_crossover"


class TestBreakout:
    def test_generates_signals(self, featured_data):
        strat = VolumeBreakout()
        signals = strat.generate_signals(featured_data)
        assert len(signals) == len(featured_data)


class TestWalkForward:
    def test_walk_forward_returns_results(self, featured_data):
        strat = EMACrossover()
        results = walk_forward_backtest(strat, featured_data, n_splits=3)
        assert len(results) >= 1
        for r in results:
            assert r.total_trades >= 0
