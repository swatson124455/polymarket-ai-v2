"""
Metaculus Calibration Benchmark — validate calibration pipeline correctness.

Uses Metaculus API (free, public) to download resolved binary questions,
then runs them through our calibration pipeline to verify it produces
well-calibrated outputs. This is a cold-start validation tool, not a
live trading component.

Usage::
    benchmark = MetaculusBenchmark()
    results = await benchmark.run_validation(calibrator)
    # results = {"n_questions": 500, "raw_ece": 0.08, "calibrated_ece": 0.04, ...}
"""
import numpy as np
from typing import Any, Dict, List, Optional
from structlog import get_logger

logger = get_logger()

# Metaculus public API — no auth required for resolved questions
_METACULUS_API = "https://www.metaculus.com/api2/questions/"


class MetaculusBenchmark:
    """Validate calibration pipeline against Metaculus resolved questions."""

    def __init__(self):
        self._questions: List[Dict] = []

    async def fetch_resolved_binary(
        self, limit: int = 500, categories: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Fetch resolved binary questions from Metaculus API.

        Returns list of {community_prediction: float, resolution: 0|1, question_id: int}

        API: GET /api2/questions/?status=resolved&type=binary&limit=100&offset=0
        Community prediction is the final aggregated forecast before resolution.
        """
        import httpx

        results = []
        offset = 0
        per_page = 100

        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(results) < limit:
                try:
                    params = {
                        "status": "resolved",
                        "type": "binary",
                        "limit": per_page,
                        "offset": offset,
                        "order_by": "-resolve_time",
                    }
                    resp = await client.get(_METACULUS_API, params=params)
                    if resp.status_code != 200:
                        logger.warning("metaculus_fetch_failed", status=resp.status_code)
                        break

                    data = resp.json()
                    questions = data.get("results", [])
                    if not questions:
                        break

                    for q in questions:
                        # Extract community prediction and resolution
                        cp = q.get("community_prediction", {})
                        if isinstance(cp, dict):
                            pred = cp.get("full", {}).get("q2")  # median
                        elif isinstance(cp, (int, float)):
                            pred = float(cp)
                        else:
                            continue

                        resolution = q.get("resolution")
                        if pred is None or resolution is None:
                            continue
                        if resolution not in (0, 0.0, 1, 1.0, True, False):
                            continue  # Skip ambiguous resolutions

                        results.append({
                            "community_prediction": float(pred),
                            "resolution": 1 if resolution in (1, 1.0, True) else 0,
                            "question_id": q.get("id"),
                        })

                    offset += per_page
                    if len(questions) < per_page:
                        break

                except Exception as e:
                    logger.warning("metaculus_fetch_error", error=str(e))
                    break

        self._questions = results[:limit]
        logger.info("metaculus_fetched", count=len(self._questions))
        return self._questions

    def compute_calibration_metrics(
        self, predictions: List[float], outcomes: List[int], n_bins: int = 10,
    ) -> Dict[str, float]:
        """
        Compute ECE, Brier score, and per-bin calibration data.

        Returns:
            {ece, brier, n_samples, bins: [{bin_center, avg_pred, avg_actual, count}]}
        """
        if not predictions or len(predictions) != len(outcomes):
            return {"ece": 1.0, "brier": 1.0, "n_samples": 0, "bins": []}

        preds = np.array(predictions)
        actuals = np.array(outcomes, dtype=float)
        n = len(preds)

        brier = float(np.mean((preds - actuals) ** 2))

        # ECE
        bins_data = []
        ece = 0.0
        for i in range(n_bins):
            lo = i / n_bins
            hi = (i + 1) / n_bins
            mask = (preds >= lo) & (preds < hi) if i < n_bins - 1 else (preds >= lo) & (preds <= hi)
            count = int(mask.sum())
            if count > 0:
                avg_pred = float(preds[mask].mean())
                avg_actual = float(actuals[mask].mean())
                ece += (count / n) * abs(avg_pred - avg_actual)
                bins_data.append({
                    "bin_center": round((lo + hi) / 2, 2),
                    "avg_pred": round(avg_pred, 4),
                    "avg_actual": round(avg_actual, 4),
                    "count": count,
                })

        return {
            "ece": round(ece, 4),
            "brier": round(brier, 4),
            "n_samples": n,
            "bins": bins_data,
        }

    async def run_validation(
        self, calibrator=None, limit: int = 500,
    ) -> Dict[str, Any]:
        """
        Full validation: fetch Metaculus data, compute raw metrics,
        optionally apply calibrator and compute calibrated metrics.

        Args:
            calibrator: Any object with .calibrate(prob) method (e.g. FocalTemperatureCalibrator)
            limit: Number of questions to fetch

        Returns:
            {raw_ece, raw_brier, calibrated_ece, calibrated_brier, improvement_pct, n_questions}
        """
        if not self._questions:
            await self.fetch_resolved_binary(limit=limit)

        if not self._questions:
            return {"error": "no questions fetched", "n_questions": 0}

        raw_preds = [q["community_prediction"] for q in self._questions]
        outcomes = [q["resolution"] for q in self._questions]

        raw_metrics = self.compute_calibration_metrics(raw_preds, outcomes)

        result = {
            "n_questions": len(self._questions),
            "raw_ece": raw_metrics["ece"],
            "raw_brier": raw_metrics["brier"],
            "raw_bins": raw_metrics["bins"],
        }

        if calibrator and hasattr(calibrator, "calibrate"):
            cal_preds = [calibrator.calibrate(p) for p in raw_preds]
            cal_metrics = self.compute_calibration_metrics(cal_preds, outcomes)
            result["calibrated_ece"] = cal_metrics["ece"]
            result["calibrated_brier"] = cal_metrics["brier"]
            result["calibrated_bins"] = cal_metrics["bins"]
            if raw_metrics["ece"] > 0:
                result["ece_improvement_pct"] = round(
                    (raw_metrics["ece"] - cal_metrics["ece"]) / raw_metrics["ece"] * 100, 1
                )

        logger.info(
            "metaculus_benchmark_complete",
            n_questions=result["n_questions"],
            raw_ece=result["raw_ece"],
            raw_brier=result["raw_brier"],
            calibrated_ece=result.get("calibrated_ece"),
        )
        return result
