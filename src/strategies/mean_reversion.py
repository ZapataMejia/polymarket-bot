from __future__ import annotations

import pandas as pd

from src.strategies.base import SignalDirection, Strategy


class BollingerMeanReversion(Strategy):
    """Enter when price touches Bollinger Band, exit at middle band."""

    name = "bb_mean_reversion"

    def __init__(self, period: int = 20, std_dev: float = 2.0, rsi_oversold: int = 30, rsi_overbought: int = 70):
        self.period = period
        self.std_dev = std_dev
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(SignalDirection.FLAT, index=df.index)

        required = ["bb_lower", "bb_upper", "bb_middle", "rsi_14"]
        if not all(col in df.columns for col in required):
            return signals

        long_cond = (df["close"] <= df["bb_lower"]) & (df["rsi_14"] < self.rsi_oversold)
        short_cond = (df["close"] >= df["bb_upper"]) & (df["rsi_14"] > self.rsi_overbought)

        signals[long_cond] = SignalDirection.LONG
        signals[short_cond] = SignalDirection.SHORT

        return signals
