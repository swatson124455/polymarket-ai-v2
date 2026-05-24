"""
Rule-Based Config Tuner (P5-01).

Periodically reads performance metrics from prediction_log and adjusts
tunable parameters via TunableConfigStore. Simple rule-based for v1.

Rules:
- Accuracy dropping → increase MIN_CONFIDENCE_THRESHOLD
- Drawdown increasing → decrease KELLY_FRACTION
- Win rate above target → loosen thresholds slightly
"""
from typing import Optional, Any
from structlog import get_logger

logger = get_logger()


class ConfigTuner:
    """Rule-based tuner that adjusts parameters based on live performance."""

    def __init__(self, db: Optional[Any] = None, config_store: Optional[Any] = None):
        self.db = db
        self.config_store = config_store

    async def tune(self) -> dict:
        """
        Run one tuning cycle. Reads recent performance, adjusts parameters.
        Returns dict of {key: new_value} for parameters that were changed.
        """
        if not self.db or not self.config_store:
            return {}

        changes = {}
        try:
            perf = await self._get_recent_performance()
            if not perf or perf.get("count", 0) < 20:
                return {}

            accuracy = perf.get("accuracy", 0.5)
            brier = perf.get("brier", 0.25)

            # Rule 1: Accuracy-based confidence threshold adjustment
            current_conf = self.config_store.get("MIN_CONFIDENCE_THRESHOLD", 0.55)
            if accuracy < 0.40:
                new_conf = min(current_conf + 0.05, 0.90)
                if new_conf != current_conf:
                    await self.config_store.set("MIN_CONFIDENCE_THRESHOLD", new_conf, "config_tuner")
                    changes["MIN_CONFIDENCE_THRESHOLD"] = new_conf
            elif accuracy > 0.65:
                new_conf = max(current_conf - 0.02, 0.50)
                if new_conf != current_conf:
                    await self.config_store.set("MIN_CONFIDENCE_THRESHOLD", new_conf, "config_tuner")
                    changes["MIN_CONFIDENCE_THRESHOLD"] = new_conf

            # Rule 2: Brier-based Kelly fraction adjustment
            current_kelly = self.config_store.get("KELLY_FRACTION", 0.25)
            if brier > 0.30:
                new_kelly = max(current_kelly * 0.8, 0.05)
                if abs(new_kelly - current_kelly) > 0.01:
                    await self.config_store.set("KELLY_FRACTION", round(new_kelly, 3), "config_tuner")
                    changes["KELLY_FRACTION"] = round(new_kelly, 3)
            elif brier < 0.15 and accuracy > 0.60:
                new_kelly = min(current_kelly * 1.1, 0.50)
                if abs(new_kelly - current_kelly) > 0.01:
                    await self.config_store.set("KELLY_FRACTION", round(new_kelly, 3), "config_tuner")
                    changes["KELLY_FRACTION"] = round(new_kelly, 3)

            # Rule 3: Edge threshold based on accuracy
            current_edge = self.config_store.get("RISK_MIN_EDGE_PCT", 2.0)
            if accuracy < 0.45:
                new_edge = min(current_edge + 0.5, 8.0)
                if new_edge != current_edge:
                    await self.config_store.set("RISK_MIN_EDGE_PCT", new_edge, "config_tuner")
                    changes["RISK_MIN_EDGE_PCT"] = new_edge

            if changes:
                logger.info("Config tuner adjusted %d params", len(changes), changes=changes)
            return changes

        except Exception as e:
            logger.debug("Config tuner failed: %s", e)
            return {}

    async def _get_recent_performance(self) -> dict:
        """Get recent prediction performance from prediction_log."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return {}
        try:
            perf = await self.db.get_recent_brier_from_prediction_log(50)
            return perf or {}
        except Exception:
            return {}
