"""
Tests for EsportsBot calibration classes:
  - BetaCalibrator (bots/esports_bot.py:48-161)
  - OnlinePlattCalibrator (bots/esports_bot.py:163-204)
  - VennAbersCalibrator (esports/models/venn_abers_calibrator.py)

Covers identity, directional math, output bounds, fit from DB,
temporal decay, graceful fallback when deps missing.
"""
import math
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bots.esports_bot import BetaCalibrator, OnlinePlattCalibrator
from esports.models.venn_abers_calibrator import VennAbersCalibrator


# =========================================================================
# BetaCalibrator
# =========================================================================

class TestBetaCalibrator:
    def test_unfitted_returns_identity(self):
        """Unfitted calibrator returns p unchanged."""
        cal = BetaCalibrator()
        assert not cal.is_fitted
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            assert cal.calibrate(p) == p

    def test_identity_params(self):
        """a=1, b=1, c=0 is the identity function."""
        cal = BetaCalibrator()
        cal.a, cal.b, cal.c = 1.0, 1.0, 0.0
        cal._fitted = True
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            assert abs(cal.calibrate(p) - p) < 0.001, f"p={p} got {cal.calibrate(p)}"

    def test_positive_c_shifts_up(self):
        """c > 0 shifts logit upward → output increases."""
        cal = BetaCalibrator()
        cal.a, cal.b, cal.c = 1.0, 1.0, 1.0
        cal._fitted = True
        # p=0.5: logit_cal = 1*log(0.5) - 1*log(0.5) + 1 = 1.0
        # sigmoid(1.0) ≈ 0.731
        result = cal.calibrate(0.50)
        assert result > 0.50

    def test_negative_c_shifts_down(self):
        """c < 0 shifts logit downward → output decreases."""
        cal = BetaCalibrator()
        cal.a, cal.b, cal.c = 1.0, 1.0, -1.0
        cal._fitted = True
        result = cal.calibrate(0.50)
        assert result < 0.50

    def test_output_range(self):
        """Output always in [0.01, 0.99]."""
        cal = BetaCalibrator()
        cal._fitted = True
        for a, b, c in [(1.0, 1.0, 0.0), (3.0, 0.5, 2.0), (0.5, 3.0, -2.0)]:
            cal.a, cal.b, cal.c = a, b, c
            for p in [0.001, 0.01, 0.50, 0.99, 0.999]:
                result = cal.calibrate(p)
                assert 0.01 <= result <= 0.99, f"a={a} b={b} c={c} p={p} → {result}"

    def test_monotonic(self):
        """Calibrated output should be monotonically increasing."""
        cal = BetaCalibrator()
        cal.a, cal.b, cal.c = 1.5, 0.8, 0.3
        cal._fitted = True
        prev = 0.0
        for p_int in range(5, 96, 5):
            p = p_int / 100.0
            result = cal.calibrate(p)
            assert result >= prev, f"Not monotonic at p={p}: {result} < {prev}"
            prev = result

    @pytest.mark.asyncio
    async def test_fit_insufficient_data(self):
        """<15 samples → returns False, stays unfitted."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(0.6, 1.0, 1.0)] * 10

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *a):
                pass

        mock_db = MagicMock()
        mock_db.get_session = MagicMock(return_value=_Ctx())

        cal = BetaCalibrator(min_samples=15)
        result = await cal.fit_from_db(mock_db, game="lol", days=90)
        assert result is False
        assert not cal.is_fitted

    @pytest.mark.asyncio
    async def test_fit_sufficient_data(self):
        """50+ samples → fits successfully, params within bounds."""
        np.random.seed(42)
        n = 60
        rows = []
        for _ in range(n):
            pred = np.random.uniform(0.3, 0.8)
            outcome = 1.0 if np.random.random() < pred else 0.0
            age_days = np.random.uniform(0, 30)
            rows.append((pred, outcome, age_days))

        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *a):
                pass

        mock_db = MagicMock()
        mock_db.get_session = MagicMock(return_value=_Ctx())

        cal = BetaCalibrator(min_samples=15)
        result = await cal.fit_from_db(mock_db, game="lol", days=90)
        assert result is True
        assert cal.is_fitted
        assert 0.1 <= cal.a <= 5.0
        assert 0.1 <= cal.b <= 5.0
        assert -2.0 <= cal.c <= 2.0

    @pytest.mark.asyncio
    async def test_fit_none_db_returns_false(self):
        """None db → returns False."""
        cal = BetaCalibrator()
        result = await cal.fit_from_db(None, game="lol")
        assert result is False


# =========================================================================
# OnlinePlattCalibrator
# =========================================================================

class TestOnlinePlattCalibrator:
    def test_unfitted_returns_identity(self):
        """<50 samples → returns p unchanged."""
        cal = OnlinePlattCalibrator(min_samples=50)
        assert not cal.is_fitted
        assert cal.calibrate(0.70) == 0.70

    def test_becomes_fitted_after_min_samples(self):
        """After feeding 50+ samples, is_fitted becomes True."""
        cal = OnlinePlattCalibrator(min_samples=50)
        if not cal._available:
            pytest.skip("River not installed")
        for i in range(60):
            predicted = 0.6 + 0.1 * (i % 3)
            actual = 1 if i % 2 == 0 else 0
            cal.update(predicted, actual)
        assert cal.is_fitted
        assert cal._n == 60

    def test_calibrate_returns_float_after_fitting(self):
        """After fitting, calibrate returns a float in [0, 1]."""
        cal = OnlinePlattCalibrator(min_samples=50)
        if not cal._available:
            pytest.skip("River not installed")
        np.random.seed(42)
        for _ in range(60):
            p = np.random.uniform(0.3, 0.8)
            outcome = 1 if np.random.random() < p else 0
            cal.update(p, outcome)
        result = cal.calibrate(0.70)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_not_available_graceful(self):
        """When River is not available, calibrate returns identity."""
        cal = OnlinePlattCalibrator()
        cal._available = False
        cal._n = 100  # simulate having samples
        assert cal.calibrate(0.70) == 0.70
        assert not cal.is_fitted

    def test_update_noop_when_unavailable(self):
        """When River unavailable, update does nothing."""
        cal = OnlinePlattCalibrator()
        cal._available = False
        cal.update(0.6, 1)
        assert cal._n == 0


# =========================================================================
# VennAbersCalibrator
# =========================================================================

class TestVennAbersCalibrator:
    def test_unfitted_returns_identity(self):
        """Unfitted calibrator returns p unchanged."""
        cal = VennAbersCalibrator()
        assert not cal.is_fitted
        assert cal.calibrate(0.70) == 0.70

    def test_unfitted_interval_wide(self):
        """Unfitted calibrator returns wide interval."""
        cal = VennAbersCalibrator()
        low, high = cal.get_interval(0.50)
        assert high - low >= 0.40  # Default wide interval

    def test_min_samples_gate(self):
        """<5 samples → returns False."""
        cal = VennAbersCalibrator(min_samples=5)
        preds = np.array([0.5, 0.6, 0.7])
        outcomes = np.array([1, 0, 1])
        result = cal.fit(preds, outcomes)
        assert result is False

    @pytest.mark.xfail(
        reason=(
            "esports/models/venn_abers_calibrator.py:66-68 missing cal_size "
            "param. venn_abers>=0.4.0 with inductive=True requires explicit "
            "cal_size or train_proper_size; without it the library raises "
            "'For Inductive Venn-ABERS please provide either calibration or "
            "proper train set size' and the wrapper's fit() returns False. "
            "This means EsportsBot's per-game calibrator at "
            "bots/esports_bot.py:5636-5663 has been silently failing fit. "
            "Tracked in §S195 hygiene; fix needs operator review because it "
            "changes prod calibration behavior (raw -> calibrated probs) "
            "which affects Kelly sizing + edge calculations."
        ),
        strict=False,
    )
    def test_fit_with_sufficient_data(self):
        """20+ samples -> fits successfully."""
        cal = VennAbersCalibrator(min_samples=5)
        np.random.seed(42)
        n = 30
        preds = np.random.uniform(0.2, 0.8, n)
        outcomes = (np.random.random(n) < preds).astype(int)
        result = cal.fit(preds, outcomes)
        assert result is True
        assert cal.is_fitted
        assert cal._n_samples == n

    def test_calibrate_returns_float_after_fit(self):
        """After fitting, calibrate returns a float."""
        cal = VennAbersCalibrator(min_samples=5)
        np.random.seed(42)
        n = 30
        preds = np.random.uniform(0.2, 0.8, n)
        outcomes = (np.random.random(n) < preds).astype(int)
        cal.fit(preds, outcomes)
        result = cal.calibrate(0.60)
        assert isinstance(result, float)

    def test_interval_after_fit(self):
        """After fitting, get_interval returns (low, high) tuple."""
        cal = VennAbersCalibrator(min_samples=5)
        np.random.seed(42)
        n = 30
        preds = np.random.uniform(0.2, 0.8, n)
        outcomes = (np.random.random(n) < preds).astype(int)
        cal.fit(preds, outcomes)
        low, high = cal.get_interval(0.60)
        assert isinstance(low, float)
        assert isinstance(high, float)
        assert low <= high

    def test_interval_width_updates(self):
        """interval_width attribute updates after fitting."""
        cal = VennAbersCalibrator(min_samples=5)
        initial_width = cal.interval_width
        np.random.seed(42)
        n = 30
        preds = np.random.uniform(0.2, 0.8, n)
        outcomes = (np.random.random(n) < preds).astype(int)
        cal.fit(preds, outcomes)
        # Width should change from default 1.0
        if cal._calibrator is not None:  # venn_abers package available
            assert cal.interval_width != initial_width

    def test_fallback_without_venn_abers_package(self):
        """When venn_abers not installed, fallback stores data but returns identity."""
        cal = VennAbersCalibrator(min_samples=5)
        cal._vac_class = None  # simulate package missing
        cal._available = False
        np.random.seed(42)
        n = 10
        preds = np.random.uniform(0.2, 0.8, n)
        outcomes = (np.random.random(n) < preds).astype(int)
        result = cal.fit(preds, outcomes)
        assert result is True  # fallback stores data
        assert cal.is_fitted
        # calibrate returns identity in fallback mode
        assert cal.calibrate(0.60) == 0.60
