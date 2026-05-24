"""
Data Quality SLA Monitoring (#48) - track SLA metrics and alert on violations.

SLA dashboard and alerts when metrics fall outside thresholds.
"""
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()


class DataQualitySLA:
    """
    Check data quality metrics against SLA thresholds; optionally alert.
    """

    def __init__(
        self,
        db: Optional[Any] = None,
        alert_callback: Optional[Any] = None,
    ):
        self.db = db
        self.alert_callback = alert_callback

    async def get_sla_definitions(self) -> List[Dict[str, Any]]:
        """Load SLA definitions from data_quality_sla table."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return []
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                result = await session.execute(
                    text("SELECT id, sla_name, metric_name, threshold_min, threshold_max, window_minutes, alert_on_violation FROM data_quality_sla")
                )
                rows = result.mappings().all()
            return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.debug("data_quality_sla get_sla_definitions failed: %s", e)
            return []

    async def check_metric(
        self,
        metric_name: str,
        value: float,
        threshold_min: Optional[float] = None,
        threshold_max: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Check one metric value against thresholds; return status and violation flag."""
        violated = False
        if threshold_min is not None and value < threshold_min:
            violated = True
        if threshold_max is not None and value > threshold_max:
            violated = True
        return {
            "metric_name": metric_name,
            "value": value,
            "threshold_min": threshold_min,
            "threshold_max": threshold_max,
            "violated": violated,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def run_checks(
        self,
        metrics: Dict[str, float],
        slas: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run SLA checks for given metrics. If slas is None, load from DB.
        Returns list of check results; calls alert_callback on violation when configured.
        """
        if slas is None:
            slas = await self.get_sla_definitions()
        results = []
        for sla in slas:
            name = sla.get("sla_name") or sla.get("metric_name")
            metric = sla.get("metric_name")
            if metric not in metrics:
                continue
            value = metrics[metric]
            r = await self.check_metric(
                metric_name=metric,
                value=value,
                threshold_min=sla.get("threshold_min"),
                threshold_max=sla.get("threshold_max"),
            )
            r["sla_name"] = name
            results.append(r)
            if r.get("violated") and sla.get("alert_on_violation") and self.alert_callback:
                try:
                    if asyncio.iscoroutinefunction(self.alert_callback):
                        await self.alert_callback(r)
                    else:
                        self.alert_callback(r)
                except Exception as e:
                    logger.warning("data_quality_sla alert_callback error: %s", e)
        return results

    async def seed_default_slas(self) -> int:
        """
        Insert default SLA definitions if table exists and rows are missing.
        Returns number of rows inserted.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return 0
        defaults = [
            ("data_freshness_minutes_max", "data_freshness_minutes", None, 60.0, 60, True),
            ("sync_success_rate_min", "sync_success_rate", 0.95, None, 60, True),
            ("price_coverage_min", "price_coverage_ratio", 0.8, None, 60, True),
            ("quality_issues_count_max", "quality_issues_count", None, 100.0, 1440, True),
        ]
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                for sla_name, metric_name, threshold_min, threshold_max, window_minutes, alert_on in defaults:
                    await session.execute(
                        text(
                            "INSERT INTO data_quality_sla (sla_name, metric_name, threshold_min, threshold_max, window_minutes, alert_on_violation) "
                            "VALUES (:sla_name, :metric_name, :threshold_min, :threshold_max, :window_minutes, :alert_on) "
                            "ON CONFLICT (sla_name) DO NOTHING"
                        ),
                        {
                            "sla_name": sla_name,
                            "metric_name": metric_name,
                            "threshold_min": threshold_min,
                            "threshold_max": threshold_max,
                            "window_minutes": window_minutes,
                            "alert_on": alert_on,
                        },
                    )
                await session.commit()
            logger.info("data_quality_sla seed_default_slas applied %s definitions", len(defaults))
            return len(defaults)
        except Exception as e:
            logger.debug("data_quality_sla seed_default_slas failed (table may not exist): %s", e)
            return 0
