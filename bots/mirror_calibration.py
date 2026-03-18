"""
MirrorBot Calibration Integration — Session 82, cleaned S103.

Wires existing FocalTemperatureCalibrator and HorizonBiasCalibrator
into MirrorBot's confidence/sizing pipeline.

Gated behind env var (default off):
  MIRROR_USE_CALIBRATION=true  — apply FTS + Le(2026) domain bias to confidence

Calibrators are already implemented in base_engine/features/calibration.py.
This module provides the MirrorBot-specific init + apply helpers.
"""
import numpy as np
from typing import Any, Dict, Optional

from structlog import get_logger
from config.settings import settings

logger = get_logger()


class MirrorCalibrationStack:
    """Manages FTS + horizon bias calibration for MirrorBot."""

    def __init__(self, db: Any = None):
        self._db = db
        self._fts = None  # FocalTemperatureCalibrator
        self._horizon = None  # HorizonBiasCalibrator
        self._fitted = False

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
