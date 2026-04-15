"""
B3: Venn-ABERS calibration for EsportsBot v2.

Venn-ABERS produces calibrated probabilities with finite-sample validity
guarantees. We maintain separate calibrators per game (cs2, lol).

The calibrator wraps the Venn-ABERS multiprobability predictor:
  - Produces interval [p_lower, p_upper] for each prediction
  - Point estimate = (p_lower + p_upper) / 2 (midpoint)
  - Width = p_upper - p_lower (confidence measure)

Falls back to isotonic regression if venn-abers not installed.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class VennAbersCalibrator:
    """
    Per-game Venn-ABERS calibrator.

    Wraps VennAbersCalibrator from the venn-abers package. Maintains separate
    calibrators for each game to handle game-specific calibration curves.
    """

    def __init__(self) -> None:
        self._calibrators: Dict[str, object] = {}
        self._train_data: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self._fallback = False

    def fit(self, scores: np.ndarray, labels: np.ndarray, game: str) -> None:
        """
        Fit calibrator for a specific game.

        Args:
            scores: Raw model probabilities (from XGBoost).
            labels: Binary outcomes (0/1).
            game: Game identifier ('cs2', 'lol').
        """
        if len(scores) < 10:
            logger.warning(f"Too few samples for {game} calibration ({len(scores)}), using passthrough")
            self._calibrators[game] = None
            return

        self._train_data[game] = (scores.copy(), labels.copy())

        try:
            from venn_abers import VennAbers as VA
            cal = VA(setting="classification")
            # VennAbers expects 2-column probability array: [P(class=0), P(class=1)]
            probs_2d = np.column_stack([1.0 - scores, scores])
            cal.fit(probs_2d, labels)
            self._calibrators[game] = cal
            self._fallback = False
            logger.info(f"Venn-ABERS calibrator fit for {game} on {len(scores)} samples")
        except ImportError:
            logger.warning("venn-abers not installed, falling back to isotonic regression")
            self._fallback = True
            from sklearn.isotonic import IsotonicRegression
            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(scores, labels)
            self._calibrators[game] = iso

    def predict(self, score: float, game: str) -> Tuple[float, float, float]:
        """
        Calibrate a single raw score.

        Args:
            score: Raw model probability.
            game: Game identifier.

        Returns:
            (calibrated_prob, p_lower, p_upper)
            calibrated_prob = midpoint of Venn-ABERS interval.
        """
        cal = self._calibrators.get(game)
        if cal is None:
            return score, score, score

        if self._fallback:
            # Isotonic fallback
            cal_prob = float(cal.predict([score])[0])
            return cal_prob, cal_prob, cal_prob

        # VennAbers.predict_proba returns (p0_arr, p1_arr) tuple
        # p0[i] = calibrated probs assuming label=0
        # p1[i] = calibrated probs assuming label=1
        # Interval for class 1: [p0[0][1], p1[0][1]]
        try:
            test_2d = np.array([[1.0 - score, score]])
            p0, p1 = cal.predict_proba(test_2d)
            p_lower = float(p0[0][1])
            p_upper = float(p1[0][1])
            calibrated = (p_lower + p_upper) / 2.0
            return calibrated, p_lower, p_upper
        except Exception as e:
            logger.warning(f"Venn-ABERS predict failed for {game}: {e}, returning raw score")
            return score, score, score

    def predict_batch(
        self, scores: np.ndarray, game: str
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Calibrate a batch of scores.

        Returns:
            (calibrated, p_lower, p_upper) arrays.
        """
        cal = self._calibrators.get(game)
        if cal is None:
            return scores.copy(), scores.copy(), scores.copy()

        if self._fallback:
            cal_probs = cal.predict(scores)
            return cal_probs, cal_probs, cal_probs

        try:
            probs_2d = np.column_stack([1.0 - scores, scores])
            p0, p1 = cal.predict_proba(probs_2d)
            p_lower = p0[:, 1]
            p_upper = p1[:, 1]
            calibrated = (p_lower + p_upper) / 2.0
            return calibrated, p_lower, p_upper
        except Exception as e:
            logger.warning(f"Venn-ABERS batch predict failed: {e}")
            return scores.copy(), scores.copy(), scores.copy()
