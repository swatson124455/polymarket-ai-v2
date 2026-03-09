"""Tests for Dota2Model — XGBoost with 6 Glicko-2 features."""
import os
import tempfile

import pytest

from esports.models.dota2_model import Dota2Model, FEATURE_NAMES


def _make_row(team_str_diff=0.1, uncertainty=0.3, rd_asym=0.0,
              vol_a=1.0, vol_b=1.0, best_of=1, outcome=1):
    return {
        "team_strength_diff": team_str_diff,
        "matchup_uncertainty": uncertainty,
        "rd_asymmetry": rd_asym,
        "team_a_volatility": vol_a,
        "team_b_volatility": vol_b,
        "best_of": best_of,
        "team_a_won": outcome,
    }


def _make_training_data(n=100):
    """Generate synthetic training data."""
    import random
    random.seed(42)
    data = []
    for _ in range(n):
        tsd = random.uniform(-0.4, 0.4)
        outcome = 1 if tsd > 0 else 0
        if random.random() < 0.2:
            outcome = 1 - outcome  # noise
        data.append(_make_row(
            team_str_diff=tsd,
            uncertainty=random.uniform(0.1, 0.9),
            rd_asym=random.uniform(-0.5, 0.5),
            vol_a=random.uniform(0.5, 2.0),
            vol_b=random.uniform(0.5, 2.0),
            best_of=random.choice([1, 3, 5]),
            outcome=outcome,
        ))
    return data


class TestDota2Model:
    def test_init(self):
        model = Dota2Model()
        assert not model.is_trained

    def test_heuristic_fallback(self):
        model = Dota2Model()
        prob = model.predict({"team_strength_diff": 0.2})
        assert 0.5 < prob < 0.95

        prob = model.predict({"team_strength_diff": -0.2})
        assert 0.05 < prob < 0.5

        prob = model.predict({"team_strength_diff": 0.0})
        assert 0.45 < prob < 0.55

    def test_heuristic_uncertainty_damping(self):
        model = Dota2Model()
        # High uncertainty should push prediction toward 0.5
        high_unc = model.predict({"team_strength_diff": 0.3, "matchup_uncertainty": 0.9})
        low_unc = model.predict({"team_strength_diff": 0.3, "matchup_uncertainty": 0.1})
        assert high_unc < low_unc  # more uncertain = closer to 0.5

    def test_train_insufficient_data(self):
        model = Dota2Model()
        result = model.train([_make_row()] * 10)
        assert not result
        assert not model.is_trained

    def test_train_sufficient_data(self):
        model = Dota2Model()
        data = _make_training_data(100)
        result = model.train(data)
        assert result
        assert model.is_trained

    def test_predict_after_training(self):
        model = Dota2Model()
        data = _make_training_data(100)
        model.train(data)

        # Positive team strength should favor team A
        prob_a = model.predict({"team_strength_diff": 0.3, "matchup_uncertainty": 0.2,
                                "rd_asymmetry": 0.0, "team_a_volatility": 1.0,
                                "team_b_volatility": 1.0, "best_of": 1})
        # Negative team strength should favor team B
        prob_b = model.predict({"team_strength_diff": -0.3, "matchup_uncertainty": 0.2,
                                "rd_asymmetry": 0.0, "team_a_volatility": 1.0,
                                "team_b_volatility": 1.0, "best_of": 1})
        assert prob_a > prob_b

    def test_predict_clamped(self):
        model = Dota2Model()
        data = _make_training_data(100)
        model.train(data)
        prob = model.predict({"team_strength_diff": 0.5})
        assert 0.01 <= prob <= 0.99

    def test_save_and_load(self):
        model = Dota2Model()
        data = _make_training_data(100)
        model.train(data)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name

        try:
            assert model.save(path)
            model2 = Dota2Model()
            assert model2.load(path)
            assert model2.is_trained

            # Predictions should match
            state = {"team_strength_diff": 0.2, "matchup_uncertainty": 0.3}
            assert abs(model.predict(state) - model2.predict(state)) < 0.01
        finally:
            os.unlink(path)

    def test_load_nonexistent(self):
        model = Dota2Model()
        assert not model.load("/nonexistent/path.pkl")
        assert not model.is_trained

    def test_feature_names(self):
        assert len(FEATURE_NAMES) == 6
        assert "team_strength_diff" in FEATURE_NAMES
        assert "best_of" in FEATURE_NAMES

    def test_extract_features_missing_keys(self):
        model = Dota2Model()
        features = model._extract_features({})
        assert features is not None
        assert len(features) == 6
        assert all(f == 0.0 for f in features)
