"""
AutoHealer - Monitors pipeline health and auto-fixes: retry failed syncs, fill gaps, refresh stale data.
Uses GapDetector and sync_log for detection; DataIngestionService for actions.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from structlog import get_logger

logger = get_logger()


class AutoHealer:
    """
    Runs health check (failed syncs, data gaps, stale prices) then applies fixes
    via DataIngestionService (ingest_all_markets, ingest_historical_prices).
    """

    def __init__(self, db: Any, gap_detector: Any, data_ingestion: Any):
        self.db = db
        self.gap_detector = gap_detector
        self.data_ingestion = data_ingestion

    async def run_health_check(self) -> Dict[str, List]:
        """Run full health check and return issues (failed_syncs, data_gaps, stale_data)."""
        issues: Dict[str, List] = {
            "failed_syncs": [],
            "data_gaps": [],
            "stale_data": [],
        }
        if not self.db or not self.db.session_factory:
            return issues
        try:
            since = datetime.now(timezone.utc) - timedelta(days=1)
            issues["failed_syncs"] = await self.db.get_failed_syncs_since(
                since=since, component="data_ingestion", limit=50
            )
        except Exception as e:
            logger.warning("AutoHealer: failed to get failed syncs: %s", e)
        if self.gap_detector:
            try:
                issues["data_gaps"] = await self.gap_detector.find_trade_gaps(
                    expected_interval_hours=6.0, limit=100
                )
            except Exception as e:
                logger.warning("AutoHealer: failed to get trade gaps: %s", e)
            try:
                issues["stale_data"] = await self.gap_detector.find_markets_without_recent_prices(
                    hours=48.0, active_only=True, limit_markets=100
                )
            except Exception as e:
                logger.warning("AutoHealer: failed to get stale markets: %s", e)
        return issues

    async def auto_heal(self) -> Dict[str, int]:
        """
        Apply fixes: retry failed syncs (one ingest_all_markets), fill top gaps, refresh top stale.
        Returns counts: retried_syncs, filled_gaps, refreshed_stale.
        """
        fixes: Dict[str, int] = {
            "retried_syncs": 0,
            "filled_gaps": 0,
            "refreshed_stale": 0,
        }
        issues = await self.run_health_check()

        if not self.data_ingestion:
            logger.warning("AutoHealer: no data_ingestion, skipping fixes")
            await self._log_healing(issues, fixes)
            return fixes

        if issues["failed_syncs"]:
            try:
                await self.data_ingestion.ingest_all_markets(top_markets_count=500)
                fixes["retried_syncs"] = len(issues["failed_syncs"])
                logger.info("AutoHealer: retried failed syncs via ingest_all_markets")
            except Exception as e:
                logger.error("AutoHealer: retry failed: %s", e, exc_info=True)

        for gap in issues["data_gaps"][:10]:
            try:
                market_id = gap.get("market_id")
                if not market_id:
                    continue
                to_ts = int(datetime.now(timezone.utc).timestamp())
                from_ts = to_ts - (7 * 24 * 60 * 60)
                await self.data_ingestion.ingest_historical_prices(
                    market_ids=[market_id],
                    from_timestamp=from_ts,
                    to_timestamp=to_ts,
                    max_markets=1,
                )
                fixes["filled_gaps"] += 1
            except Exception as e:
                logger.warning("AutoHealer: fill gap for %s failed: %s", gap.get("market_id"), e)

        for stale in issues["stale_data"][:20]:
            try:
                market_id = stale.get("market_id")
                if not market_id:
                    continue
                to_ts = int(datetime.now(timezone.utc).timestamp())
                from_ts = to_ts - (48 * 60 * 60)
                await self.data_ingestion.ingest_historical_prices(
                    market_ids=[market_id],
                    from_timestamp=from_ts,
                    to_timestamp=to_ts,
                    max_markets=1,
                )
                fixes["refreshed_stale"] += 1
            except Exception as e:
                logger.warning("AutoHealer: refresh stale %s failed: %s", stale.get("market_id"), e)

        await self._log_healing(issues, fixes)
        return fixes

    async def _log_healing(self, issues: Dict[str, List], fixes: Dict[str, int]) -> None:
        """Write healing_log row and log summary."""
        total_fixes = sum(fixes.values())
        total_issues = sum(len(v) for v in issues.values())
        if self.db and hasattr(self.db, "insert_healing_log"):
            try:
                await self.db.insert_healing_log(
                    issues_detected=total_issues,
                    fixes_applied=total_fixes,
                    details=fixes,
                )
            except Exception as e:
                logger.warning("AutoHealer: could not write healing_log: %s", e)
        logger.info(
            "AutoHealer complete",
            issues_detected=total_issues,
            fixes_applied=total_fixes,
            details=fixes,
        )
