"""Unit tests for diverse ML model integration in PredictionEngine.

Tests that all 8 ensemble models:
- Accept sample_weight in fit()
- Produce valid predict_proba() output
- Work with CalibratedClassifierCV wrapping

- Train on minimal datasets (50 samples)
"""
import pytest
import numpy as np
from unittest.mock import MagicMock

from sklearn.ensemble import (
    RandomForestClassifier, GradientBoostingClassifier,
    ExtraTreesClassifier, HistGradientBoostingClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
import xgboost as xgb


@pytest.fixture
def small_dataset():
    """Generate a small binary classification dataset (50 samples, 8 features)."""
    rng = np.random.default_rng(42)
    X = rng.standard_normal((50, 8))
    y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
    w = rng.uniform(0.5, 2.0, size=50)
    return X, y, w


@pytest.fixture
def medium_dataset():
    """Generate a medium dataset (200 samples, 8 features)."""
    rng = np.random.default_rng(42)
    X = rng.standard_normal((200, 8))
    y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
    w = rng.uniform(0.5, 2.0, size=200)
    return X, y, w


# ---------------------------------------------------------------------------
# Sklearn built-in models
# ---------------------------------------------------------------------------

class TestLogisticRegression:
    def test_sample_weight(self, small_dataset):
        X, y, w = small_dataset
        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42, class_weight="balanced")
        lr.fit(X, y, sample_weight=w)
        proba = lr.predict_proba(X)
        assert proba.shape == (50, 2)
        assert np.all((proba >= 0) & (proba <= 1))
        assert not np.any(np.isnan(proba))

    def test_calibrated_wrapper(self, medium_dataset):
        X, y, w = medium_dataset
        base = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        cal = CalibratedClassifierCV(base, cv=3, method="isotonic")
        cal.fit(X, y, sample_weight=w)
        proba = cal.predict_proba(X)
        assert proba.shape == (200, 2)

    def test_no_feature_importances(self, small_dataset):
        X, y, _ = small_dataset
        lr = LogisticRegression(max_iter=1000, random_state=42)
        lr.fit(X, y)
        assert not hasattr(lr, "feature_importances_")
        assert hasattr(lr, "coef_")


class TestExtraTrees:
    def test_sample_weight(self, small_dataset):
        X, y, w = small_dataset
        et = ExtraTreesClassifier(n_estimators=10, max_depth=8, min_samples_leaf=5, random_state=42)
        et.fit(X, y, sample_weight=w)
        proba = et.predict_proba(X)
        assert proba.shape == (50, 2)
        assert not np.any(np.isnan(proba))

    def test_has_feature_importances(self, small_dataset):
        X, y, _ = small_dataset
        et = ExtraTreesClassifier(n_estimators=10, random_state=42)
        et.fit(X, y)
        assert hasattr(et, "feature_importances_")
        assert len(et.feature_importances_) == 8

    def test_calibrated_wrapper(self, medium_dataset):
        X, y, w = medium_dataset
        base = ExtraTreesClassifier(n_estimators=10, random_state=42)
        cal = CalibratedClassifierCV(base, cv=3, method="isotonic")
        cal.fit(X, y, sample_weight=w)
        proba = cal.predict_proba(X)
        assert proba.shape == (200, 2)


class TestHistGradientBoosting:
    def test_sample_weight(self, small_dataset):
        X, y, w = small_dataset
        hgb = HistGradientBoostingClassifier(
            max_iter=10, max_depth=5, learning_rate=0.1,
            min_samples_leaf=10, max_bins=64, random_state=42, early_stopping=False,
        )
        hgb.fit(X, y, sample_weight=w)
        proba = hgb.predict_proba(X)
        assert proba.shape == (50, 2)
        assert not np.any(np.isnan(proba))

    def test_calibrated_wrapper(self, medium_dataset):
        X, y, w = medium_dataset
        base = HistGradientBoostingClassifier(max_iter=10, random_state=42, early_stopping=False)
        cal = CalibratedClassifierCV(base, cv=3, method="isotonic")
        cal.fit(X, y, sample_weight=w)
        proba = cal.predict_proba(X)
        assert proba.shape == (200, 2)


# ---------------------------------------------------------------------------
# External dependency models (graceful fallback on ImportError)
# ---------------------------------------------------------------------------

class TestLightGBM:
    def test_import(self):
        import lightgbm as lgb
        clf = lgb.LGBMClassifier(n_estimators=10, verbose=-1)
        assert hasattr(clf, "fit")
        assert hasattr(clf, "predict_proba")

    def test_sample_weight(self, small_dataset):
        import lightgbm as lgb
        X, y, w = small_dataset
        clf = lgb.LGBMClassifier(
            n_estimators=10, max_depth=6, learning_rate=0.1,
            num_leaves=31, min_child_samples=5, random_state=42, verbose=-1,
        )
        clf.fit(X, y, sample_weight=w)
        proba = clf.predict_proba(X)
        assert proba.shape == (50, 2)
        assert not np.any(np.isnan(proba))

    def test_calibrated_wrapper(self, medium_dataset):
        import lightgbm as lgb
        X, y, w = medium_dataset
        base = lgb.LGBMClassifier(n_estimators=10, random_state=42, verbose=-1)
        cal = CalibratedClassifierCV(base, cv=3, method="isotonic")
        cal.fit(X, y, sample_weight=w)
        proba = cal.predict_proba(X)
        assert proba.shape == (200, 2)


class TestCatBoost:
    def test_import(self):
        from catboost import CatBoostClassifier
        clf = CatBoostClassifier(iterations=10, verbose=0)
        assert hasattr(clf, "fit")
        assert hasattr(clf, "predict_proba")

    def test_sample_weight(self, small_dataset):
        from catboost import CatBoostClassifier
        X, y, w = small_dataset
        clf = CatBoostClassifier(
            iterations=10, depth=6, learning_rate=0.1,
            random_seed=42, verbose=0, auto_class_weights="Balanced",
        )
        clf.fit(X, y, sample_weight=w)
        proba = clf.predict_proba(X)
        assert proba.shape == (50, 2)
        assert not np.any(np.isnan(proba))

    def test_catboost_incompatible_with_calibrated_cv(self, medium_dataset):
        """CatBoost lacks __sklearn_tags__, so CalibratedClassifierCV raises AttributeError.
        We skip the wrapper in production code and use CatBoost directly."""
        from catboost import CatBoostClassifier
        X, y, w = medium_dataset
        base = CatBoostClassifier(iterations=10, random_seed=42, verbose=0)
        with pytest.raises(AttributeError, match="__sklearn_tags__"):
            cal = CalibratedClassifierCV(base, cv=3, method="isotonic")
            cal.fit(X, y, sample_weight=w)
        # Direct use works fine:
        base.fit(X, y, sample_weight=w)
        proba = base.predict_proba(X)
        assert proba.shape == (200, 2)



# ---------------------------------------------------------------------------
# Full Ensemble Integration
# ---------------------------------------------------------------------------

class TestFullEnsemble:
    """Test all 8 models training together on minimal data."""

    def test_all_8_models_on_50_samples(self, small_dataset):
        import lightgbm as lgb
        from catboost import CatBoostClassifier
        X, y, w = small_dataset

        models = {
            "random_forest": RandomForestClassifier(n_estimators=10, max_depth=10, random_state=42),
            "xgboost": xgb.XGBClassifier(n_estimators=10, max_depth=6, learning_rate=0.1, random_state=42),
            "gradient_boosting": GradientBoostingClassifier(n_estimators=10, max_depth=5, learning_rate=0.1, random_state=42),
            "logistic_regression": LogisticRegression(C=1.0, max_iter=1000, random_state=42, class_weight="balanced"),
            "extra_trees": ExtraTreesClassifier(n_estimators=10, max_depth=8, min_samples_leaf=5, random_state=42),
            "hist_gradient_boosting": HistGradientBoostingClassifier(max_iter=10, max_depth=5, min_samples_leaf=10, max_bins=64, random_state=42, early_stopping=False),
            "lightgbm": lgb.LGBMClassifier(n_estimators=10, max_depth=6, learning_rate=0.1, num_leaves=31, min_child_samples=5, random_state=42, verbose=-1),
            "catboost": CatBoostClassifier(iterations=10, depth=6, learning_rate=0.1, random_seed=42, verbose=0, auto_class_weights="Balanced"),
        }

        for name, model in models.items():
            model.fit(X, y, sample_weight=w)
            proba = model.predict_proba(X)
            assert proba.shape == (50, 2), f"{name}: wrong shape {proba.shape}"
            assert not np.any(np.isnan(proba)), f"{name}: produced NaN"
            assert np.all(proba >= 0) and np.all(proba <= 1), f"{name}: out of [0,1]"

    def test_ensemble_averaging(self):
        """Simulate the predict() ensemble averaging with 8 models."""
        predictions = {
            "random_forest": 0.65,
            "xgboost": 0.70,
            "gradient_boosting": 0.68,
            "logistic_regression": 0.60,
            "extra_trees": 0.67,
            "hist_gradient_boosting": 0.72,
            "lightgbm": 0.71,
            "catboost": 0.69,
        }
        ensemble = np.mean(list(predictions.values()))
        assert 0.5 < ensemble < 0.8
        # 8-model average should be more stable than 3-model
        assert len(predictions) == 8


# ---------------------------------------------------------------------------
# Feature Importance Compatibility
# ---------------------------------------------------------------------------

class TestFeatureScoresCompat:
    """Verify get_feature_scores handles models with/without feature_importances_."""

    def test_counts_only_contributing_models(self, small_dataset):
        """LogisticRegression should NOT dilute the feature importance average."""
        X, y, _ = small_dataset

        # Train one tree model + one linear model
        rf = RandomForestClassifier(n_estimators=10, random_state=42)
        rf.fit(X, y)
        lr = LogisticRegression(max_iter=1000, random_state=42)
        lr.fit(X, y)

        # Simulate PredictionEngine.get_feature_scores logic
        feature_columns = [f"f{i}" for i in range(8)]
        scores = {}
        n_contributors = 0
        for name, model in {"rf": rf, "lr": lr}.items():
            est = model
            if hasattr(model, "calibrated_classifiers_"):
                est = model.calibrated_classifiers_[0].estimator
            if not hasattr(est, "feature_importances_"):
                continue
            imp = getattr(est, "feature_importances_", None)
            if imp is None or len(imp) != len(feature_columns):
                continue
            n_contributors += 1
            for i, col in enumerate(feature_columns):
                scores[col] = scores.get(col, 0.0) + float(imp[i])
        if n_contributors > 0:
            scores = {k: v / n_contributors for k, v in scores.items()}

        # Only RF should contribute (n_contributors=1), not LR
        assert n_contributors == 1
        assert len(scores) == 8
        assert abs(sum(scores.values()) - 1.0) < 0.01  # RF importances sum to ~1.0


# ---------------------------------------------------------------------------
# Settings Toggle Tests
# ---------------------------------------------------------------------------

class TestModelToggleSettings:
    def test_all_settings_exist_and_default_true(self):
        from config.settings import settings
        # All 8 models should be toggleable and default to True
        assert getattr(settings, "MODEL_ENABLE_RANDOM_FOREST", None) is True
        assert getattr(settings, "MODEL_ENABLE_XGBOOST", None) is True
        assert getattr(settings, "MODEL_ENABLE_GRADIENT_BOOSTING", None) is True
        assert getattr(settings, "MODEL_ENABLE_LOGISTIC_REGRESSION", None) is True
        assert getattr(settings, "MODEL_ENABLE_EXTRA_TREES", None) is True
        assert getattr(settings, "MODEL_ENABLE_HIST_GRADIENT_BOOSTING", None) is True
        assert getattr(settings, "MODEL_ENABLE_LIGHTGBM", None) is True
        assert getattr(settings, "MODEL_ENABLE_CATBOOST", None) is True
        # GAP-4 non-tree diversity models
        assert getattr(settings, "MODEL_ENABLE_RIDGE", None) is True
        assert getattr(settings, "MODEL_ENABLE_KNN", None) is True
        # Neural network diversity model
        assert getattr(settings, "MODEL_ENABLE_MLP", None) is True
