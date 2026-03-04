"""
Favorite-Longshot Bias Calibration (P3-06).

Contracts at extreme prices (< 20c or > 80c) are systematically mispriced.
Build isotonic regression calibration curve from resolved prediction_log.
Apply to raw predictions before edge computation.

Dependencies: scikit-learn (IsotonicRegression).
"""
import numpy as np
from typing import Optional, Any, List, Dict
from structlog import get_logger

logger = get_logger()

MIN_RESOLVED_FOR_CALIBRATION = 50


class FavoriteLongshotCalibrator:
    """Calibrate raw predictions using isotonic regression on resolved outcomes."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._calibrator = None
        self._fitted = False

    async def fit_from_prediction_log(self, n_days: int = 90) -> bool:
        """
        Fit isotonic regression from resolved prediction_log entries.
        Returns True if calibration curve was fitted, False if insufficient data.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return False

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT predicted_prob, resolution
                    FROM prediction_log
                    WHERE resolution IS NOT NULL
                      AND prediction_time > NOW() - INTERVAL ':days days'
                    ORDER BY prediction_time DESC
                    LIMIT 5000
                """.replace(":days", str(n_days))))
                rows = r.fetchall()

            if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
                logger.info("Insufficient data for calibration: %d rows (need %d)", len(rows), MIN_RESOLVED_FOR_CALIBRATION)
                return False

            predictions = np.array([float(r[0]) for r in rows])
            outcomes = np.array([1.0 if r[1] == "YES" else 0.0 for r in rows])

            from sklearn.isotonic import IsotonicRegression
            self._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
            self._calibrator.fit(predictions, outcomes)
            self._fitted = True

            logger.info("Calibration fitted on %d resolved predictions", len(rows))
            return True

        except ImportError:
            logger.warning("sklearn not available for isotonic regression calibration")
            return False
        except Exception as e:
            logger.debug("Calibration fit failed: %s", e)
            return False

    def calibrate(self, raw_prob: float) -> float:
        """
        Apply calibration curve to raw prediction.
        If not fitted, returns raw_prob unchanged (identity).
        """
        if not self._fitted or self._calibrator is None:
            return raw_prob
        try:
            result = self._calibrator.predict([raw_prob])[0]
            return float(np.clip(result, 0.01, 0.99))
        except Exception:
            return raw_prob

    @property
    def is_fitted(self) -> bool:
        return self._fitted


class DomainCalibrator:
    """
    Per-category isotonic regression calibrators (crypto, politics, sports, economics).

    Each category has its own calibration curve because systematic biases differ:
    - Crypto markets tend to overshoot on hype
    - Political markets have known favourite-longshot bias
    - Sports markets are best-calibrated (deep liquidity)
    """

    CATEGORIES = ("crypto", "politics", "sports", "economics")

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._calibrators: Dict[str, FavoriteLongshotCalibrator] = {}
        self._global = FavoriteLongshotCalibrator(db=db)

    async def fit_all(self, n_days: int = 90) -> Dict[str, bool]:
        """Fit per-category calibrators + global fallback."""
        results = {}
        # Global
        results["global"] = await self._global.fit_from_prediction_log(n_days)

        # Per-category
        for cat in self.CATEGORIES:
            cal = FavoriteLongshotCalibrator(db=self.db)
            fitted = await self._fit_category(cal, cat, n_days)
            results[cat] = fitted
            if fitted:
                self._calibrators[cat] = cal

        return results

    async def _fit_category(self, cal: FavoriteLongshotCalibrator, category: str, n_days: int) -> bool:
        """Fit calibrator for a single category."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return False
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT pl.predicted_prob, pl.resolution
                    FROM prediction_log pl
                    JOIN markets m ON pl.market_id = m.id
                    WHERE pl.resolution IS NOT NULL
                      AND LOWER(m.market_category) = :category
                      AND pl.prediction_time > NOW() - INTERVAL ':days days'
                    ORDER BY pl.prediction_time DESC
                    LIMIT 5000
                """.replace(":days", str(n_days)).replace(":category", f"'{category}'")),
                    {"category": category})
                rows = r.fetchall()

            if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
                return False

            predictions = np.array([float(r[0]) for r in rows])
            outcomes = np.array([1.0 if r[1] == "YES" else 0.0 for r in rows])

            from sklearn.isotonic import IsotonicRegression
            cal._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
            cal._calibrator.fit(predictions, outcomes)
            cal._fitted = True
            logger.info("Category calibrator fitted: %s (%d samples)", category, len(rows))
            return True
        except Exception as e:
            logger.debug("Category calibrator fit failed for %s: %s", category, e)
            return False

    def calibrate(self, raw_prob: float, category: str = "") -> float:
        """Calibrate probability using category-specific calibrator (falls back to global)."""
        cat = category.lower().strip()
        if cat in self._calibrators and self._calibrators[cat].is_fitted:
            return self._calibrators[cat].calibrate(raw_prob)
        if self._global.is_fitted:
            return self._global.calibrate(raw_prob)
        return raw_prob
