"""Tests for ValorantModel — XGBoost with 6 Glicko-2 features."""
import os
import tempfile

import pytest

from esports.models.valorant_model import ValorantModel, FEATURE_NAMES


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


class TestValorantModel:
    def test_init(self):
        model = ValorantModel()
        assert not model.is_trained

    def test_heuristic_fallback(self):
        model = ValorantModel()
        prob = model.predict({"team_strength_diff": 0.2})
        assert 0.5 < prob < 0.95

        prob = model.predict({"team_strength_diff": -0.2})
        assert 0.05 < prob < 0.5

        prob = model.predict({"team_strength_diff": 0.0})
        assert 0.45 < prob < 0.55

    def test_heuristic_uncertainty_damping(self):
        model = ValorantModel()
        high_unc = model.predict({"team_strength_diff": 0.3, "matchup_uncertainty": 0.9})
        low_unc = model.predict({"team_strength_diff": 0.3, "matchup_uncertainty": 0.1})
        assert high_unc < low_unc

    def test_train_insufficient_data(self):
        model = ValorantModel()
        result = model.train([_make_row()] * 10)
        assert not result
        assert not model.is_trained

    def test_train_sufficient_data(self):
        model = ValorantModel()
        data = _make_training_data(100)
        result = model.train(data)
        assert result
        assert model.is_trained

    def test_predict_after_training(self):
        model = ValorantModel()
        data = _make_training_data(100)
        model.train(data)

        prob_a = model.predict({"team_strength_diff": 0.3, "matchup_uncertainty": 0.2,
                                "rd_asymmetry": 0.0, "team_a_volatility": 1.0,
                                "team_b_volatility": 1.0, "best_of": 1})
        prob_b = model.predict({"team_strength_diff": -0.3, "matchup_uncertainty": 0.2,
                                "rd_asymmetry": 0.0, "team_a_volatility": 1.0,
                                "team_b_volatility": 1.0, "best_of": 1})
        assert prob_a > prob_b

    def test_predict_clamped(self):
        model = ValorantModel()
        data = _make_training_data(100)
        model.train(data)
        prob = model.predict({"team_strength_diff": 0.5})
        assert 0.01 <= prob <= 0.99

    def test_save_and_load(self):
        model = ValorantModel()
        data = _make_training_data(100)
        model.train(data)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name

        try:
            assert model.save(path)
            model2 = ValorantModel()
            assert model2.load(path)
            assert model2.is_trained

            state = {"team_strength_diff": 0.2, "matchup_uncertainty": 0.3}
            assert abs(model.predict(state) - model2.predict(state)) < 0.01
        finally:
            os.unlink(path)

    def test_load_nonexistent(self):
        model = ValorantModel()
        assert not model.load("/nonexistent/path.pkl")
        assert not model.is_trained

    def test_feature_names(self):
        assert len(FEATURE_NAMES) == 6
        assert "team_strength_diff" in FEATURE_NAMES
        assert "best_of" in FEATURE_NAMES

    def test_extract_features_missing_keys(self):
        model = ValorantModel()
        features = model._extract_features({})
        assert features is not None
        assert len(features) == 6
        assert all(f == 0.0 for f in features)
