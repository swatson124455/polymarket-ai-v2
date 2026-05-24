"""
Calibration Tracker - Track predicted vs actual outcomes for model calibration.
Persisted via prediction_log table (resolution backfill). In-memory cache for speed.
Includes DDM/EDDM concept drift detection for triggering model retraining.
"""
import math
from typing import Dict, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()


class DriftDetector:
    """
    DDM (Drift Detection Method) + EDDM (Early Drift Detection Method).
    DDM detects abrupt accuracy drops; EDDM detects gradual degradation.
    Both track a rolling error rate and trigger warnings/drift when thresholds are exceeded.
    """

    def __init__(self):
        # DDM state
        self._ddm_n = 0
        self._ddm_sum_errors = 0
        self._ddm_p_min = float("inf")
        self._ddm_s_min = float("inf")
        self._ddm_warning = False
        self._ddm_drift = False

        # EDDM state: tracks distance between errors (higher = better)
        self._eddm_n = 0
        self._eddm_n_errors = 0
        self._eddm_last_error_idx = 0
        self._eddm_distance_sum = 0.0
        self._eddm_distance_sq_sum = 0.0
        self._eddm_dist_mean_max = 0.0
        self._eddm_dist_std_max = 0.0
        self._eddm_warning = False
        self._eddm_drift = False

    def update(self, is_error: bool) -> Dict[str, Any]:
        """
        Feed a single binary observation (True=prediction was wrong).
        Returns dict with drift status.
        """
        self._ddm_n += 1
        self._eddm_n += 1

        # --- DDM ---
        if is_error:
            self._ddm_sum_errors += 1

        p = self._ddm_sum_errors / self._ddm_n
        s = math.sqrt(p * (1 - p) / self._ddm_n) if self._ddm_n > 0 and 0 < p < 1 else 0.0

        if self._ddm_n >= 30:  # need minimum samples
            if p + s <= self._ddm_p_min + self._ddm_s_min:
                self._ddm_p_min = p
                self._ddm_s_min = s
                self._ddm_warning = False

            if p + s > self._ddm_p_min + 2 * self._ddm_s_min:
                self._ddm_warning = True
            if p + s > self._ddm_p_min + 3 * self._ddm_s_min:
                self._ddm_drift = True

        # --- EDDM ---
        if is_error:
            self._eddm_n_errors += 1
            if self._eddm_n_errors > 1:
                distance = self._eddm_n - self._eddm_last_error_idx
                self._eddm_distance_sum += distance
                self._eddm_distance_sq_sum += distance * distance

                n_dist = self._eddm_n_errors - 1
                dist_mean = self._eddm_distance_sum / n_dist
                dist_var = (self._eddm_distance_sq_sum / n_dist) - (dist_mean * dist_mean)
                dist_std = math.sqrt(max(0, dist_var))

                if dist_mean + 2 * dist_std > self._eddm_dist_mean_max + 2 * self._eddm_dist_std_max:
                    self._eddm_dist_mean_max = dist_mean
                    self._eddm_dist_std_max = dist_std
                    self._eddm_warning = False

                if self._eddm_n >= 30:
                    ratio = (dist_mean + 2 * dist_std) / max(self._eddm_dist_mean_max + 2 * self._eddm_dist_std_max, 1e-10)
                    if ratio < 0.90:
                        self._eddm_warning = True
                    if ratio < 0.80:
                        self._eddm_drift = True

            self._eddm_last_error_idx = self._eddm_n

        return {
            "ddm_warning": self._ddm_warning,
            "ddm_drift": self._ddm_drift,
            "eddm_warning": self._eddm_warning,
            "eddm_drift": self._eddm_drift,
            "n_observations": self._ddm_n,
            "error_rate": p if self._ddm_n > 0 else 0.0,
        }

    def reset(self):
        """Reset after drift detected and retrain triggered."""
        self.__init__()


class CalibrationTracker:
    """
    Tracks prediction calibration: predicted probability vs actual outcome.
    Brier score: lower is better (0 = perfect calibration).
    Persistence: reads resolved predictions from prediction_log (was_correct column).
    """

    def __init__(self, db=None):
        self.db = db
        self.enabled = getattr(settings, "CALIBRATION_TRACKING_ENABLED", True)
        self._predictions: Dict[str, tuple] = {}  # market_id -> (predicted_prob, timestamp)
        self._results: list = []  # (pred, actual) pairs for metrics
        self._max_results = 10000
        self._loaded_from_db = False
        self.drift_detector = DriftDetector()

    def record_prediction(self, market_id: str, predicted_prob: float) -> None:
        """Record a prediction for a market (overwrites previous for same market)."""
        if not self.enabled:
            return
        try:
            p = float(predicted_prob)
            if 0 <= p <= 1:
                self._predictions[market_id] = (p, datetime.now(timezone.utc))
        except (ValueError, TypeError):
            pass

    def record_resolution(self, market_id: str, outcome: int) -> Dict[str, Any]:
        """
        Record resolution (1=YES, 0=NO) and compute calibration if we have prediction.
        Returns drift detection status dict (empty if disabled/no prediction).
        """
        if not self.enabled:
            return {}
        if market_id not in self._predictions:
            return {}
        pred, _ = self._predictions.pop(market_id)
        if outcome not in (0, 1):
            return {}
        self._results.append((pred, outcome))
        if len(self._results) > self._max_results:
            self._results.pop(0)

        # Feed drift detector: error = prediction was on wrong side
        is_error = (pred >= 0.5 and outcome == 0) or (pred < 0.5 and outcome == 1)
        drift_status = self.drift_detector.update(is_error)

        if drift_status.get("ddm_drift") or drift_status.get("eddm_drift"):
            logger.warning(
                "Concept drift detected — model retrain recommended",
                ddm_drift=drift_status["ddm_drift"],
                eddm_drift=drift_status["eddm_drift"],
                error_rate=round(drift_status["error_rate"], 4),
                n_observations=drift_status["n_observations"],
            )
        elif drift_status.get("ddm_warning") or drift_status.get("eddm_warning"):
            logger.info(
                "Drift warning — model accuracy degrading",
                ddm_warning=drift_status["ddm_warning"],
                eddm_warning=drift_status["eddm_warning"],
                error_rate=round(drift_status["error_rate"], 4),
            )

        return drift_status

    def get_metrics(self) -> Dict[str, Any]:
        """Return calibration metrics."""
        if not self._results:
            return {"brier_score": 0.0, "count": 0, "enabled": self.enabled}
        brier = sum((p - a) ** 2 for p, a in self._results) / len(self._results)
        return {
            "brier_score": round(brier, 4),
            "count": len(self._results),
            "enabled": self.enabled,
        }

    def get_drift_status(self) -> Dict[str, Any]:
        """Return current drift detector state."""
        return {
            "ddm_warning": self.drift_detector._ddm_warning,
            "ddm_drift": self.drift_detector._ddm_drift,
            "eddm_warning": self.drift_detector._eddm_warning,
            "eddm_drift": self.drift_detector._eddm_drift,
            "n_observations": self.drift_detector._ddm_n,
            "error_rate": (self.drift_detector._ddm_sum_errors / self.drift_detector._ddm_n)
            if self.drift_detector._ddm_n > 0 else 0.0,
        }

    async def load_historical_from_db(self) -> int:
        """
        Bootstrap calibration results from prediction_log on startup.
        Reads resolved predictions (was_correct IS NOT NULL) and populates _results.
        """
        if self._loaded_from_db or not self.enabled or not self.db or not self.db.session_factory:
            return 0
        count = 0
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text(
                    "SELECT predicted_prob, was_correct FROM prediction_log "
                    "WHERE was_correct IS NOT NULL "
                    "ORDER BY predicted_at DESC LIMIT :limit"
                ), {"limit": self._max_results})
                for row in result.fetchall():
                    pred = float(row[0]) if row[0] is not None else 0.5
                    actual = 1 if row[1] else 0
                    self._results.append((pred, actual))
                    # Feed drift detector with historical data too
                    is_error = (pred >= 0.5 and actual == 0) or (pred < 0.5 and actual == 1)
                    self.drift_detector.update(is_error)
                    count += 1
            self._loaded_from_db = True
            if count > 0:
                logger.info("Calibration tracker loaded %d historical results from prediction_log", count)
        except Exception as e:
            logger.debug("Calibration load_historical_from_db failed: %s", e)
        return count

    async def process_resolved_from_db(self) -> int:
        """
        Query DB for resolved markets, match with in-memory predictions, update calibration.
        Returns number of resolutions processed.
        """
        if not self.enabled or not self.db or not self.db.session_factory:
            return 0
        # Bootstrap historical on first call
        if not self._loaded_from_db:
            await self.load_historical_from_db()
        count = 0
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import select
                from bots.weather.engine.base_engine.data.database import Market
                result = await session.execute(
                    select(Market.id, Market.resolution).where(
                        Market.resolved == True,
                        Market.resolution.in_(["YES", "NO"])
                    )
                )
                rows = result.fetchall()
            for market_id, resolution in rows:
                if market_id in self._predictions:
                    outcome = 1 if resolution == "YES" else 0
                    self.record_resolution(market_id, outcome)
                    count += 1
        except Exception as e:
            # L4 FIX: Elevate from DEBUG to WARNING. At DEBUG, calibration silently stopped
            # working with no visibility — calibration is a key model quality signal.
            logger.warning("Calibration process_resolved_from_db failed: %s", e)
        return count
