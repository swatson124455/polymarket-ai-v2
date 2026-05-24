"""
7J: ADWIN-U (unsupervised) concept drift detector on prediction_log.realized_edge.

Detects when MirrorBot's prediction quality degrades over time by monitoring
the realized_edge stream from resolved predictions.  Reuses the ADWIN pattern
from base_engine/execution/rl_trade_timing.py:182-285 (AdaptiveRewardTracker).

Key differences from supervised DriftDetector (DDM/EDDM in calibration_tracker.py):
  - Operates on continuous realized_edge (not binary is_error)
  - Handles delayed resolution naturally: only processes rows where realized_edge IS NOT NULL
  - Tracks last-processed prediction_log.id to avoid re-scanning

Integration: called periodically from MirrorBot.scan_and_trade() (scan_count % 15 == 5).
Pure read + log operation — no behavior change to trading.
"""

import math
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


class PredictionDriftDetector:
    """
    ADWIN-U (unsupervised) drift detector on realized_edge from prediction_log.

    Sliding window of realized_edge values.  Establishes a baseline from the
    first full window, then flags gradual drift (z > z_gradual) and sudden
    drift (error rate > 3σ above minimum).

    On gradual drift: shrinks window to half (ADWIN adaptation step).
    Window capped at max_window to bound memory.
    """

    def __init__(
        self,
        db: Any,
        bot_name: str = "MirrorBot",
        min_window: int = 30,
        max_window: int = 500,
        z_gradual: float = 2.0,
        z_sudden: float = 3.0,
    ):
        self.db = db
        self.bot_name = bot_name
        self.min_window = min_window
        self.max_window = max_window
        self.z_gradual = z_gradual
        self.z_sudden = z_sudden

        self._window: List[float] = []
        self._sum: float = 0.0
        self._sum_sq: float = 0.0
        self._baseline_mean: Optional[float] = None
        self._baseline_std: Optional[float] = None

        # DDM-style sudden drift tracking
        self._error_rate: float = 0.0
        self._error_rate_min: float = float("inf")
        self._error_rate_std: float = 0.0
        self._n_updates: int = 0

        # Track last-processed prediction_log.id to avoid re-scanning
        self._last_check_id: int = 0

        # Last report for callers
        self._last_report: Dict[str, Any] = {}

    async def check(self) -> Dict[str, Any]:
        """
        Query new resolved predictions since last check, feed ADWIN-U.

        Returns:
            {"drift_detected": bool, "drift_type": str|None,
             "window_size": int, "current_mean": float,
             "baseline_mean": float|None, "new_observations": int}
        """
        from sqlalchemy import text

        new_obs = 0
        report: Dict[str, Any] = {
            "drift_detected": False,
            "drift_type": None,
            "window_size": len(self._window),
            "current_mean": self._sum / max(len(self._window), 1) if self._window else 0.0,
            "baseline_mean": self._baseline_mean,
            "new_observations": 0,
        }

        try:
            async with self.db.get_session() as session:
                r = await session.execute(
                    text("""
                        SELECT id, realized_edge
                        FROM prediction_log
                        WHERE bot_name = :bot
                          AND id > :last_id
                          AND realized_edge IS NOT NULL
                        ORDER BY id ASC
                        LIMIT 500
                    """),
                    {"bot": self.bot_name, "last_id": self._last_check_id},
                )
                rows = r.fetchall()
        except Exception as e:
            logger.debug("prediction_drift_query_failed", error=str(e))
            return report

        if not rows:
            return report

        for row in rows:
            row_id, realized_edge = row
            edge_f = float(realized_edge)
            sub_report = self._update(edge_f)
            self._last_check_id = int(row_id)
            new_obs += 1

            if sub_report["drift_detected"]:
                report["drift_detected"] = True
                report["drift_type"] = sub_report["drift_type"]

        report["window_size"] = len(self._window)
        report["current_mean"] = self._sum / max(len(self._window), 1) if self._window else 0.0
        report["baseline_mean"] = self._baseline_mean
        report["new_observations"] = new_obs

        self._last_report = report
        return report

    def _update(self, value: float) -> Dict[str, Any]:
        """Feed single realized_edge value into ADWIN-U window."""
        self._window.append(value)
        self._sum += value
        self._sum_sq += value * value
        self._n_updates += 1

        report: Dict[str, Any] = {
            "drift_detected": False,
            "drift_type": None,
        }

        if len(self._window) < self.min_window:
            return report

        # Set baseline from first full window
        if self._baseline_mean is None:
            self._baseline_mean = self._sum / len(self._window)
            variance = max(self._sum_sq / len(self._window) - self._baseline_mean ** 2, 0.0)
            self._baseline_std = max(math.sqrt(variance), 0.001)
            # Reset DDM baseline: capture current error rate as the "normal" rate
            self._error_rate_min = self._error_rate
            self._error_rate_std = math.sqrt(
                max(self._error_rate * (1 - self._error_rate) / max(self._n_updates, 1), 1e-10)
            )
            return report

        current_mean = self._sum / len(self._window)

        # DDM-style sudden drift: error rate spike > z_sudden sigma
        is_negative = 1.0 if value < 0 else 0.0
        self._error_rate += (is_negative - self._error_rate) / self._n_updates
        error_std = math.sqrt(
            max(self._error_rate * (1 - self._error_rate) / max(self._n_updates, 1), 1e-10)
        )

        if self._error_rate + error_std < self._error_rate_min + self._error_rate_std:
            self._error_rate_min = self._error_rate
            self._error_rate_std = error_std

        # DDM fires only after 2× min_window so error_rate baseline stabilizes.
        # Without this, error_rate_min gets locked at 0 from the first positive
        # observation and any subsequent negatives look like "spikes".
        if (
            self._n_updates > 2 * self.min_window
            and self._error_rate > self._error_rate_min + self.z_sudden * self._error_rate_std
        ):
            report["drift_detected"] = True
            report["drift_type"] = "sudden"
            return report

        # ADWIN-style gradual drift: mean shift beyond threshold
        if self._baseline_std and self._baseline_std > 0:
            z_score = abs(current_mean - self._baseline_mean) / self._baseline_std
            if z_score > self.z_gradual:
                report["drift_detected"] = True
                report["drift_type"] = "gradual"
                # Shrink window to adapt
                half = len(self._window) // 2
                self._window = self._window[half:]
                self._sum = sum(self._window)
                self._sum_sq = sum(r * r for r in self._window)
                self._baseline_mean = current_mean
                _var = (
                    max(self._sum_sq / len(self._window) - current_mean ** 2, 0.0)
                    if self._window
                    else 0.0
                )
                self._baseline_std = max(math.sqrt(_var), 0.001)
                return report

        # Keep window bounded
        if len(self._window) > self.max_window:
            removed = self._window.pop(0)
            self._sum -= removed
            self._sum_sq -= removed * removed

        return report

    def reset(self) -> None:
        """Reset tracker for new regime."""
        self._window.clear()
        self._sum = 0.0
        self._sum_sq = 0.0
        self._baseline_mean = None
        self._baseline_std = None
        self._error_rate = 0.0
        self._error_rate_min = float("inf")
        self._error_rate_std = 0.0
        self._n_updates = 0
        # Preserve _last_check_id to avoid re-processing old data
