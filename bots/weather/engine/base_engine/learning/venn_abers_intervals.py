"""
7K: Venn-ABERS prediction intervals for MirrorBot gate_score calibration.

Provably calibrated probability intervals (p0, p1) bracketing P(y=1|score=s)
under exchangeability.  Implements the Inductive Venn-ABERS Predictor (IVAP)
using only sklearn.IsotonicRegression — no extra pip dependencies.

Algorithm (Vovk & Petej 2014):
  Given calibration set {(s_i, y_i)} i=1..n and test score s_test:
    p0 = IsotonicRegression().fit([s_test; s_1..n], [0; y_1..n]).predict(s_test)
    p1 = IsotonicRegression().fit([s_test; s_1..n], [1; y_1..n]).predict(s_test)
  Return (p0, p1) where p0 ≤ P(y=1|s_test) ≤ p1.

Interval width shrinks as n grows (finite-sample guarantee: wide intervals
when data is thin, tight intervals when data is plentiful — natural
confidence signal).

For MirrorBot: score = gate_score (0.20-0.85), y = was_correct (0/1).
With current ~7 resolved predictions, intervals will be very wide — that's
correct behavior, not a bug.  As resolutions accumulate (~weeks), intervals
tighten and become actionable.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import structlog

logger = structlog.get_logger()


class VennAbersIntervalCalibrator:
    """
    Inductive Venn-ABERS predictor producing (p0, p1) probability intervals.

    Stateless fit: stores calibration set; per-prediction computes two
    isotonic regressions (one for each hypothetical label extension).
    O(n log n) per prediction.
    """

    def __init__(self, min_samples: int = 30):
        self.min_samples = min_samples
        self._scores: Optional[np.ndarray] = None
        self._labels: Optional[np.ndarray] = None
        self._n: int = 0
        self._fitted: bool = False

    @property
    def n_samples(self) -> int:
        return self._n

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(self, scores: List[float], labels: List[int]) -> bool:
        """
        Store calibration set.  Returns True if fitted successfully.

        Args:
            scores: raw classifier outputs (e.g., MirrorBot gate_score, 0.20-0.85)
            labels: binary outcomes (was_correct, 0 or 1)
        """
        if len(scores) != len(labels):
            raise ValueError("scores and labels must have the same length")
        if len(scores) < self.min_samples:
            logger.debug(
                "venn_abers_insufficient_data",
                n=len(scores),
                min_samples=self.min_samples,
            )
            self._fitted = False
            return False

        s = np.asarray(scores, dtype=float)
        y = np.asarray(labels, dtype=int)
        if not np.all((y == 0) | (y == 1)):
            raise ValueError("labels must be 0 or 1")

        self._scores = s
        self._labels = y
        self._n = len(s)
        self._fitted = True
        return True

    def predict_interval(self, score: float) -> Tuple[float, float]:
        """
        Return (p0, p1) prediction interval for a single score.

        Falls back to a wide default interval if not fitted.
        """
        if not self._fitted or self._scores is None or self._labels is None:
            # Unfitted: return a wide default interval centered at score
            lo = max(0.0, float(score) - 0.25)
            hi = min(1.0, float(score) + 0.25)
            return (lo, hi)

        # Lazy import to avoid sklearn load at module import time
        from sklearn.isotonic import IsotonicRegression

        s_test = float(score)

        # Hypothesis 0: extend calibration set with (s_test, 0)
        ext_scores_0 = np.append(self._scores, s_test)
        ext_labels_0 = np.append(self._labels, 0)
        iso0 = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso0.fit(ext_scores_0, ext_labels_0)
        p0 = float(iso0.predict([s_test])[0])

        # Hypothesis 1: extend calibration set with (s_test, 1)
        ext_scores_1 = np.append(self._scores, s_test)
        ext_labels_1 = np.append(self._labels, 1)
        iso1 = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso1.fit(ext_scores_1, ext_labels_1)
        p1 = float(iso1.predict([s_test])[0])

        # Under Venn-ABERS theory: p0 ≤ p1.  Clamp if numerical noise inverts.
        if p0 > p1:
            p0, p1 = p1, p0
        return (p0, p1)

    def predict_midpoint(self, score: float) -> float:
        """Return single calibrated probability (midpoint of interval)."""
        p0, p1 = self.predict_interval(score)
        return 0.5 * (p0 + p1)

    def predict_width(self, score: float) -> float:
        """Return interval width p1 - p0 (confidence signal: narrower = more confident)."""
        p0, p1 = self.predict_interval(score)
        return p1 - p0

    def predict_batch(self, scores: List[float]) -> List[Tuple[float, float]]:
        """Batch prediction.  Each score independently recomputes isotonic regressions."""
        return [self.predict_interval(s) for s in scores]

    async def fit_from_prediction_log(
        self,
        db,
        bot_name: str = "MirrorBot",
        score_col: str = "confidence",
        label_col: str = "was_correct",
        limit: int = 10000,
    ) -> bool:
        """
        Load resolved predictions from prediction_log and fit.

        Returns True if sufficient data was available and fit succeeded.
        """
        from sqlalchemy import text

        try:
            async with db.get_session() as session:
                r = await session.execute(
                    text(f"""
                        SELECT {score_col}, {label_col}
                        FROM prediction_log
                        WHERE bot_name = :bot
                          AND {score_col} IS NOT NULL
                          AND {label_col} IS NOT NULL
                        ORDER BY prediction_time DESC
                        LIMIT :lim
                    """),
                    {"bot": bot_name, "lim": limit},
                )
                rows = r.fetchall()
        except Exception as e:
            logger.debug("venn_abers_db_load_failed", error=str(e))
            return False

        if not rows:
            logger.debug("venn_abers_no_data", bot=bot_name)
            return False

        scores = [float(row[0]) for row in rows]
        labels = [int(bool(row[1])) for row in rows]
        ok = self.fit(scores, labels)
        if ok:
            logger.info(
                "venn_abers_fitted",
                bot=bot_name,
                n_samples=self._n,
                score_range=(round(min(scores), 4), round(max(scores), 4)),
                base_rate=round(sum(labels) / len(labels), 4),
            )
        return ok
