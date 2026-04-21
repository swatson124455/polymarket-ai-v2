"""
Tests for FavoriteLongshotCalibrator and DomainCalibrator
(base_engine/features/calibration.py).

Covers:
  - Unfitted returns identity
  - Fitted isotonic: monotonic, clipped, exception fallback
  - DomainCalibrator: per-category selection, global fallback
"""
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.features.calibration import (
    FavoriteLongshotCalibrator,
    DomainCalibrator,
)


# =========================================================================
# FavoriteLongshotCalibrator
# =========================================================================

class TestFLCUnfitted:
    def test_returns_identity(self):
        """Unfitted calibrator returns raw_prob unchanged."""
        cal = FavoriteLongshotCalibrator(db=None)
        assert not cal.is_fitted
        for p in [0.05, 0.20, 0.50, 0.80, 0.95]:
            assert cal.calibrate(p) == p


class TestFLCFitted:
    @staticmethod
    def _build_fitted():
        """Build a fitted FLC from synthetic data."""
        from sklearn.isotonic import IsotonicRegression
        np.random.seed(42)
        n = 200
        predictions = np.random.uniform(0.05, 0.95, n)
        # Outcomes correlated with predictions but noisy
        outcomes = (np.random.random(n) < predictions).astype(float)

        cal = FavoriteLongshotCalibrator(db=None)
        cal._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        cal._calibrator.fit(predictions, outcomes)
        cal._fitted = True
        return cal

    def test_is_fitted(self):
        cal = self._build_fitted()
        assert cal.is_fitted

    def test_monotonic_output(self):
        """Isotonic regression produces monotonically increasing output."""
        cal = self._build_fitted()
        prev = 0.0
        for p_int in range(5, 96, 5):
            p = p_int / 100.0
            result = cal.calibrate(p)
            assert result >= prev, f"Not monotonic at p={p}: {result} < {prev}"
            prev = result

    def test_output_clipped(self):
        """Output stays within [0.01, 0.99]."""
        cal = self._build_fitted()
        for p in [0.001, 0.01, 0.50, 0.99, 0.999]:
            result = cal.calibrate(p)
            assert 0.01 <= result <= 0.99, f"p={p} → {result}"

    def test_broken_calibrator_returns_raw(self):
        """If internal calibrator raises, returns raw_prob (fail-safe)."""
        cal = FavoriteLongshotCalibrator(db=None)
        cal._fitted = True
        cal._calibrator = MagicMock()
        cal._calibrator.predict = MagicMock(side_effect=ValueError("broken"))
        result = cal.calibrate(0.60)
        assert result == 0.60

    @pytest.mark.asyncio
    async def test_fit_insufficient_data(self):
        """<50 rows → returns False."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(0.6, "YES")] * 30

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *a):
                pass

        mock_db = MagicMock()
        mock_db.session_factory = True
        mock_db.get_session = MagicMock(return_value=_Ctx())

        cal = FavoriteLongshotCalibrator(db=mock_db)
        result = await cal.fit_from_prediction_log(n_days=90)
        assert result is False
        assert not cal.is_fitted

    @pytest.mark.asyncio
    async def test_no_db_returns_false(self):
        """None db → returns False."""
        cal = FavoriteLongshotCalibrator(db=None)
        result = await cal.fit_from_prediction_log(n_days=90)
        assert result is False


# =========================================================================
# DomainCalibrator
# =========================================================================

class TestDomainCalibrator:
    def test_no_fits_returns_raw(self):
        """When nothing is fitted, returns raw_prob."""
        cal = DomainCalibrator(db=None)
        assert cal.calibrate(0.65) == 0.65
        assert cal.calibrate(0.65, category="politics") == 0.65

    def test_category_specific_selected(self):
        """When category calibrator is fitted, it's used over global."""
        cal = DomainCalibrator(db=None)
        # Manually fit a politics calibrator
        from sklearn.isotonic import IsotonicRegression
        np.random.seed(42)
        n = 100
        preds = np.random.uniform(0.1, 0.9, n)
        outcomes = (np.random.random(n) < preds * 0.8).astype(float)  # biased

        politics_cal = FavoriteLongshotCalibrator(db=None)
        politics_cal._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        politics_cal._calibrator.fit(preds, outcomes)
        politics_cal._fitted = True
        cal._calibrators["politics"] = politics_cal

        # Politics category uses category calibrator
        result = cal.calibrate(0.70, category="politics")
        assert result != 0.70  # calibrated, not identity

    def test_global_fallback(self):
        """When category not fitted, falls back to global."""
        cal = DomainCalibrator(db=None)
        # Fit only global
        from sklearn.isotonic import IsotonicRegression
        np.random.seed(42)
        n = 100
        preds = np.random.uniform(0.1, 0.9, n)
        outcomes = (np.random.random(n) < preds).astype(float)

        cal._global._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        cal._global._calibrator.fit(preds, outcomes)
        cal._global._fitted = True

        # Unknown category falls to global
        result = cal.calibrate(0.70, category="unknown")
        assert result != 0.70  # global calibrator applied

    def test_empty_category_uses_global(self):
        """Empty category string falls back to global."""
        cal = DomainCalibrator(db=None)
        from sklearn.isotonic import IsotonicRegression
        np.random.seed(42)
        preds = np.random.uniform(0.1, 0.9, 100)
        outcomes = (np.random.random(100) < preds).astype(float)
        cal._global._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        cal._global._calibrator.fit(preds, outcomes)
        cal._global._fitted = True

        result = cal.calibrate(0.70, category="")
        assert result != 0.70
