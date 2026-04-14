"""
B4: MAPIE conformal prediction filter for EsportsBot v2.

Uses MAPIE's Least Ambiguous set-valued Classifier (LAC) at alpha=0.10.
Only singleton prediction sets (set = {team_a} or {team_b}) are bettable.
Multi-label sets (both classes) = uncertain = abstain.

This filter is the final quality gate before sizing. It ensures we only
bet on matches where the model is confident enough that the prediction
set excludes one class entirely.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ConformalFilter:
    """
    MAPIE conformal prediction filter.

    Fits on calibration set (raw probs + labels), then for new predictions
    determines whether the conformal set is a singleton (bettable) or
    multi-label (abstain).
    """

    def __init__(self, alpha: float = 0.10) -> None:
        """
        Args:
            alpha: Significance level. Lower = wider sets = more abstains.
                   0.10 = 90% marginal coverage guarantee.
        """
        self._alpha = alpha
        self._fitted = False
        # Store calibration nonconformity scores for LAC
        self._cal_scores: Optional[np.ndarray] = None
        self._quantile: float = 1.0  # default: everything is singleton

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> None:
        """
        Fit conformal predictor on calibration data.

        Uses LAC (Least Ambiguous Criterion): nonconformity score = 1 - p(true_class).
        The quantile of these scores at level (1-alpha)(1+1/n) determines the
        prediction set threshold.

        Args:
            probs: Predicted P(class=1) from calibrated model.
            labels: True binary labels (0 or 1).
        """
        n = len(probs)
        if n < 20:
            logger.warning(f"Only {n} calibration samples, conformal filter may be unreliable")
            self._fitted = False
            return

        # Nonconformity scores: 1 - P(true class)
        scores = np.where(labels == 1, 1 - probs, probs)
        self._cal_scores = np.sort(scores)

        # Quantile at level ceil((n+1)(1-alpha))/n
        import math
        q_level = math.ceil((n + 1) * (1 - self._alpha)) / n
        q_level = min(q_level, 1.0)
        self._quantile = float(np.quantile(self._cal_scores, q_level))
        self._fitted = True

        # Stats
        n_singleton = sum(1 for p in probs for _ in [1] if self._is_singleton_score(p))
        logger.info(
            f"Conformal filter fit: n={n}, alpha={self._alpha}, "
            f"quantile={self._quantile:.4f}, "
            f"singleton_rate={n_singleton/n:.1%}"
        )

    def _is_singleton_score(self, prob: float) -> bool:
        """Check if a probability produces a singleton set."""
        # Class 0 in set if: prob (score for class 0 = prob) <= quantile  ->  actually 1-prob <= quantile
        # Class 1 in set if: 1-prob (score for class 1) <= quantile
        # Singleton = exactly one class in set
        class_0_in = (1 - prob) <= self._quantile  # score for y=0 is prob
        class_1_in = prob <= self._quantile          # score for y=1 is 1-prob
        # Wait — LAC: score(y) = 1 - p(y). Class y is in set if score(y) <= quantile.
        # score(0) = 1 - P(Y=0) = 1 - (1-prob) = prob
        # score(1) = 1 - P(Y=1) = 1 - prob
        score_0 = prob          # nonconformity of class 0
        score_1 = 1 - prob      # nonconformity of class 1
        in_0 = score_0 <= self._quantile
        in_1 = score_1 <= self._quantile
        return (in_0 and not in_1) or (not in_0 and in_1)

    def predict(self, prob: float) -> Dict:
        """
        Predict conformal set for a single probability.

        Args:
            prob: Calibrated P(team_a wins).

        Returns:
            dict with:
              conformal_set: list of class labels in the set
              is_singleton: bool
              predicted_class: int (0 or 1) — the singleton class, or argmax if multi
        """
        if not self._fitted:
            # Not fitted — pass everything through as singleton
            predicted = 1 if prob > 0.5 else 0
            return {
                "conformal_set": [predicted],
                "is_singleton": True,
                "predicted_class": predicted,
            }

        score_0 = prob
        score_1 = 1 - prob
        in_set = []
        if score_0 <= self._quantile:
            in_set.append(0)
        if score_1 <= self._quantile:
            in_set.append(1)

        if not in_set:
            # Empty set — rare, treat as abstain
            in_set = [0, 1]

        is_singleton = len(in_set) == 1
        predicted = in_set[0] if is_singleton else (1 if prob > 0.5 else 0)

        return {
            "conformal_set": in_set,
            "is_singleton": is_singleton,
            "predicted_class": predicted,
        }

    def predict_batch(self, probs: np.ndarray) -> List[Dict]:
        """Predict conformal sets for a batch."""
        return [self.predict(float(p)) for p in probs]

    @property
    def singleton_rate_estimate(self) -> float:
        """Estimated singleton rate from calibration data."""
        if self._cal_scores is None:
            return 1.0
        # Approximate: count how many cal scores would produce singletons
        # This is rough — actual rate depends on test distribution
        return float(np.mean(self._cal_scores > self._quantile))
