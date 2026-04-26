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
            # venn_abers>=0.4.0 API changes vs earlier versions:
            #   1. inductive=True now REQUIRES cal_size or train_proper_size;
            #      without one the library raises "For Inductive Venn-ABERS
            #      please provide either calibration or proper train set size".
            #      0.25 = 75/25 train/calib split, the standard default.
            #   2. predict_proba(X) now returns sklearn-style (n, 2) ndarray
            #      directly. The old (p0_arr, p1_arr) tuple shape is now
            #      opt-in via p0_p1_output=True, which returns a 2-tuple of
            #      (sklearn_proba_2d, [p0_p1_2d]) where p0_p1_2d[:,0]=p_low
            #      and [:,1]=p_high are the Venn-ABERS interval bounds.
            # Both bugs were silent debug-level failures since the library
            # bump; re-enabled the per-game calibrator at
            # bots/esports_bot.py:5636-5663 which had been failing every fit.
            cal = self._vac_class(estimator=GradientBoostingClassifier(
                n_estimators=50, max_depth=1, learning_rate=0.1
            ), inductive=True, cal_size=0.25)
            cal.fit(X, y)
            self._calibrator = cal
            self._fitted = True
            self._n_samples = len(predictions)
            # Compute average interval width on training data via the new
            # p0_p1_output API.
            _, p0_p1_list = cal.predict_proba(X, p0_p1_output=True)
            p0_p1 = np.asarray(p0_p1_list).squeeze(axis=0)
            self._interval_width = float(np.mean(p0_p1[:, 1] - p0_p1[:, 0]))
            return True
        except Exception as exc:
            # Bumped from debug to warning — Protocol 10 (silent-loop
            # emission must be observable). The S195 bug class is silent
            # debug-level failures running for weeks; this fit failure
            # was the canonical example.
            logger.warning("venn_abers_fit_failed",
                           error=str(exc),
                           error_type=type(exc).__name__,
                           n_samples=int(len(predictions)))
            return False

    def calibrate(self, prob: float) -> float:
        """Return calibrated probability (library's chosen point estimate)."""
        if not self._fitted:
            return prob
        if self._calibrator is not None:
            try:
                X = np.array([[prob]])
                # New API: predict_proba(X) returns (1, 2) sklearn-style;
                # [:, 1] is P(class=1), the calibrated probability. The
                # library internally averages the Venn-ABERS interval to
                # produce this point estimate, matching the pre-API-change
                # `(p0[0] + p1[0]) / 2.0` behaviour.
                proba = self._calibrator.predict_proba(X)
                return float(proba[0, 1])
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
            # p0_p1_output=True asks for the interval bounds explicitly.
            # Returns (sklearn_proba, [p0_p1_2d]) — squeeze the list+axis-0
            # wrapper to get a (1, 2) array of [p_low, p_high].
            _, p0_p1_list = self._calibrator.predict_proba(X, p0_p1_output=True)
            p0_p1 = np.asarray(p0_p1_list).squeeze(axis=0)
            return (float(p0_p1[0, 0]), float(p0_p1[0, 1]))
        except Exception:
            return (max(0.0, prob - 0.15), min(1.0, prob + 0.15))

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def interval_width(self) -> float:
        return self._interval_width
