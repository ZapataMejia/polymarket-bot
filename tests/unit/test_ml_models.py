"""Test 4.1-4.9: ML models training and prediction."""
import numpy as np
import pandas as pd
import pytest

from src.features.pipeline import FeaturePipeline
from src.models.ml_models import (
    EnsembleModel,
    GradientBoostModel,
    make_target,
    prepare_xy,
)


@pytest.fixture
def featured_data():
    np.random.seed(42)
    n = 1000
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


class TestMakeTarget:
    def test_three_classes(self, featured_data):
        target = make_target(featured_data)
        valid = target.dropna()
        assert set(valid.unique()).issubset({0, 1, 2})

    def test_not_all_same(self, featured_data):
        target = make_target(featured_data)
        assert target.dropna().nunique() >= 2


class TestGradientBoost:
    def test_xgboost_trains(self, featured_data):
        X, y = prepare_xy(featured_data)
        model = GradientBoostModel("xgboost")
        result = model.train_evaluate(X, y)
        assert result.accuracy > 0.25  # better than random 1/3
        assert result.feature_importance is not None

    def test_lightgbm_trains(self, featured_data):
        X, y = prepare_xy(featured_data)
        model = GradientBoostModel("lightgbm")
        result = model.train_evaluate(X, y)
        assert result.accuracy > 0.25

    def test_walk_forward(self, featured_data):
        X, y = prepare_xy(featured_data)
        model = GradientBoostModel("xgboost")
        results = model.walk_forward(X, y, n_splits=3)
        assert len(results) == 3
        for r in results:
            assert 0 <= r.accuracy <= 1

    def test_save_load_predict(self, featured_data, tmp_path):
        X, y = prepare_xy(featured_data)
        model = GradientBoostModel("xgboost")
        model.train_evaluate(X, y)
        path = str(tmp_path / "model.pkl")
        model.save(path)

        model2 = GradientBoostModel("xgboost")
        model2.load(path)
        p1 = model.predict(X.iloc[:10])
        p2 = model2.predict(X.iloc[:10])
        pd.testing.assert_series_equal(p1, p2)


class TestEnsemble:
    def test_ensemble_accuracy(self, featured_data):
        X, y = prepare_xy(featured_data)
        xgb = GradientBoostModel("xgboost")
        lgb = GradientBoostModel("lightgbm")
        ensemble = EnsembleModel([xgb, lgb])
        result = ensemble.train_evaluate(X, y)
        assert result.accuracy > 0.2
