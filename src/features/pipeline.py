from __future__ import annotations

import logging

import pandas as pd

from src.features.indicators import (
    atr,
    bollinger_bands,
    ema,
    hurst_exponent,
    macd,
    obv,
    order_flow_imbalance,
    realized_volatility,
    rsi,
    vwap,
)

logger = logging.getLogger("trading.features.pipeline")


class FeaturePipeline:
    """Transforms raw OHLCV data into a feature matrix for ML models."""

    def __init__(self, warmup_periods: int = 200):
        self.warmup = warmup_periods

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        features = df[["open", "high", "low", "close", "volume"]].copy()

        features["returns"] = features["close"].pct_change()
        features["log_returns"] = features["close"].pipe(
            lambda s: (s / s.shift(1)).apply(lambda x: __import__("numpy").log(x) if x > 0 else 0)
        )

        for period in [7, 14, 21]:
            features[f"rsi_{period}"] = rsi(features["close"], period)

        macd_df = macd(features["close"])
        features = pd.concat([features, macd_df], axis=1)

        bb_df = bollinger_bands(features["close"])
        features = pd.concat([features, bb_df], axis=1)

        for period in [9, 21, 50, 200]:
            features[f"ema_{period}"] = ema(features["close"], period)

        for period in [7, 14, 21]:
            features[f"atr_{period}"] = atr(features, period)

        features["obv"] = obv(features)
        features["vwap"] = vwap(features)
        features["ofi"] = order_flow_imbalance(features)

        for period in [10, 20, 60]:
            features[f"rvol_{period}"] = realized_volatility(features["close"], period)

        for period in [5, 10, 20]:
            features[f"vol_ma_{period}"] = features["volume"].rolling(period).mean()

        features["vol_ratio"] = features["volume"] / features["volume"].rolling(20).mean()

        for lag in [1, 2, 3, 5, 10]:
            features[f"return_lag_{lag}"] = features["returns"].shift(lag)

        for period in [5, 10, 20]:
            features[f"return_rolling_{period}"] = features["returns"].rolling(period).sum()

        features["close_to_ema50"] = (features["close"] - features["ema_50"]) / features["ema_50"]
        features["close_to_ema200"] = (features["close"] - features["ema_200"]) / features["ema_200"]

        features["high_low_range"] = (features["high"] - features["low"]) / features["close"]

        features["hour"] = features.index.hour if hasattr(features.index, "hour") else 0
        features["day_of_week"] = features.index.dayofweek if hasattr(features.index, "dayofweek") else 0

        features = features.iloc[self.warmup:]
        nan_cols = features.columns[features.isna().all()]
        if len(nan_cols) > 0:
            logger.warning("Dropping all-NaN columns: %s", list(nan_cols))
            features = features.drop(columns=nan_cols)

        logger.info("Feature pipeline: %d rows, %d features", len(features), len(features.columns))
        return features

    def normalize(self, df: pd.DataFrame, exclude: list[str] | None = None) -> pd.DataFrame:
        """Z-score normalization, excluding specified columns."""
        exclude = exclude or ["open", "high", "low", "close", "volume", "hour", "day_of_week"]
        cols_to_norm = [c for c in df.columns if c not in exclude]
        result = df.copy()
        for col in cols_to_norm:
            mean = result[col].mean()
            std = result[col].std()
            if std > 0:
                result[f"{col}_z"] = (result[col] - mean) / std
        return result
