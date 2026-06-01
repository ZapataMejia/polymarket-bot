"""Phase 5: Gymnasium trading environment for RL agents."""
from __future__ import annotations

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces


class TradingEnv(gym.Env):
    """
    RL environment that simulates trading on OHLCV + features data.

    Actions: 0=HOLD, 1=BUY, 2=SELL
    Observation: feature vector + [position, unrealized_pnl, cash_ratio]
    Reward: change in portfolio Sharpe-adjusted value.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        df: pd.DataFrame,
        initial_capital: float = 10_000.0,
        commission: float = 0.001,
        feature_columns: list[str] | None = None,
        window: int = 1,
    ):
        super().__init__()
        self.df = df.reset_index(drop=True)
        self.initial_capital = initial_capital
        self.commission = commission
        self.window = window

        if feature_columns:
            self.feature_cols = [c for c in feature_columns if c in df.columns]
        else:
            exclude = {"open", "high", "low", "close", "volume"}
            self.feature_cols = [c for c in df.columns if c not in exclude]

        n_features = len(self.feature_cols) + 3  # +position, unrealized_pnl_pct, cash_ratio
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(n_features,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(3)  # HOLD, BUY, SELL

        self._step = 0
        self.position = 0.0
        self.cash = initial_capital
        self.entry_price = 0.0
        self.portfolio_values: list[float] = []

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._step = self.window
        self.position = 0.0
        self.cash = self.initial_capital
        self.entry_price = 0.0
        self.portfolio_values = [self.initial_capital]
        return self._get_obs(), {}

    def _get_obs(self) -> np.ndarray:
        row = self.df.iloc[self._step]
        features = row[self.feature_cols].values.astype(np.float32)
        price = row["close"]
        unrealized = (price - self.entry_price) / price * self.position if self.position != 0 else 0.0
        portfolio_val = self.cash + self.position * price
        cash_ratio = self.cash / max(portfolio_val, 1e-8)
        extra = np.array([self.position, unrealized, cash_ratio], dtype=np.float32)
        obs = np.concatenate([features, extra])
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def step(self, action: int):
        price = self.df.iloc[self._step]["close"]
        reward = 0.0

        if action == 1 and self.position == 0:  # BUY
            cost = price * (1 + self.commission)
            qty = self.cash * 0.95 / cost
            self.position = qty
            self.cash -= qty * cost
            self.entry_price = price

        elif action == 2 and self.position > 0:  # SELL
            revenue = self.position * price * (1 - self.commission)
            self.cash += revenue
            self.position = 0.0
            self.entry_price = 0.0

        portfolio_val = self.cash + self.position * price
        self.portfolio_values.append(portfolio_val)

        if len(self.portfolio_values) >= 2:
            ret = (self.portfolio_values[-1] - self.portfolio_values[-2]) / max(self.portfolio_values[-2], 1e-8)
            reward = ret * 100  # scale for learning

        self._step += 1
        terminated = self._step >= len(self.df) - 1
        truncated = portfolio_val < self.initial_capital * 0.5  # stop if lost 50%

        return self._get_obs(), reward, terminated, truncated, {
            "portfolio_value": portfolio_val,
            "position": self.position,
            "cash": self.cash,
        }

    @property
    def total_return(self) -> float:
        if not self.portfolio_values:
            return 0.0
        return self.portfolio_values[-1] / self.initial_capital - 1

    @property
    def sharpe(self) -> float:
        if len(self.portfolio_values) < 10:
            return 0.0
        rets = np.diff(self.portfolio_values) / np.array(self.portfolio_values[:-1])
        std = np.std(rets)
        if std == 0:
            return 0.0
        return float(np.mean(rets) / std * np.sqrt(252 * 24))
