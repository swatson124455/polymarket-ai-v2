"""
Tests for MLPClassifier integration into the prediction ensemble (Model #11).
Validates sklearn neural network compatibility with the training pipeline.
"""
import pickle
import numpy as np
import pytest
from sklearn.neural_network import MLPClassifier
from sklearn.calibration import CalibratedClassifierCV


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


class TestMLPClassifier:
    """Test MLPClassifier compatibility with the ensemble pipeline."""

    def test_fit_predict_proba_shape(self, small_dataset):
        """MLPClassifier produces (n_samples, 2) probability output."""
        X, y, w = small_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300,
                            random_state=42, early_stopping=True)
        mlp.fit(X, y, sample_weight=w)
        proba = mlp.predict_proba(X)
        assert proba.shape == (50, 2)
        # Probabilities should sum to 1 for each sample
        np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-6)

    def test_no_nan_predictions(self, small_dataset):
        """MLPClassifier predictions contain no NaN values."""
        X, y, w = small_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300,
                            random_state=42, early_stopping=True)
        mlp.fit(X, y, sample_weight=w)
        proba = mlp.predict_proba(X)
        assert not np.any(np.isnan(proba)), "MLP predictions contain NaN"

    def test_sample_weight_accepted(self, small_dataset):
        """MLPClassifier fit() accepts sample_weight (sklearn 1.7+)."""
        X, y, w = small_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(32, 16), max_iter=500,
                            random_state=42)
        # This should NOT raise — sample_weight support added in sklearn 1.7
        mlp.fit(X, y, sample_weight=w)
        proba = mlp.predict_proba(X)
        assert proba.shape == (50, 2)

    def test_early_stopping_converges(self, medium_dataset):
        """MLPClassifier with early_stopping converges without MaxIter warning."""
        X, y, w = medium_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=500,
                            random_state=42, early_stopping=True, alpha=0.001)
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mlp.fit(X, y, sample_weight=w)
        # Check no ConvergenceWarning was raised
        convergence_warnings = [w for w in caught
                                if issubclass(w.category, UserWarning)
                                and "maximum iterations" in str(w.message).lower()]
        # Note: with early_stopping and small data, convergence is expected
        # If it doesn't converge in 500 iter on 200 samples, something is very wrong
        assert len(convergence_warnings) == 0, \
            f"MLP did not converge: {convergence_warnings}"

    def test_no_feature_importances(self, small_dataset):
        """MLPClassifier does NOT have feature_importances_ (won't dilute get_feature_scores)."""
        X, y, w = small_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(32,), max_iter=500,
                            early_stopping=True, random_state=42)
        mlp.fit(X, y)
        assert not hasattr(mlp, "feature_importances_"), \
            "MLP should NOT have feature_importances_ attribute"

    def test_has_weight_matrices(self, small_dataset):
        """MLPClassifier has coefs_ (weight matrices) and intercepts_ after fitting."""
        X, y, w = small_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=500, random_state=42)
        mlp.fit(X, y)
        assert hasattr(mlp, "coefs_"), "MLP should have coefs_ after fitting"
        assert hasattr(mlp, "intercepts_"), "MLP should have intercepts_ after fitting"
        # coefs_ should have one matrix per layer transition
        # input(8) -> hidden(64) -> hidden(32) -> output(1 or 2) = 3 weight matrices
        # Binary classification: sklearn may use 1 output neuron (logistic) or 2
        assert len(mlp.coefs_) == 3
        assert mlp.coefs_[0].shape == (8, 64)
        assert mlp.coefs_[1].shape == (64, 32)
        assert mlp.coefs_[2].shape[0] == 32
        assert mlp.coefs_[2].shape[1] in (1, 2), \
            f"Output layer should have 1 or 2 units, got {mlp.coefs_[2].shape[1]}"

    def test_calibrated_wrapper_compatible(self, medium_dataset):
        """MLPClassifier works with CalibratedClassifierCV if USE_CALIBRATED_MODELS enabled."""
        X, y, w = medium_dataset
        base_mlp = MLPClassifier(hidden_layer_sizes=(32, 16), max_iter=500,
                                 random_state=42)
        cal = CalibratedClassifierCV(base_mlp, cv=3, method="isotonic")
        cal.fit(X, y)
        proba = cal.predict_proba(X)
        assert proba.shape == (200, 2)
        assert not np.any(np.isnan(proba))

    def test_pickle_roundtrip(self, small_dataset):
        """MLPClassifier survives pickle save/load with identical predictions."""
        X, y, w = small_dataset
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300,
                            random_state=42, early_stopping=True)
        mlp.fit(X, y, sample_weight=w)
        proba_before = mlp.predict_proba(X)

        # Pickle roundtrip
        data = pickle.dumps(mlp)
        mlp_loaded = pickle.loads(data)
        proba_after = mlp_loaded.predict_proba(X)

        np.testing.assert_array_equal(proba_before, proba_after,
                                      err_msg="MLP predictions changed after pickle roundtrip")
