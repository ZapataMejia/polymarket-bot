"""Test 5.1-5.8: RL environment and agent."""
import numpy as np
import pandas as pd
import pytest

from src.features.pipeline import FeaturePipeline
from src.models.rl_agent import SimpleRLAgent, RLStrategy
from src.models.rl_env import TradingEnv
from src.strategies.base import SignalDirection


@pytest.fixture
def featured_data():
    np.random.seed(42)
    n = 500
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
    return FeaturePipeline(warmup_periods=200).build(df)


class TestTradingEnv:
    def test_reset_and_step(self, featured_data):
        env = TradingEnv(featured_data)
        obs, info = env.reset()
        assert obs.shape[0] > 0
        assert not np.any(np.isnan(obs))

        obs2, reward, term, trunc, info = env.step(0)  # HOLD
        assert obs2.shape == obs.shape
        assert isinstance(reward, float)

    def test_buy_sell_cycle(self, featured_data):
        env = TradingEnv(featured_data)
        env.reset()
        env.step(1)  # BUY
        assert env.position > 0
        env.step(2)  # SELL
        assert env.position == 0

    def test_actions_valid(self, featured_data):
        env = TradingEnv(featured_data)
        assert env.action_space.n == 3

    def test_episode_terminates(self, featured_data):
        env = TradingEnv(featured_data)
        obs, _ = env.reset()
        done = False
        steps = 0
        while not done:
            obs, _, term, trunc, _ = env.step(0)
            done = term or trunc
            steps += 1
        assert steps > 0


class TestRLAgent:
    def test_agent_trains(self, featured_data):
        env = TradingEnv(featured_data)
        agent = SimpleRLAgent(n_features=env.observation_space.shape[0])
        returns = agent.train(env, episodes=10)
        assert len(returns) == 10

    def test_agent_improves_or_stable(self, featured_data):
        env = TradingEnv(featured_data)
        agent = SimpleRLAgent(n_features=env.observation_space.shape[0])
        returns = agent.train(env, episodes=30)
        early = np.mean(returns[:5])
        late = np.mean(returns[-5:])
        assert late >= early - 0.5  # should not catastrophically degrade

    def test_save_load(self, featured_data, tmp_path):
        env = TradingEnv(featured_data)
        agent = SimpleRLAgent(n_features=env.observation_space.shape[0])
        agent.train(env, episodes=5)
        path = str(tmp_path / "agent.pkl")
        agent.save(path)
        agent2 = SimpleRLAgent(n_features=env.observation_space.shape[0])
        agent2.load(path)
        assert len(agent2.q_table) == len(agent.q_table)


class TestRLStrategy:
    def test_generates_signals(self, featured_data):
        env = TradingEnv(featured_data)
        agent = SimpleRLAgent(n_features=env.observation_space.shape[0])
        agent.train(env, episodes=5)
        exclude = {"open", "high", "low", "close", "volume"}
        feat_cols = [c for c in featured_data.columns if c not in exclude]
        strat = RLStrategy(agent, feat_cols)
        signals = strat.generate_signals(featured_data.iloc[:20])
        assert len(signals) == 20
        assert all(isinstance(s, SignalDirection) for s in signals)
