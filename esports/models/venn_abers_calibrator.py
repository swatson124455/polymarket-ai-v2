"""
Venn-ABERS calibrator wrapper for esports predictions.

S136 Phase 4A: Finite-sample valid calibration with provable guarantees.
At small n, produces wide intervals (natural confidence signal).
Uses the venn-abers pip package (sklearn-compatible API).
Falls back gracefully if package not installed.
"""
from __future__ import annotations

import numpy as np
from structlog import get_logger

logger = get_logger()


class VennAbersCalibrator:
    """Wraps venn-abers for probability calibration with interval estimates."""

    __slots__ = (
        "_min_samples", "_fitted", "_n_samples", "_available",
        "_calibrator", "_interval_width", "_vac_class",
        "_predictions", "_outcomes",
    )

    def __init__(self, min_samples: int = 5) -> None:
        self._min_samples = min_samples
        self._fitted = False
        self._n_samples = 0
        self._available = False
        self._calibrator = None
        self._interval_width = 1.0  # default wide interval
        self._predictions = None
        self._outcomes = None
        self._vac_class = None
        try:
            from venn_abers import VennAbersCalibrator as _VAC
            self._vac_class = _VAC
            self._available = True
        except ImportError:
            pass
        # sklearn needed for the underlying estimator
        try:
            from sklearn.ensemble import GradientBoostingClassifier  # noqa: F401
            if self._vac_class is not None:
                self._available = True
        except ImportError:
            pass

    def fit(self, predictions: np.ndarray, outcomes: np.ndarray) -> bool:
        """Fit from arrays of (predicted_prob, actual_outcome)."""
        if len(predictions) < self._min_samples:
            return False
        if self._vac_class is None:
            # Fallback: store data but no real calibration
            self._fitted = True
            self._n_samples = len(predictions)
            self._predictions = predictions.copy()
            self._outcomes = outcomes.copy()
            return True
        try:
            from sklearn.ensemble import GradientBoostingClassifier
            # Venn-ABERS needs an sklearn estimator
            X = predictions.reshape(-1, 1)
            y = outcomes.astype(int)
            cal = self._vac_class(estimator=GradientBoostingClassifier(
                n_estimators=50, max_depth=1, learning_rate=0.1
            ), inductive=True)
            cal.fit(X, y)
            self._calibrator = cal
            self._fitted = True
            self._n_samples = len(predictions)
            # Compute average interval width on training data
            p0, p1 = cal.predict_proba(X)
            self._interval_width = float(np.mean(np.abs(p1 - p0)))
            return True
        except Exception as exc:
            logger.debug("venn_abers_fit_failed", error=str(exc))
            return False

    def calibrate(self, prob: float) -> float:
        """Return calibrated probability (midpoint of interval)."""
        if not self._fitted:
            return prob
        if self._calibrator is not None:
            try:
                X = np.array([[prob]])
                p0, p1 = self._calibrator.predict_proba(X)
                return float((p0[0] + p1[0]) / 2.0)
            except Exception:
                return prob
        # Fallback: return unchanged
        return prob

    def get_interval(self, prob: float) -> tuple:
        """Return (p_low, p_high) prediction interval."""
        if not self._fitted or self._calibrator is None:
            # Wide interval when not fitted
            return (max(0.0, prob - 0.25), min(1.0, prob + 0.25))
        try:
            X = np.array([[prob]])
            p0, p1 = self._calibrator.predict_proba(X)
            return (float(p0[0]), float(p1[0]))
        except Exception:
            return (max(0.0, prob - 0.15), min(1.0, prob + 0.15))

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def interval_width(self) -> float:
        return self._interval_width
