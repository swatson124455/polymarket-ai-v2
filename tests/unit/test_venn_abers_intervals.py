"""
Unit tests for base_engine/learning/venn_abers_intervals.py.

Tests the Inductive Venn-ABERS Predictor (IVAP) used for
MirrorBot gate_score → probability interval calibration.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.learning.venn_abers_intervals import VennAbersIntervalCalibrator


class TestFit:
    def test_fit_below_min_samples_returns_false(self):
        cal = VennAbersIntervalCalibrator(min_samples=30)
        ok = cal.fit([0.5] * 10, [1] * 10)
        assert ok is False
        assert not cal.is_fitted

    def test_fit_at_min_samples_returns_true(self):
        cal = VennAbersIntervalCalibrator(min_samples=30)
        scores = [i / 50 for i in range(30)]
        labels = [1 if s > 0.5 else 0 for s in scores]
        ok = cal.fit(scores, labels)
        assert ok is True
        assert cal.is_fitted
        assert cal.n_samples == 30

    def test_fit_mismatched_lengths_raises(self):
        cal = VennAbersIntervalCalibrator(min_samples=5)
        with pytest.raises(ValueError, match="same length"):
            cal.fit([0.5, 0.6], [1, 0, 1])

    def test_fit_invalid_labels_raises(self):
        cal = VennAbersIntervalCalibrator(min_samples=5)
        with pytest.raises(ValueError, match="must be 0 or 1"):
            cal.fit([0.5] * 5, [0, 1, 2, 1, 0])


class TestPredictInterval:
    def test_unfitted_returns_wide_fallback(self):
        cal = VennAbersIntervalCalibrator(min_samples=30)
        p0, p1 = cal.predict_interval(0.5)
        # Fallback is ±0.25 around score
        assert p0 == pytest.approx(0.25)
        assert p1 == pytest.approx(0.75)

    def test_unfitted_clamps_to_unit_interval(self):
        cal = VennAbersIntervalCalibrator(min_samples=30)
        p0, p1 = cal.predict_interval(0.1)
        assert p0 == pytest.approx(0.0)
        assert p1 == pytest.approx(0.35)
        p0, p1 = cal.predict_interval(0.9)
        assert p0 == pytest.approx(0.65)
        assert p1 == pytest.approx(1.0)

    def test_interval_bounds_ordering(self):
        """p0 ≤ p1 must hold after fitting."""
        cal = VennAbersIntervalCalibrator(min_samples=10)
        # Well-calibrated data: score ≈ P(y=1)
        scores, labels = [], []
        for i in range(100):
            s = (i + 1) / 101
            scores.append(s)
            labels.append(1 if i % 2 == 0 else 0)
        cal.fit(scores, labels)
        for s in [0.1, 0.3, 0.5, 0.7, 0.9]:
            p0, p1 = cal.predict_interval(s)
            assert 0.0 <= p0 <= p1 <= 1.0, f"failed at s={s}: p0={p0}, p1={p1}"

    def test_perfect_calibration_tight_interval(self):
        """With many samples and perfect score→label relationship, interval should be tight."""
        cal = VennAbersIntervalCalibrator(min_samples=10)
        # Perfect: score < 0.5 → label=0, score >= 0.5 → label=1
        scores = [i / 100 for i in range(1, 100)]
        labels = [1 if s >= 0.5 else 0 for s in scores]
        cal.fit(scores, labels)

        # At clear-signal scores, interval should be narrow
        p0_low, p1_low = cal.predict_interval(0.1)
        p0_high, p1_high = cal.predict_interval(0.9)
        assert p1_low - p0_low < 0.15, f"narrow interval expected at 0.1: {p0_low}, {p1_low}"
        assert p1_high - p0_high < 0.15, f"narrow interval expected at 0.9: {p0_high}, {p1_high}"
        # Midpoint should track true probability
        assert (p0_low + p1_low) / 2 < 0.3
        assert (p0_high + p1_high) / 2 > 0.7

    def test_small_sample_wide_interval(self):
        """Near the min_samples floor, intervals should be wider than at n=500."""
        cal_small = VennAbersIntervalCalibrator(min_samples=30)
        cal_large = VennAbersIntervalCalibrator(min_samples=30)

        import random
        random.seed(7)
        scores_big = [random.random() for _ in range(500)]
        labels_big = [1 if s + random.gauss(0, 0.1) > 0.5 else 0 for s in scores_big]
        cal_large.fit(scores_big, labels_big)
        cal_small.fit(scores_big[:30], labels_big[:30])

        # Average width across several test points
        test_points = [0.3, 0.5, 0.7]
        w_small = sum(cal_small.predict_width(s) for s in test_points) / len(test_points)
        w_large = sum(cal_large.predict_width(s) for s in test_points) / len(test_points)
        assert w_small >= w_large, f"small n interval ({w_small:.3f}) should be ≥ large n ({w_large:.3f})"


class TestConvenienceMethods:
    def test_predict_midpoint(self):
        cal = VennAbersIntervalCalibrator(min_samples=10)
        scores = [i / 50 for i in range(30)]
        labels = [1 if s > 0.5 else 0 for s in scores]
        cal.fit(scores, labels)
        mid = cal.predict_midpoint(0.6)
        p0, p1 = cal.predict_interval(0.6)
        assert mid == pytest.approx((p0 + p1) / 2)

    def test_predict_width_positive(self):
        cal = VennAbersIntervalCalibrator(min_samples=10)
        scores = [i / 50 for i in range(30)]
        labels = [1 if s > 0.5 else 0 for s in scores]
        cal.fit(scores, labels)
        w = cal.predict_width(0.5)
        assert w >= 0.0

    def test_predict_batch(self):
        cal = VennAbersIntervalCalibrator(min_samples=10)
        scores = [i / 50 for i in range(30)]
        labels = [1 if s > 0.5 else 0 for s in scores]
        cal.fit(scores, labels)
        results = cal.predict_batch([0.3, 0.5, 0.7])
        assert len(results) == 3
        for p0, p1 in results:
            assert 0.0 <= p0 <= p1 <= 1.0


class TestAsyncDbLoading:
    @pytest.mark.asyncio
    async def test_fit_from_prediction_log_success(self):
        mock_db = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Return 50 rows with clear signal
        mock_result.fetchall.return_value = [
            (0.3 + 0.01 * i, 1 if (0.3 + 0.01 * i) > 0.5 else 0)
            for i in range(50)
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.get_session = MagicMock(return_value=mock_ctx)

        cal = VennAbersIntervalCalibrator(min_samples=30)
        ok = await cal.fit_from_prediction_log(mock_db, bot_name="MirrorBot")
        assert ok is True
        assert cal.is_fitted
        assert cal.n_samples == 50

    @pytest.mark.asyncio
    async def test_fit_from_prediction_log_no_data(self):
        mock_db = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.get_session = MagicMock(return_value=mock_ctx)

        cal = VennAbersIntervalCalibrator(min_samples=30)
        ok = await cal.fit_from_prediction_log(mock_db)
        assert ok is False
        assert not cal.is_fitted

    @pytest.mark.asyncio
    async def test_fit_from_prediction_log_db_error_graceful(self):
        mock_db = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("db down"))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.get_session = MagicMock(return_value=mock_ctx)

        cal = VennAbersIntervalCalibrator(min_samples=30)
        ok = await cal.fit_from_prediction_log(mock_db)
        assert ok is False
