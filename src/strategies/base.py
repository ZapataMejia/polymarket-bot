from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class SignalDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass
class Signal:
    direction: SignalDirection
    strength: float  # 0.0 to 1.0
    price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    metadata: dict | None = None


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str = "base"

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Return a Series of SignalDirection values aligned with the DataFrame index."""
        ...

    def backtest_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = self.generate_signals(df)
        result = df[["close"]].copy()
        result["signal"] = signals
        result["position"] = result["signal"].map(
            {SignalDirection.LONG: 1, SignalDirection.SHORT: -1, SignalDirection.FLAT: 0}
        )
        result["returns"] = result["close"].pct_change()
        result["strategy_returns"] = result["position"].shift(1) * result["returns"]
        result["cumulative"] = (1 + result["strategy_returns"]).cumprod()
        result["buy_hold"] = (1 + result["returns"]).cumprod()
        return result
