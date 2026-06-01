from __future__ import annotations

import pandas as pd

from src.strategies.base import SignalDirection, Strategy


class EMACrossover(Strategy):
    """Enter on EMA crossover confirmed by higher timeframe trend."""

    name = "ema_crossover"

    def __init__(self, fast: int = 9, slow: int = 21, trend: int = 200):
        self.fast = fast
        self.slow = slow
        self.trend = trend

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(SignalDirection.FLAT, index=df.index)

        fast_col = f"ema_{self.fast}"
        slow_col = f"ema_{self.slow}"
        trend_col = f"ema_{self.trend}"

        if not all(col in df.columns for col in [fast_col, slow_col, trend_col]):
            return signals

        bullish_cross = (df[fast_col] > df[slow_col]) & (df[fast_col].shift(1) <= df[slow_col].shift(1))
        bearish_cross = (df[fast_col] < df[slow_col]) & (df[fast_col].shift(1) >= df[slow_col].shift(1))

        uptrend = df["close"] > df[trend_col]
        downtrend = df["close"] < df[trend_col]

        signals[bullish_cross & uptrend] = SignalDirection.LONG
        signals[bearish_cross & downtrend] = SignalDirection.SHORT

        return signals
