"""Phase 4: ML models for signal prediction (XGBoost, LightGBM, LSTM-stub)."""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import TimeSeriesSplit

logger = logging.getLogger("trading.models")

TARGET_MAP = {"DOWN": 0, "NEUTRAL": 1, "UP": 2}
TARGET_MAP_INV = {v: k for k, v in TARGET_MAP.items()}


def make_target(
    df: pd.DataFrame,
    horizon: int = 5,
    up_thresh: float = 0.002,
    down_thresh: float = -0.002,
) -> pd.Series:
    """Create classification target: UP / DOWN / NEUTRAL based on future returns."""
    future_ret = df["close"].pct_change(horizon).shift(-horizon)
    target = pd.Series("NEUTRAL", index=df.index)
    target[future_ret > up_thresh] = "UP"
    target[future_ret < down_thresh] = "DOWN"
    return target.map(TARGET_MAP)


def prepare_xy(
    df: pd.DataFrame,
    exclude_cols: list[str] | None = None,
    horizon: int = 5,
) -> tuple[pd.DataFrame, pd.Series]:
    exclude = set(exclude_cols or ["open", "high", "low", "close", "volume"])
    y = make_target(df, horizon=horizon)
    X = df[[c for c in df.columns if c not in exclude]].copy()
    mask = y.notna() & X.notna().all(axis=1)
    return X[mask], y[mask].astype(int)


@dataclass
class ModelResult:
    name: str
    accuracy: float
    report: str
    feature_importance: dict[str, float] | None
    predictions: pd.Series


class GradientBoostModel:
    """XGBoost or LightGBM classifier for UP/DOWN/NEUTRAL prediction."""

    def __init__(self, engine: str = "xgboost", **params):
        self.engine = engine
        defaults = {
            "n_estimators": 200,
            "max_depth": 5,
            "learning_rate": 0.05,
            "random_state": 42,
        }
        defaults.update(params)

        if engine == "xgboost":
            from xgboost import XGBClassifier
            self.model = XGBClassifier(**defaults, eval_metric="mlogloss")
        else:
            from lightgbm import LGBMClassifier
            defaults["verbosity"] = -1
            self.model = LGBMClassifier(**defaults)

        self.feature_names: list[str] = []

    def train_evaluate(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        train_ratio: float = 0.7,
    ) -> ModelResult:
        split = int(len(X) * train_ratio)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]

        self.feature_names = list(X.columns)
        self.model.fit(X_train, y_train)

        preds = pd.Series(self.model.predict(X_test), index=X_test.index)
        acc = accuracy_score(y_test, preds)
        report = classification_report(y_test, preds, target_names=list(TARGET_MAP.keys()))

        importance = dict(zip(
            self.feature_names,
            self.model.feature_importances_.tolist(),
        ))
        importance = dict(sorted(importance.items(), key=lambda x: -x[1])[:15])

        logger.info("[%s] Accuracy: %.3f (train=%d, test=%d)", self.engine, acc, len(X_train), len(X_test))
        return ModelResult(
            name=self.engine,
            accuracy=acc,
            report=report,
            feature_importance=importance,
            predictions=preds,
        )

    def walk_forward(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        n_splits: int = 5,
    ) -> list[ModelResult]:
        tscv = TimeSeriesSplit(n_splits=n_splits)
        results = []
        for i, (train_idx, test_idx) in enumerate(tscv.split(X)):
            X_tr, X_te = X.iloc[train_idx], X.iloc[test_idx]
            y_tr, y_te = y.iloc[train_idx], y.iloc[test_idx]
            self.model.fit(X_tr, y_tr)
            preds = pd.Series(self.model.predict(X_te), index=X_te.index)
            acc = accuracy_score(y_te, preds)
            results.append(ModelResult(
                name=f"{self.engine}_fold_{i}",
                accuracy=acc,
                report="",
                feature_importance=None,
                predictions=preds,
            ))
            logger.info("[%s] Fold %d: accuracy=%.3f", self.engine, i, acc)
        return results

    def save(self, path: str = "data/models/gb_model.pkl") -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "features": self.feature_names}, f)

    def load(self, path: str = "data/models/gb_model.pkl") -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.feature_names = data["features"]

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(self.model.predict(X[self.feature_names]), index=X.index)


class EnsembleModel:
    """Combine multiple models via majority vote."""

    def __init__(self, models: list[GradientBoostModel]):
        self.models = models

    def predict(self, X: pd.DataFrame) -> pd.Series:
        all_preds = pd.DataFrame({
            f"m{i}": m.predict(X) for i, m in enumerate(self.models)
        })
        return all_preds.mode(axis=1)[0].astype(int)

    def train_evaluate(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        train_ratio: float = 0.7,
    ) -> ModelResult:
        split = int(len(X) * train_ratio)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]

        for m in self.models:
            m.feature_names = list(X.columns)
            m.model.fit(X_train, y_train)

        preds = self.predict(X_test)
        acc = accuracy_score(y_test, preds)
        report = classification_report(y_test, preds, target_names=list(TARGET_MAP.keys()))

        logger.info("[ensemble] Accuracy: %.3f", acc)
        return ModelResult(
            name="ensemble",
            accuracy=acc,
            report=report,
            feature_importance=None,
            predictions=preds,
        )
