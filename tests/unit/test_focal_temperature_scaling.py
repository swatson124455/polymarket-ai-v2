"""Tests for FocalTemperatureCalibrator (Focal Temperature Scaling)."""
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from base_engine.features.calibration import FocalTemperatureCalibrator


class TestFTSUnfittedReturnsIdentity:
    """Before fitting, calibrate() should return the input unchanged."""

    def test_unfitted_returns_exact_input(self):
        cal = FocalTemperatureCalibrator(db=None)
        assert not cal.is_fitted
        for p in [0.1, 0.25, 0.5, 0.75, 0.9]:
            assert cal.calibrate(p) == p

    def test_unfitted_temperature_defaults(self):
        cal = FocalTemperatureCalibrator(db=None)
        assert cal.temperature == 1.0
        assert cal.gamma == 0.0


class TestFTSCalibrateBasic:
    """After manually setting T and gamma, verify calibration transforms correctly."""

    def test_temperature_1_is_identity(self):
        """T=1.0 should return (approximately) the input probability."""
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 1.0
        cal._gamma = 0.0
        cal._fitted = True
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            assert abs(cal.calibrate(p) - p) < 1e-6

    def test_temperature_below_1_sharpens(self):
        """T < 1 should push probabilities away from 0.5 (sharpen)."""
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 0.5
        cal._gamma = 0.0
        cal._fitted = True
        # p > 0.5 should increase
        assert cal.calibrate(0.7) > 0.7
        # p < 0.5 should decrease
        assert cal.calibrate(0.3) < 0.3
        # p = 0.5 should stay at 0.5 (logit=0, scaling doesn't change it)
        assert abs(cal.calibrate(0.5) - 0.5) < 1e-6

    def test_temperature_above_1_softens(self):
        """T > 1 should push probabilities toward 0.5 (soften)."""
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 2.0
        cal._gamma = 0.0
        cal._fitted = True
        # p > 0.5 should decrease toward 0.5
        assert cal.calibrate(0.8) < 0.8
        assert cal.calibrate(0.8) > 0.5
        # p < 0.5 should increase toward 0.5
        assert cal.calibrate(0.2) > 0.2
        assert cal.calibrate(0.2) < 0.5

    def test_output_clipped_to_bounds(self):
        """Output should be clipped to [0.01, 0.99]."""
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 0.5
        cal._gamma = 0.0
        cal._fitted = True
        # Very extreme input should be clipped
        result_high = cal.calibrate(0.999)
        assert result_high <= 0.99
        result_low = cal.calibrate(0.001)
        assert result_low >= 0.01


class TestFTSFitGridSearch:
    """Mock DB data and verify fitting produces reasonable T."""

    def test_grid_search_well_calibrated_data(self):
        """Well-calibrated data: FTS auto-disables because it can't improve Brier score."""
        cal = FocalTemperatureCalibrator(db=None)
        np.random.seed(42)
        n = 500
        # Generate well-calibrated predictions: outcome matches probability
        predictions = np.random.uniform(0.1, 0.9, n)
        outcomes = (np.random.random(n) < predictions).astype(float)

        cal._fit_grid_search(predictions, outcomes)
        # S90: Well-calibrated data means FTS is unnecessary — auto-disable fires
        assert not cal.is_fitted, "FTS should auto-disable for already-calibrated data"
        # calibrate() returns identity when not fitted
        assert cal.calibrate(0.7) == 0.7

    def test_grid_search_overconfident_data(self):
        """Overconfident predictions (too sharp) should produce T > 1."""
        cal = FocalTemperatureCalibrator(db=None)
        np.random.seed(42)
        n = 300
        # True probs near 0.5, but predictions are pushed to extremes
        true_probs = np.random.uniform(0.3, 0.7, n)
        predictions = np.where(true_probs > 0.5, 0.85, 0.15)
        outcomes = (np.random.random(n) < true_probs).astype(float)

        cal._fit_grid_search(predictions, outcomes)
        assert cal.is_fitted
        # Overconfident predictions need softening → T > 1
        assert cal.temperature > 1.0

    def test_grid_search_underconfident_data(self):
        """Underconfident predictions (too soft) should produce T < 1."""
        cal = FocalTemperatureCalibrator(db=None)
        np.random.seed(42)
        n = 300
        # True probs are extreme, but predictions are pulled toward 0.5
        true_probs = np.concatenate([
            np.full(150, 0.9),
            np.full(150, 0.1),
        ])
        predictions = np.where(true_probs > 0.5, 0.6, 0.4)
        outcomes = (np.random.random(n) < true_probs).astype(float)

        cal._fit_grid_search(predictions, outcomes)
        assert cal.is_fitted
        # Underconfident predictions need sharpening → T < 1
        assert cal.temperature < 1.0

    @pytest.mark.asyncio
    async def test_fit_from_prediction_log_insufficient_data(self):
        """Should return False when fewer than 50 resolved predictions."""
        db = MagicMock()
        db.session_factory = True
        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchall.return_value = [(0.6, "YES")] * 10  # Only 10 rows
        session_mock.execute = AsyncMock(return_value=result_mock)

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=ctx)

        cal = FocalTemperatureCalibrator(db=db)
        result = await cal.fit_from_prediction_log(n_days=90)
        assert result is False
        assert not cal.is_fitted

    @pytest.mark.asyncio
    async def test_fit_from_prediction_log_success(self):
        """Should fit successfully with overconfident predictions (FTS genuinely helps)."""
        db = MagicMock()
        db.session_factory = True
        session_mock = AsyncMock()
        result_mock = MagicMock()
        # 100 rows: overconfident — predict 0.85 but actual ~50/50
        rows = [(0.85, "YES")] * 50 + [(0.85, "NO")] * 50
        result_mock.fetchall.return_value = rows
        session_mock.execute = AsyncMock(return_value=result_mock)

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=ctx)

        cal = FocalTemperatureCalibrator(db=db)
        result = await cal.fit_from_prediction_log(n_days=90)
        assert result is True
        assert cal.is_fitted
        assert 0.5 <= cal.temperature <= 2.0


class TestFTSExtremeInputs:
    """Test with extreme probability inputs."""

    def test_extreme_low(self):
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 1.5
        cal._gamma = 1.0
        cal._fitted = True
        result = cal.calibrate(0.01)
        assert 0.01 <= result <= 0.99
        assert result < 0.5  # Should still be low

    def test_midpoint(self):
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 1.5
        cal._gamma = 1.0
        cal._fitted = True
        result = cal.calibrate(0.5)
        # logit(0.5) = 0, so sigmoid(0/T) = 0.5 regardless of T
        assert abs(result - 0.5) < 1e-6

    def test_extreme_high(self):
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 1.5
        cal._gamma = 1.0
        cal._fitted = True
        result = cal.calibrate(0.99)
        assert 0.01 <= result <= 0.99
        assert result > 0.5  # Should still be high

    def test_boundary_values(self):
        """Ensure no NaN or inf for boundary probabilities."""
        cal = FocalTemperatureCalibrator(db=None)
        cal._temperature = 0.5
        cal._gamma = 2.0
        cal._fitted = True
        for p in [0.001, 0.01, 0.5, 0.99, 0.999]:
            result = cal.calibrate(p)
            assert np.isfinite(result), f"Non-finite result for p={p}"
            assert 0.01 <= result <= 0.99, f"Out of bounds for p={p}: {result}"


class TestFTSStaticMethods:
    """Test internal static helpers."""

    def test_sigmoid_values(self):
        assert abs(FocalTemperatureCalibrator._sigmoid(np.array([0.0]))[0] - 0.5) < 1e-7
        assert FocalTemperatureCalibrator._sigmoid(np.array([10.0]))[0] > 0.999
        assert FocalTemperatureCalibrator._sigmoid(np.array([-10.0]))[0] < 0.001

    def test_logit_inverse_of_sigmoid(self):
        """logit(sigmoid(x)) should approximately equal x."""
        x = np.array([-3.0, -1.0, 0.0, 1.0, 3.0])
        p = FocalTemperatureCalibrator._sigmoid(x)
        x_back = FocalTemperatureCalibrator._logit(p)
        np.testing.assert_allclose(x_back, x, atol=1e-6)

    def test_focal_loss_is_positive(self):
        """Focal loss should always be non-negative."""
        preds = np.array([0.2, 0.5, 0.8])
        outcomes = np.array([0.0, 1.0, 1.0])
        loss = FocalTemperatureCalibrator._focal_loss(preds, outcomes, 1.0, 0.0)
        assert loss > 0.0

    def test_focal_loss_gamma_0_is_cross_entropy(self):
        """With gamma=0, focal loss reduces to binary cross-entropy."""
        preds = np.array([0.3, 0.7, 0.9])
        outcomes = np.array([0.0, 1.0, 1.0])
        focal = FocalTemperatureCalibrator._focal_loss(preds, outcomes, 1.0, 0.0)
        # Manual BCE
        p = np.clip(preds, 1e-7, 1.0 - 1e-7)
        bce = -np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p))
        assert abs(focal - bce) < 1e-6
