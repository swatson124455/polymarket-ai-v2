"""
Unit tests for base_engine/learning/prediction_drift.py (PredictionDriftDetector).

Tests:
  - Stable regime: no drift detected on consistent positive edges
  - Gradual drift: detected when mean shifts from positive to negative
  - Sudden drift: detected when error rate (negative edges) spikes
  - Below minimum window: no drift flagged
  - Reset: clears state but preserves _last_check_id
  - async check(): processes new rows and updates state
"""
import asyncio
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from base_engine.learning.prediction_drift import PredictionDriftDetector


def _make_detector(min_window=20, z_gradual=2.0, z_sudden=3.0):
    """Create a PredictionDriftDetector with a mock DB."""
    mock_db = MagicMock()
    return PredictionDriftDetector(
        db=mock_db,
        bot_name="MirrorBot",
        min_window=min_window,
        z_gradual=z_gradual,
        z_sudden=z_sudden,
    )


class TestStableRegime:
    def test_no_drift_on_stable_positive_edges(self):
        """Consistent positive realized_edge should not trigger drift."""
        det = _make_detector(min_window=10)
        for i in range(50):
            report = det._update(0.05)  # consistent +5% edge
        assert not report["drift_detected"]
        assert report["drift_type"] is None

    def test_no_drift_on_stable_mixed_edges(self):
        """Mixed but statistically stable edges should not trigger drift."""
        det = _make_detector(min_window=20, z_gradual=2.0)
        import random
        random.seed(42)
        # Larger min_window + lower variance → stable baseline.
        # Mean 5% with std 1% — the occasional negative doesn't shift the mean > 2σ.
        drift_detected = False
        for i in range(100):
            edge = random.gauss(0.05, 0.01)
            report = det._update(edge)
            if report["drift_detected"]:
                drift_detected = True
        assert not drift_detected, "Stable regime should not trigger drift"


class TestGradualDrift:
    def test_gradual_drift_detected(self):
        """Mean shift from positive to negative edges should trigger gradual drift."""
        det = _make_detector(min_window=15, z_gradual=2.0)

        # Phase 1: establish positive baseline
        for i in range(20):
            report = det._update(0.05)
        assert not report["drift_detected"]
        assert det._baseline_mean is not None
        assert det._baseline_mean > 0

        # Phase 2: shift to negative edges
        drift_found = False
        for i in range(50):
            report = det._update(-0.08)
            if report["drift_detected"] and report["drift_type"] == "gradual":
                drift_found = True
                break
        assert drift_found, "Gradual drift should be detected after mean shift"


class TestSuddenDrift:
    def test_sudden_drift_detected(self):
        """Spike in negative edge frequency should trigger sudden drift."""
        det = _make_detector(min_window=10, z_sudden=3.0)

        # Phase 1: establish baseline with ~15% negative rate.
        # Need > 2*min_window (20) updates before DDM activates.
        import random
        random.seed(99)
        for i in range(30):
            edge = 0.04 if random.random() > 0.15 else -0.02
            det._update(edge)

        # Phase 2: sudden spike of ALL negative edges — error_rate jumps to ~1.0
        drift_found = False
        for i in range(60):
            report = det._update(-0.10)
            if report["drift_detected"] and report["drift_type"] == "sudden":
                drift_found = True
                break
        assert drift_found, "Sudden drift should be detected on negative spike"


class TestBelowMinimumWindow:
    def test_no_drift_below_min_window(self):
        """Below min_window, no drift should be flagged regardless of values."""
        det = _make_detector(min_window=50)
        for i in range(49):
            report = det._update(-1.0)  # extreme negative
        assert not report["drift_detected"]
        assert det._baseline_mean is None


class TestReset:
    def test_reset_clears_state(self):
        """reset() should clear window but preserve _last_check_id."""
        det = _make_detector(min_window=5)
        for i in range(10):
            det._update(0.03)
        assert len(det._window) > 0

        det._last_check_id = 12345
        det.reset()

        assert len(det._window) == 0
        assert det._baseline_mean is None
        assert det._baseline_std is None
        assert det._n_updates == 0
        assert det._last_check_id == 12345  # preserved


class TestWindowBounding:
    def test_window_capped_at_max(self):
        """Window should not exceed max_window."""
        det = _make_detector(min_window=5)
        det.max_window = 50
        for i in range(100):
            det._update(0.02)
        assert len(det._window) <= 50


class TestAsyncCheck:
    @pytest.mark.asyncio
    async def test_check_processes_new_rows(self):
        """check() should query DB, feed new rows, and return report."""
        mock_db = MagicMock()
        det = PredictionDriftDetector(db=mock_db, bot_name="MirrorBot", min_window=5)

        # Mock the DB session and query
        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Return 10 rows of positive edge
        mock_result.fetchall.return_value = [
            (i + 1, 0.04) for i in range(10)
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.get_session = MagicMock(return_value=mock_ctx)

        report = await det.check()

        assert report["new_observations"] == 10
        assert report["window_size"] == 10
        assert det._last_check_id == 10
        assert not report["drift_detected"]

    @pytest.mark.asyncio
    async def test_check_returns_empty_on_no_rows(self):
        """check() with no new rows should return default report."""
        mock_db = MagicMock()
        det = PredictionDriftDetector(db=mock_db, bot_name="MirrorBot", min_window=5)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.get_session = MagicMock(return_value=mock_ctx)

        report = await det.check()
        assert report["new_observations"] == 0
        assert not report["drift_detected"]

    @pytest.mark.asyncio
    async def test_check_handles_db_error_gracefully(self):
        """check() should return default report on DB error (fail-open)."""
        mock_db = MagicMock()
        det = PredictionDriftDetector(db=mock_db, bot_name="MirrorBot", min_window=5)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("connection lost"))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.get_session = MagicMock(return_value=mock_ctx)

        report = await det.check()
        assert not report["drift_detected"]
        assert report["new_observations"] == 0
