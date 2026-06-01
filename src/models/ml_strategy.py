"""Strategy adapter: wraps ML model predictions into the Strategy interface."""
from __future__ import annotations

import pandas as pd

from src.models.ml_models import GradientBoostModel, TARGET_MAP_INV, prepare_xy
from src.strategies.base import SignalDirection, Strategy


class MLStrategy(Strategy):
    """Use a trained GradientBoostModel to generate trading signals."""

    name = "ml_gradient_boost"

    def __init__(self, model: GradientBoostModel, threshold: float = 0.0):
        self.model = model
        self.threshold = threshold

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(SignalDirection.FLAT, index=df.index)
        try:
            X, _ = prepare_xy(df)
            if X.empty:
                return signals
            preds = self.model.predict(X)
            label_map = {0: SignalDirection.SHORT, 1: SignalDirection.FLAT, 2: SignalDirection.LONG}
            for idx, pred in preds.items():
                signals[idx] = label_map.get(int(pred), SignalDirection.FLAT)
        except Exception:
            pass
        return signals
