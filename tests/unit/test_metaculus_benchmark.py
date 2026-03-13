"""Tests for MetaculusBenchmark calibration validation utility."""
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

from esports.calibration.metaculus_benchmark import MetaculusBenchmark


class TestComputeCalibrationMetrics:
    """Tests for compute_calibration_metrics (pure math, no I/O)."""

    def setup_method(self):
        self.bench = MetaculusBenchmark()

    def test_compute_calibration_metrics_perfect(self):
        """Predictions exactly matching outcomes should give ECE=0, Brier=0."""
        predictions = [0.0, 0.0, 1.0, 1.0, 0.0, 1.0]
        outcomes =    [0,   0,   1,   1,   0,   1]
        result = self.bench.compute_calibration_metrics(predictions, outcomes)
        assert result["ece"] == 0.0
        assert result["brier"] == 0.0
        assert result["n_samples"] == 6

    def test_compute_calibration_metrics_random(self):
        """Random predictions on binary outcomes should yield high ECE and Brier."""
        rng = np.random.default_rng(42)
        predictions = rng.uniform(0, 1, size=200).tolist()
        outcomes = rng.integers(0, 2, size=200).tolist()
        result = self.bench.compute_calibration_metrics(predictions, outcomes)
        # Random preds vs random outcomes: Brier ~0.33, ECE > 0
        assert result["brier"] > 0.1
        assert result["ece"] > 0.0
        assert result["n_samples"] == 200
        assert len(result["bins"]) > 0

    def test_compute_calibration_metrics_empty(self):
        """Empty input returns safe defaults."""
        result = self.bench.compute_calibration_metrics([], [])
        assert result["ece"] == 1.0
        assert result["brier"] == 1.0
        assert result["n_samples"] == 0
        assert result["bins"] == []

    def test_compute_calibration_metrics_length_mismatch(self):
        """Mismatched lengths return safe defaults."""
        result = self.bench.compute_calibration_metrics([0.5, 0.6], [1])
        assert result["n_samples"] == 0


def _make_metaculus_response(questions):
    """Build a fake Metaculus API JSON response."""
    results = []
    for q in questions:
        results.append({
            "id": q["id"],
            "community_prediction": {"full": {"q2": q["pred"]}},
            "resolution": q["resolution"],
        })
    return {"results": results}


@pytest.mark.asyncio
class TestRunValidation:
    """Tests for run_validation (mocked API — inject questions directly)."""

    async def test_run_validation_no_calibrator(self):
        """Pre-populate questions, verify raw metrics are computed without calibrator."""
        bench = MetaculusBenchmark()
        # Inject directly — avoids hitting real API
        bench._questions = [
            {"community_prediction": 0.8, "resolution": 1, "question_id": i}
            for i in range(50)
        ] + [
            {"community_prediction": 0.2, "resolution": 0, "question_id": i + 50}
            for i in range(50)
        ]

        result = await bench.run_validation(calibrator=None, limit=100)

        assert result["n_questions"] == 100
        assert "raw_ece" in result
        assert "raw_brier" in result
        assert result["raw_brier"] < 0.1  # Well-separated predictions
        assert "calibrated_ece" not in result

    async def test_run_validation_with_calibrator(self):
        """Pre-populate questions + mock calibrator, verify improvement metrics."""
        bench = MetaculusBenchmark()
        # Slightly miscalibrated: pred=0.7 but true rate=80%, pred=0.3 but true rate=20%
        bench._questions = [
            {"community_prediction": 0.7, "resolution": 1, "question_id": i}
            for i in range(40)
        ] + [
            {"community_prediction": 0.7, "resolution": 0, "question_id": i + 40}
            for i in range(10)
        ] + [
            {"community_prediction": 0.3, "resolution": 0, "question_id": i + 50}
            for i in range(40)
        ] + [
            {"community_prediction": 0.3, "resolution": 1, "question_id": i + 90}
            for i in range(10)
        ]

        # Calibrator that maps 0.7 -> 0.8 (closer to true 80%) and 0.3 -> 0.2
        calibrator = MagicMock()
        calibrator.calibrate = lambda p: 0.8 if p > 0.5 else 0.2

        result = await bench.run_validation(calibrator=calibrator, limit=100)

        assert result["n_questions"] == 100
        assert "calibrated_ece" in result
        assert "calibrated_brier" in result
        assert "ece_improvement_pct" in result
        # Calibrator should improve ECE since it pushes predictions closer to true rates
        assert result["calibrated_ece"] <= result["raw_ece"]
