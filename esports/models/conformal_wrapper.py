"""
MAPIE conformal prediction intervals for XGBoost models.

Wraps any trained XGBClassifier to produce [p_low, p_high] intervals.
Kelly sizing uses p_low (conservative bound) instead of point estimate,
preventing overconfident bets when model uncertainty is high.

Reference: Vovk et al. (2005), Romano et al. (2019) for conformal prediction.
MAPIE: Model Agnostic Prediction Interval Estimator.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ConformalPredictor:
    """Wraps a trained classifier with MAPIE conformal intervals.

    After fitting on calibration data, provides prediction intervals:
    - predict_interval(X) → (p_low, p_mid, p_high)
    - p_low is used for conservative Kelly sizing

    Gracefully degrades: if MAPIE is not installed or fitting fails,
    returns point estimate for all three values (identity behavior).
    """

    def __init__(self, alpha: float = 0.10):
        """
        Args:
            alpha: Significance level. 0.10 = 90% prediction interval.
                   Lower alpha = wider interval = more conservative.
        """
        self.alpha = alpha
        self._mapie_clf: Any = None
        self._fitted = False

    def fit(self, model: Any, X_cal: np.ndarray, y_cal: np.ndarray) -> bool:
        """Fit conformal predictor on calibration set.

        Args:
            model: Trained sklearn-compatible classifier (XGBClassifier).
            X_cal: Calibration features (held-out from training).
            y_cal: Calibration labels (0/1).

        Returns:
            True if fitting succeeded, False otherwise.
        """
        if len(X_cal) < 30:
            logger.debug("ConformalPredictor: insufficient calibration data (%d)", len(X_cal))
            return False

        try:
            from mapie.classification import MapieClassifier

            self._mapie_clf = MapieClassifier(
                estimator=model,
                cv="prefit",  # Model is already trained
                method="lac",  # Least Ambiguous set-valued Classifier
            )
            self._mapie_clf.fit(X_cal, y_cal)
            self._fitted = True
            logger.info("ConformalPredictor: fitted on %d samples (alpha=%.2f)", len(X_cal), self.alpha)
            return True
        except ImportError:
            logger.debug("ConformalPredictor: mapie not installed")
            return False
        except Exception as e:
            logger.debug("ConformalPredictor: fit failed: %s", e)
            return False

    def fit_from_predictions(
        self, predicted_probs: np.ndarray, outcomes: np.ndarray,
    ) -> bool:
        """Fit conformal predictor from historical (prediction, outcome) pairs.

        Logit-space residual approach — no sklearn model needed.
        Mirrors MirrorBot's mirror_calibration.py pattern.

        Args:
            predicted_probs: Array of predicted probabilities (0-1).
            outcomes: Array of binary outcomes (0 or 1).

        Returns:
            True if fitting succeeded.
        """
        if len(predicted_probs) < 30:
            logger.debug("ConformalPredictor: insufficient data (%d)", len(predicted_probs))
            return False

        try:
            _LOGIT_CAP = 3.0  # ~95.3%, prevents +-inf
            residuals = []
            for prob, outcome in zip(predicted_probs, outcomes):
                prob = float(prob)
                if prob <= 0.01 or prob >= 0.99:
                    continue
                logit_pred = float(np.log(prob / (1.0 - prob)))
                logit_outcome = _LOGIT_CAP if outcome > 0.5 else -_LOGIT_CAP
                residuals.append(abs(logit_pred - logit_outcome))

            if len(residuals) < 30:
                return False

            self._residuals = sorted(residuals)
            self._fitted = True
            logger.info(
                "ConformalPredictor: fitted from predictions (%d residuals, "
                "median=%.3f, p90=%.3f)",
                len(residuals),
                float(np.median(self._residuals)),
                float(np.percentile(self._residuals, 90)),
            )
            return True
        except Exception as e:
            logger.debug("ConformalPredictor: fit_from_predictions failed: %s", e)
            return False

    def predict_interval(
        self, X: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Predict with conformal intervals.

        Args:
            X: Feature array (n_samples, n_features).
               For residual-based path, X[:, 0] is treated as probability.

        Returns:
            (p_low, p_mid, p_high) — each shape (n_samples,).
            p_low/p_high are bounds of the (1-alpha) prediction interval.
            If not fitted, all three are the point estimate.
        """
        if not self._fitted:
            # Not fitted at all — identity
            try:
                p_mid = self._mapie_clf.estimator_.predict_proba(X)[:, 1]
            except Exception:
                p_mid = np.full(len(X), 0.5)
            return p_mid, p_mid, p_mid

        # Residual-based intervals (from fit_from_predictions)
        if hasattr(self, '_residuals') and self._residuals and self._mapie_clf is None:
            n_res = len(self._residuals)
            idx = int(np.ceil((1 - self.alpha) * (n_res + 1))) - 1
            idx = min(idx, n_res - 1)
            q = self._residuals[idx]

            # X[:, 0] contains probabilities (passed by caller)
            p_mid = np.clip(X[:, 0].astype(np.float64), 0.02, 0.98)
            logit_mid = np.log(p_mid / (1.0 - p_mid))
            p_low = np.clip(1.0 / (1.0 + np.exp(-(logit_mid - q))), 0.01, 0.99)
            p_high = np.clip(1.0 / (1.0 + np.exp(-(logit_mid + q))), 0.01, 0.99)
            return p_low, p_mid, p_high

        if self._mapie_clf is None:
            p_mid = np.full(len(X), 0.5)
            return p_mid, p_mid, p_mid

        try:
            # MAPIE predict returns (y_pred, y_set) where y_set is boolean mask
            y_pred, y_set = self._mapie_clf.predict(X, alpha=self.alpha)

            # y_set shape: (n_samples, n_classes, 1) — boolean inclusion mask
            # For binary: class 0 and class 1 inclusion
            p_mid = self._mapie_clf.estimator_.predict_proba(X)[:, 1]

            # Compute interval from the set predictions
            # If only class 1 is in set: high confidence → tight interval
            # If both classes in set: uncertain → wide interval
            # If neither: very uncertain
            n = len(X)
            p_low = np.zeros(n)
            p_high = np.ones(n)

            for i in range(n):
                class_0_in = bool(y_set[i, 0, 0]) if y_set.shape[1] > 0 else False
                class_1_in = bool(y_set[i, 1, 0]) if y_set.shape[1] > 1 else False

                if class_1_in and not class_0_in:
                    # High confidence in class 1
                    p_low[i] = max(0.5, p_mid[i] - 0.05)
                    p_high[i] = min(1.0, p_mid[i] + 0.05)
                elif class_0_in and not class_1_in:
                    # High confidence in class 0
                    p_low[i] = max(0.0, p_mid[i] - 0.05)
                    p_high[i] = min(0.5, p_mid[i] + 0.05)
                else:
                    # Uncertain (both or neither in set): wide interval
                    width = 0.15  # Default uncertainty width
                    p_low[i] = max(0.01, p_mid[i] - width)
                    p_high[i] = min(0.99, p_mid[i] + width)

            return p_low, p_mid, p_high

        except Exception as e:
            logger.debug("ConformalPredictor: predict_interval failed: %s", e)
            try:
                p_mid = self._mapie_clf.estimator_.predict_proba(X)[:, 1]
            except Exception:
                p_mid = np.full(len(X), 0.5)
            return p_mid, p_mid, p_mid

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def conservative_prob(self, X: np.ndarray) -> np.ndarray:
        """Return conservative probability for Kelly sizing.

        Uses p_low when model thinks YES (p > 0.5),
        uses p_high when model thinks NO (p < 0.5).
        This ensures Kelly never overestimates edge.
        """
        p_low, p_mid, p_high = self.predict_interval(X)
        result = np.where(p_mid >= 0.5, p_low, p_high)
        return np.clip(result, 0.01, 0.99)
