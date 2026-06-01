from __future__ import annotations

import pandas as pd

from src.strategies.base import SignalDirection, Strategy


class VolumeBreakout(Strategy):
    """Enter on price breakout from consolidation range with volume confirmation."""

    name = "volume_breakout"

    def __init__(self, lookback: int = 20, volume_mult: float = 1.5):
        self.lookback = lookback
        self.volume_mult = volume_mult

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(SignalDirection.FLAT, index=df.index)

        rolling_high = df["high"].rolling(self.lookback).max()
        rolling_low = df["low"].rolling(self.lookback).min()
        vol_avg = df["volume"].rolling(self.lookback).mean()

        high_volume = df["volume"] > (vol_avg * self.volume_mult)
        breakout_up = (df["close"] > rolling_high.shift(1)) & high_volume
        breakout_down = (df["close"] < rolling_low.shift(1)) & high_volume

        signals[breakout_up] = SignalDirection.LONG
        signals[breakout_down] = SignalDirection.SHORT

        return signals
