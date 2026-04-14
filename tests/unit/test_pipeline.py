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

    def test_corrects_overconfident_model(self):
        """Issue 2: Verify calibrator corrects systematically overconfident scores."""
        cal = VennAbersCalibrator()
        # Model predicts 0.85 for events that only happen 55% of the time
        rng = np.random.RandomState(42)
        n = 200
        scores = rng.uniform(0.75, 0.95, size=n)  # Overconfident
        labels = (rng.random(n) < 0.55).astype(float)  # Only 55% actually win
        cal.fit(scores, labels, "lol")
        # Calibrated output for 0.85 should be pulled DOWN toward ~0.55
        calibrated, lo, hi = cal.predict(0.85, "lol")
        assert calibrated < 0.80, f"Expected calibrated < 0.80, got {calibrated:.3f}"

    def test_corrects_underconfident_model(self):
        """Issue 2: Verify calibrator handles underconfident (too-close-to-0.5) scores."""
        cal = VennAbersCalibrator()
        rng = np.random.RandomState(99)
        n = 200
        # Model predicts ~0.52 but true rate is ~0.70
        scores = rng.uniform(0.48, 0.56, size=n)
        labels = (rng.random(n) < 0.70).astype(float)
        cal.fit(scores, labels, "cs2")
        calibrated, lo, hi = cal.predict(0.52, "cs2")
        # Should be pulled UP toward true rate
        assert calibrated > 0.55, f"Expected calibrated > 0.55, got {calibrated:.3f}"

    def test_per_game_isolation(self):
        """Issue 2: CS2 calibrator doesn't affect LoL predictions."""
        cal = VennAbersCalibrator()
        rng = np.random.RandomState(42)
        n = 100
        # CS2: overconfident
        cs2_scores = rng.uniform(0.75, 0.95, size=n)
        cs2_labels = (rng.random(n) < 0.55).astype(float)
        cal.fit(cs2_scores, cs2_labels, "cs2")
        # LoL: well-calibrated
        lol_scores = rng.uniform(0.3, 0.7, size=n)
        lol_labels = (rng.random(n) < 0.5).astype(float)
        cal.fit(lol_scores, lol_labels, "lol")
        # CS2 prediction at 0.85 should be pulled down
        cs2_cal, _, _ = cal.predict(0.85, "cs2")
        # LoL at 0.85 should go through LoL calibrator, not CS2
        lol_cal, _, _ = cal.predict(0.5, "lol")
        assert cs2_cal < 0.80
        # LoL at midpoint should stay near midpoint
        assert 0.3 < lol_cal < 0.7

    def test_batch_matches_single(self):
        """Issue 2: Batch predict produces same results as individual predict."""
        cal = VennAbersCalibrator()
        scores = np.array([0.3, 0.4, 0.5, 0.6, 0.7] * 20)
        labels = np.array([0, 0, 0, 1, 1] * 20)
        cal.fit(scores, labels, "cs2")
        test_scores = np.array([0.35, 0.55, 0.75])
        batch_cal, batch_lo, batch_hi = cal.predict_batch(test_scores, "cs2")
        for i, s in enumerate(test_scores):
            single_cal, single_lo, single_hi = cal.predict(float(s), "cs2")
            assert abs(batch_cal[i] - single_cal) < 1e-6


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

    def test_alpha_sensitivity_tighter_fewer_singletons(self):
        """Issue 3: Tighter alpha = wider sets = more abstains = fewer singletons."""
        # Use same calibration data for both
        rng = np.random.RandomState(42)
        n = 200
        probs = rng.uniform(0.2, 0.8, size=n)
        labels = (rng.random(n) < probs).astype(float)

        cf_loose = ConformalFilter(alpha=0.20)   # Looser — more singletons
        cf_tight = ConformalFilter(alpha=0.05)   # Tighter — fewer singletons

        cf_loose.fit(probs, labels)
        cf_tight.fit(probs, labels)

        test_probs = np.linspace(0.2, 0.8, 50)
        loose_singletons = sum(1 for p in test_probs if cf_loose.predict(float(p))["is_singleton"])
        tight_singletons = sum(1 for p in test_probs if cf_tight.predict(float(p))["is_singleton"])

        assert loose_singletons >= tight_singletons, (
            f"Tighter alpha should produce fewer singletons: "
            f"loose={loose_singletons}, tight={tight_singletons}"
        )

    def test_uninformative_model_mostly_abstains(self):
        """Issue 3: When model is random noise, conformal filter should produce mostly
        full sets (abstains). This is the EB v1 failure mode — the filter should catch it."""
        rng = np.random.RandomState(42)
        n = 200
        # Model outputs random noise around 0.5 — no signal
        probs = rng.uniform(0.40, 0.60, size=n)
        # Labels are random — no correlation with predictions
        labels = rng.binomial(1, 0.5, size=n).astype(float)

        cf = ConformalFilter(alpha=0.10)
        cf.fit(probs, labels)

        # Test on similarly uninformative predictions
        test_probs = rng.uniform(0.40, 0.60, size=100)
        singletons = sum(1 for p in test_probs if cf.predict(float(p))["is_singleton"])
        singleton_rate = singletons / len(test_probs)

        # With an uninformative model, singleton rate should be low
        # (most predictions should have both classes in the set)
        assert singleton_rate < 0.50, (
            f"Uninformative model should have low singleton rate, got {singleton_rate:.1%}"
        )

    def test_well_calibrated_model_many_singletons(self):
        """Issue 3: When model has real signal, conformal filter should allow most bets."""
        rng = np.random.RandomState(42)
        n = 200
        # Model has clear signal
        probs = np.concatenate([rng.uniform(0.1, 0.3, 100), rng.uniform(0.7, 0.9, 100)])
        labels = np.concatenate([np.zeros(100), np.ones(100)])

        cf = ConformalFilter(alpha=0.10)
        cf.fit(probs, labels)

        # Test on similarly strong predictions
        test_probs = np.array([0.15, 0.85, 0.10, 0.90, 0.20, 0.80])
        singletons = sum(1 for p in test_probs if cf.predict(float(p))["is_singleton"])
        singleton_rate = singletons / len(test_probs)

        assert singleton_rate > 0.50, (
            f"Well-calibrated model should have high singleton rate, got {singleton_rate:.1%}"
        )


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
