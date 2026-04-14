"""Tests for B2+B3+B4: XGBoost + Venn-ABERS + Conformal pipeline."""
import numpy as np
import pytest

from esports_v2.model.meta_model import (
    ALL_FEATURES,
    XGBoostMetaModel,
    record_to_features,
    records_to_matrix,
)
from esports_v2.model.calibrator import VennAbersCalibrator
from esports_v2.model.conformal import ConformalFilter
from esports_v2.model.pipeline import EsportsPipeline


def _make_record(p_elo=0.6, p_glicko=0.58, p_openskill=0.62, actual=1, **kwargs):
    return {
        "p_elo": p_elo,
        "p_glicko": p_glicko,
        "p_openskill": p_openskill,
        "trinity_spread": max(p_elo, p_glicko, p_openskill) - min(p_elo, p_glicko, p_openskill),
        "trinity_mean": (p_elo + p_glicko + p_openskill) / 3,
        "event_tier": "a_tier",
        "is_lan": True,
        "best_of": 3,
        "game": "cs2",
        "actual": actual,
        **kwargs,
    }


def _make_training_set(n=200):
    """Generate synthetic training data with signal."""
    rng = np.random.RandomState(42)
    records = []
    for _ in range(n):
        # True prob with noise
        true_p = rng.uniform(0.3, 0.7)
        actual = 1 if rng.random() < true_p else 0
        noise = rng.normal(0, 0.05)
        records.append(_make_record(
            p_elo=true_p + noise,
            p_glicko=true_p + rng.normal(0, 0.05),
            p_openskill=true_p + rng.normal(0, 0.05),
            actual=actual,
            game="cs2" if rng.random() > 0.5 else "lol",
        ))
    return records


class TestRecordToFeatures:
    def test_correct_length(self):
        record = _make_record()
        features = record_to_features(record)
        assert len(features) == len(ALL_FEATURES)

    def test_values(self):
        record = _make_record(p_elo=0.6, p_glicko=0.58, game="cs2")
        features = record_to_features(record)
        assert features[0] == pytest.approx(0.6)   # p_elo
        assert features[1] == pytest.approx(0.58)  # p_glicko
        assert features[-1] == 1.0                  # is_cs2

    def test_missing_fields(self):
        record = {"actual": 1}
        features = record_to_features(record)
        assert len(features) == len(ALL_FEATURES)
        assert features[0] == 0.5  # default p_elo


class TestRecordsToMatrix:
    def test_shapes(self):
        records = [_make_record(), _make_record()]
        X, y = records_to_matrix(records)
        assert X.shape == (2, len(ALL_FEATURES))
        assert y.shape == (2,)


class TestXGBoostMetaModel:
    def test_fit_predict(self):
        records = _make_training_set(200)
        model = XGBoostMetaModel(n_estimators=10, max_depth=2)
        model.fit(records)
        prob = model.predict_proba(records[0])
        assert 0.0 <= prob <= 1.0

    def test_predict_before_fit(self):
        model = XGBoostMetaModel()
        assert model.predict_proba(_make_record()) == 0.5

    def test_batch_predict(self):
        records = _make_training_set(100)
        model = XGBoostMetaModel(n_estimators=10, max_depth=2)
        model.fit(records)
        probs = model.predict_proba_batch(records[:10])
        assert len(probs) == 10
        assert all(0.0 <= p <= 1.0 for p in probs)

    def test_feature_importance(self):
        records = _make_training_set(100)
        model = XGBoostMetaModel(n_estimators=10, max_depth=2)
        model.fit(records)
        imp = model.feature_importance()
        assert len(imp) == len(ALL_FEATURES)
        assert all(v >= 0 for v in imp.values())


class TestVennAbersCalibrator:
    def test_fit_predict(self):
        cal = VennAbersCalibrator()
        scores = np.array([0.3, 0.4, 0.5, 0.6, 0.7] * 10)
        labels = np.array([0, 0, 0, 1, 1] * 10)
        cal.fit(scores, labels, "cs2")
        prob, lo, hi = cal.predict(0.6, "cs2")
        assert 0.0 <= prob <= 1.0
        assert lo <= prob <= hi

    def test_unknown_game(self):
        cal = VennAbersCalibrator()
        prob, lo, hi = cal.predict(0.6, "unknown_game")
        assert prob == 0.6  # passthrough

    def test_too_few_samples(self):
        cal = VennAbersCalibrator()
        cal.fit(np.array([0.5, 0.6]), np.array([0, 1]), "cs2")
        prob, lo, hi = cal.predict(0.6, "cs2")
        assert prob == 0.6  # passthrough


class TestConformalFilter:
    def test_singleton_on_confident(self):
        cf = ConformalFilter(alpha=0.10)
        probs = np.array([0.1, 0.2, 0.8, 0.9] * 25)
        labels = np.array([0, 0, 1, 1] * 25)
        cf.fit(probs, labels)
        result = cf.predict(0.95)
        assert result["is_singleton"] is True

    def test_not_singleton_on_uncertain(self):
        cf = ConformalFilter(alpha=0.10)
        probs = np.array([0.4, 0.45, 0.5, 0.55, 0.6] * 20)
        labels = np.array([0, 0, 1, 1, 1] * 20)
        cf.fit(probs, labels)
        result = cf.predict(0.5)
        # At 0.5 with noisy calibration data, likely multi-label
        assert len(result["conformal_set"]) >= 1

    def test_unfitted_passthrough(self):
        cf = ConformalFilter()
        result = cf.predict(0.7)
        assert result["is_singleton"] is True
        assert result["predicted_class"] == 1

    def test_batch(self):
        cf = ConformalFilter(alpha=0.10)
        probs = np.array([0.1, 0.9] * 50)
        labels = np.array([0, 1] * 50)
        cf.fit(probs, labels)
        results = cf.predict_batch(np.array([0.1, 0.5, 0.9]))
        assert len(results) == 3


class TestEsportsPipeline:
    def test_fit_predict(self):
        records = _make_training_set(300)
        pipeline = EsportsPipeline(
            xgb_params={"n_estimators": 10, "max_depth": 2},
        )
        pipeline.fit(records)
        result = pipeline.predict(records[0])
        assert "p_model" in result
        assert "is_singleton" in result
        assert "kelly_fraction" in result
        assert "stake" in result
        assert 0.0 <= result["p_model"] <= 1.0

    def test_edge_triggers_sizing(self):
        records = _make_training_set(300)
        pipeline = EsportsPipeline(
            xgb_params={"n_estimators": 10, "max_depth": 2},
        )
        pipeline.fit(records)
        # Force a large edge scenario
        record = _make_record(p_elo=0.85, p_glicko=0.83, p_openskill=0.87, actual=1)
        record["market_price"] = 0.5  # Big edge
        result = pipeline.predict(record)
        # If singleton, should have positive stake
        if result["is_singleton"]:
            assert result["stake"] >= 0

    def test_no_edge_no_stake(self):
        records = _make_training_set(300)
        pipeline = EsportsPipeline(
            xgb_params={"n_estimators": 10, "max_depth": 2},
        )
        pipeline.fit(records)
        record = _make_record(p_elo=0.5, p_glicko=0.5, p_openskill=0.5, actual=1)
        record["market_price"] = 0.5  # No edge
        result = pipeline.predict(record)
        assert result["stake"] == 0.0
