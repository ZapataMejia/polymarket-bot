"""Phase 5: Simple PPO-style RL agent using only numpy (no torch dependency)."""
from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.rl_env import TradingEnv
from src.strategies.base import SignalDirection, Strategy

logger = logging.getLogger("trading.models.rl")


class SimpleRLAgent:
    """
    Lightweight Q-learning agent for trading.
    No heavy dependencies — uses discretized state space + Q-table.
    For production, swap with stable-baselines3 PPO.
    """

    def __init__(
        self,
        n_features: int,
        n_actions: int = 3,
        n_bins: int = 10,
        learning_rate: float = 0.1,
        gamma: float = 0.99,
        epsilon: float = 1.0,
        epsilon_decay: float = 0.995,
        epsilon_min: float = 0.01,
    ):
        self.n_bins = n_bins
        self.n_actions = n_actions
        self.lr = learning_rate
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = epsilon_min
        self.n_features = min(n_features, 8)  # use top 8 features to keep Q-table manageable
        self.q_table: dict[tuple, np.ndarray] = {}
        self.bins: list[np.ndarray] = []

    def _discretize(self, obs: np.ndarray) -> tuple:
        obs_trimmed = obs[:self.n_features]
        if not self.bins:
            return tuple(np.clip(np.round(obs_trimmed, 1), -5, 5))
        state = []
        for i, val in enumerate(obs_trimmed):
            state.append(int(np.digitize(val, self.bins[i])))
        return tuple(state)

    def _get_q(self, state: tuple) -> np.ndarray:
        if state not in self.q_table:
            self.q_table[state] = np.zeros(self.n_actions)
        return self.q_table[state]

    def act(self, obs: np.ndarray) -> int:
        if np.random.random() < self.epsilon:
            return np.random.randint(self.n_actions)
        state = self._discretize(obs)
        return int(np.argmax(self._get_q(state)))

    def learn(self, obs: np.ndarray, action: int, reward: float, next_obs: np.ndarray, done: bool):
        state = self._discretize(obs)
        next_state = self._discretize(next_obs)
        q = self._get_q(state)
        next_q = self._get_q(next_state)
        target = reward + (0 if done else self.gamma * np.max(next_q))
        q[action] += self.lr * (target - q[action])
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

    def train(self, env: TradingEnv, episodes: int = 100) -> list[float]:
        """Train agent and return episode returns."""
        returns = []
        for ep in range(episodes):
            obs, _ = env.reset()
            total_reward = 0.0
            done = False
            while not done:
                action = self.act(obs)
                next_obs, reward, terminated, truncated, info = env.step(action)
                self.learn(obs, action, reward, next_obs, terminated or truncated)
                obs = next_obs
                total_reward += reward
                done = terminated or truncated
            returns.append(env.total_return)
            if (ep + 1) % 20 == 0:
                avg = np.mean(returns[-20:])
                logger.info("Episode %d/%d: avg_return=%.3f, epsilon=%.3f", ep + 1, episodes, avg, self.epsilon)
        return returns

    def save(self, path: str = "data/models/rl_agent.pkl") -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"q_table": self.q_table, "bins": self.bins, "params": {
                "n_features": self.n_features, "n_actions": self.n_actions,
                "n_bins": self.n_bins, "epsilon": self.epsilon,
            }}, f)

    def load(self, path: str = "data/models/rl_agent.pkl") -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.q_table = data["q_table"]
        self.bins = data["bins"]


class RLStrategy(Strategy):
    """Wrap a trained RL agent as a Strategy."""

    name = "rl_agent"

    def __init__(self, agent: SimpleRLAgent, feature_columns: list[str]):
        self.agent = agent
        self.feature_cols = feature_columns
        self.agent.epsilon = 0.0  # greedy at inference

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(SignalDirection.FLAT, index=df.index)
        action_map = {0: SignalDirection.FLAT, 1: SignalDirection.LONG, 2: SignalDirection.SHORT}
        for i in range(len(df)):
            row = df.iloc[i]
            features = row[self.feature_cols].values.astype(np.float32)
            extra = np.array([0.0, 0.0, 1.0], dtype=np.float32)
            obs = np.concatenate([features, extra])
            obs = np.nan_to_num(obs, nan=0.0)
            action = self.agent.act(obs)
            signals.iloc[i] = action_map[action]
        return signals
