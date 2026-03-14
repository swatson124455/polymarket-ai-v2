"""
MirrorBot Calibration Integration — Session 82.

Wires existing FocalTemperatureCalibrator, HorizonBiasCalibrator, and
conformal prediction into MirrorBot's confidence/sizing pipeline.

All features gated behind env vars (default off):
  MIRROR_USE_CALIBRATION=true  — apply FTS + Le(2026) domain bias to confidence
  MIRROR_USE_CONFORMAL=true    — pass conformal interval to Kelly sizing

Calibrators are already implemented in base_engine/features/calibration.py.
This module provides the MirrorBot-specific init + apply helpers.
"""
import numpy as np
from typing import Any, Dict, Optional, Tuple

from structlog import get_logger
from config.settings import settings

logger = get_logger()


class MirrorCalibrationStack:
    """Manages calibration + conformal prediction for MirrorBot."""

    def __init__(self, db: Any = None):
        self._db = db
        self._fts = None  # FocalTemperatureCalibrator
        self._horizon = None  # HorizonBiasCalibrator
        self._fitted = False
        self._conformal_fitted = False
        # Conformal prediction: simple Venn-ABERS style from resolved trades
        self._conformal_residuals: list = []  # |predicted - outcome| for resolved trades

    async def fit(self) -> Dict[str, bool]:
        """Fit all calibrators from DB. Call on first scan, re-fit daily."""
        results: Dict[str, bool] = {}

        if not getattr(settings, "MIRROR_USE_CALIBRATION", False):
            return results

        try:
            from base_engine.features.calibration import (
                FocalTemperatureCalibrator,
                HorizonBiasCalibrator,
            )

            # Focal Temperature Scaling
            self._fts = FocalTemperatureCalibrator(db=self._db)
            results["fts"] = await self._fts.fit_from_prediction_log(n_days=180)
            if results["fts"]:
                logger.info(
                    "mirror_calibration_fts_fitted",
                    temperature=round(self._fts.temperature, 2),
                    gamma=round(self._fts.gamma, 1),
                )

            # Le (2026) Horizon Bias
            self._horizon = HorizonBiasCalibrator(db=self._db)
            results["horizon"] = await self._horizon.fit_from_paper_trades(n_days=180)
            if results["horizon"]:
                logger.info("mirror_calibration_horizon_fitted")

            self._fitted = results.get("fts", False) or results.get("horizon", False)

        except Exception as e:
            logger.warning("mirror_calibration_fit_error", error=str(e))

        return results

    async def fit_conformal(self) -> bool:
        """Fit conformal prediction residuals from resolved MirrorBot trades."""
        if not getattr(settings, "MIRROR_USE_CONFORMAL", False):
            return False
        if not self._db or not getattr(self._db, "session_factory", None):
            return False

        min_resolved = getattr(settings, "MIRROR_CONFORMAL_MIN_RESOLVED", 50)

        try:
            from sqlalchemy import text
            async with self._db.get_session() as session:
                rows = await session.execute(text(
                    "SELECT e.confidence, e.price, r.realized_pnl "
                    "FROM trade_events r "
                    "JOIN trade_events e "
                    "  ON e.market_id = r.market_id "
                    "  AND e.bot_name = r.bot_name "
                    "  AND e.event_type = 'ENTRY' "
                    "WHERE r.bot_name = 'MirrorBot' "
                    "  AND r.event_type = 'RESOLUTION' "
                    "  AND r.realized_pnl IS NOT NULL "
                    "  AND e.confidence IS NOT NULL "
                    "ORDER BY r.event_time DESC LIMIT 2000"
                ))
                data = rows.fetchall()

            if len(data) < min_resolved:
                logger.info(
                    "mirror_conformal: insufficient data (%d/%d)",
                    len(data), min_resolved,
                )
                return False

            # Compute non-conformity scores: |confidence - outcome|
            residuals = []
            for row in data:
                prob = float(row[0]) if row[0] else None
                pnl = float(row[2])
                if prob is None or prob <= 0 or prob >= 1:
                    continue
                outcome = 1.0 if pnl > 0 else 0.0
                residuals.append(abs(prob - outcome))

            if len(residuals) < min_resolved:
                return False

            self._conformal_residuals = sorted(residuals)
            self._conformal_fitted = True
            _alpha = getattr(settings, "MIRROR_CONFORMAL_ALPHA", 0.50)
            _q_idx = int(np.ceil((1 - _alpha) * (len(residuals) + 1))) - 1
            _q_idx = min(_q_idx, len(residuals) - 1)
            _q_at_alpha = self._conformal_residuals[_q_idx]
            logger.info(
                "mirror_conformal_fitted",
                n_residuals=len(residuals),
                median_residual=round(float(np.median(residuals)), 3),
                p90_residual=round(float(np.percentile(residuals, 90)), 3),
                alpha=_alpha,
                q_at_alpha=round(float(_q_at_alpha), 3),
            )
            return True

        except Exception as e:
            logger.warning("mirror_conformal_fit_error", error=str(e))
            return False

    def calibrate_confidence(
        self,
        raw_confidence: float,
        category: str = "",
        ttr_days: Optional[float] = None,
    ) -> float:
        """Apply calibration stack to raw confidence. Returns calibrated value."""
        if not self._fitted or not getattr(settings, "MIRROR_USE_CALIBRATION", False):
            return raw_confidence

        conf = raw_confidence

        # Step 1: Focal Temperature Scaling
        if self._fts and self._fts.is_fitted:
            conf = self._fts.calibrate(conf)

        # Step 2: Le (2026) domain x horizon bias correction
        if self._horizon and self._horizon.is_fitted:
            conf = self._horizon.calibrate(conf, category=category, ttr_days=ttr_days)

        return float(np.clip(conf, 0.01, 0.99))

    def get_conformal_interval(
        self, confidence: float, alpha: Optional[float] = None
    ) -> Optional[Tuple[float, float]]:
        """
        Compute conformal prediction interval at (1-alpha) coverage.

        Returns (p_low, p_high) or None if not fitted.
        Uses split conformal prediction with historical residuals.
        """
        if not self._conformal_fitted or not getattr(settings, "MIRROR_USE_CONFORMAL", False):
            return None

        if alpha is None:
            alpha = getattr(settings, "MIRROR_CONFORMAL_ALPHA", 0.50)

        n = len(self._conformal_residuals)
        if n < 10:
            return None

        # Quantile of residuals at (1-alpha) level
        idx = int(np.ceil((1 - alpha) * (n + 1))) - 1
        idx = min(idx, n - 1)
        q = self._conformal_residuals[idx]

        p_low = max(0.01, confidence - q)
        p_high = min(0.99, confidence + q)

        return (p_low, p_high)
