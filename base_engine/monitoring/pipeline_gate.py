"""
PipelineGate - Post-condition checker between pipeline stages.

Closes the fire-and-forget loop: verifies what previous stages produced
and triggers remediation (alerting, AutoHealer) on failure.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()


@dataclass
class GateResult:
    """Result of a pipeline gate check."""

    passed: bool
    failures: List[str] = field(default_factory=list)
    retriable: bool = False
    stale: bool = False
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.summary and self.failures:
            self.summary = "; ".join(self.failures[:3])
            if len(self.failures) > 3:
                self.summary += f" (+{len(self.failures) - 3} more)"


class PipelineGate:
    """
    Post-condition checker between pipeline stages.
    Wires sync_log, canary queries, AlertingSystem, and AutoHealer.
    """

    def __init__(
        self,
        db: Any,
        alerting: Optional[Any] = None,
        settings: Optional[Any] = None,
    ):
        self.db = db
        self.alerting = alerting
        self.settings = settings or __import__("config.settings", fromlist=["settings"]).settings
        # Wire DataQualitySLA alert_callback to AlertingSystem
        self._sla: Optional[Any] = None
        if db and alerting:
            try:
                from base_engine.monitoring.data_quality_sla import DataQualitySLA
                from base_engine.monitoring.alerting import AlertSeverity

                self._sla = DataQualitySLA(db)

                async def _on_sla_violation(violation: Dict[str, Any]) -> None:
                    await alerting.send_alert(
                        title=f"SLA violation: {violation.get('metric_name', 'sla')}",
                        message=str(violation),
                        severity=AlertSeverity.WARNING,
                        source="data_quality_sla",
                        metadata=violation,
                    )

                self._sla.alert_callback = _on_sla_violation
            except Exception as e:
                logger.debug("DataQualitySLA wiring failed: %s", e)

    def _get_threshold(self, name: str, default: Any) -> Any:
        return getattr(self.settings, f"PIPELINE_GATE_{name}", default)

    async def _check_freshness(
        self,
        table: str,
        max_hours: float,
        timestamp_col: str = "updated_at",
    ) -> Optional[str]:
        """Return failure message if table is stale, else None."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return "Database not available"
        try:
            from sqlalchemy import text

            col = timestamp_col
            if table == "market_prices":
                col = "timestamp"
            async with self.db.get_session() as session:
                result = await session.execute(
                    text(f"SELECT MAX({col}) FROM {table}")
                )
                row = result.scalar_one_or_none()
            if row is None:
                return f"{table}: no rows (empty table)"
            max_ts = row
            if getattr(max_ts, "tzinfo", None) is None:
                max_ts = max_ts.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - max_ts).total_seconds() / 3600
            if age_hours > max_hours:
                return f"{table}: max {timestamp_col} is {age_hours:.1f}h ago (max {max_hours}h)"
            return None
        except Exception as e:
            return f"{table} freshness check failed: {e}"

    async def _check_record_count(
        self,
        table: str,
        min_count: int,
        where: Optional[str] = None,
    ) -> Optional[str]:
        """Return failure message if count below threshold, else None."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return "Database not available"
        try:
            from sqlalchemy import text

            q = f"SELECT COUNT(*) FROM {table}"
            if where:
                q += f" WHERE {where}"
            async with self.db.get_session() as session:
                result = await session.execute(text(q))
                count = result.scalar_one_or_none() or 0
            if count < min_count:
                return f"{table}: count {count} < {min_count}"
            return None
        except Exception as e:
            return f"{table} count check failed: {e}"

    async def _check_sync_success(
        self,
        lookback_hours: float = 24,
        min_rate: float = 0.5,
    ) -> Optional[str]:
        """Return failure message if sync success rate below threshold, else None."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return "Database not available"
        try:
            from sqlalchemy import text

            q = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'success') as success
                FROM sync_log
                WHERE started_at > NOW() - INTERVAL '1 hour' * :hours
                AND component IN ('data_ingestion', 'markets', 'prices')
            """
            async with self.db.get_session() as session:
                result = await session.execute(
                    text(q), {"hours": lookback_hours}
                )
                row = result.fetchone()
            total = row[0] if row else 0
            success = row[1] if row and len(row) > 1 else 0
            if total == 0:
                return None  # No syncs in window, skip
            rate = success / total
            if rate < min_rate:
                return f"sync success rate {rate:.1%} < {min_rate:.0%} (last {lookback_hours}h)"
            return None
        except Exception as e:
            return f"sync success check failed: {e}"

    async def _check_orphan_trades(self) -> Optional[str]:
        """Return failure message if orphan paper trades exist, else None.

        Checks paper_trades (our managed trades) not the historical on-chain
        trades table, which legitimately contains millions of rows referencing
        markets we never ingested.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return "Database not available"
        try:
            from sqlalchemy import text

            q = """
                SELECT COUNT(*) FROM paper_trades pt
                WHERE pt.market_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM markets m
                    WHERE m.id::text = pt.market_id OR m.condition_id = pt.market_id
                )
            """
            async with self.db.get_session() as session:
                result = await session.execute(text(q))
                count = result.scalar_one_or_none() or 0
            if count > 500:
                return f"orphan paper_trades: {count} trades reference missing markets"
            return None
        except Exception as e:
            return f"orphan check failed: {e}"

    async def _check_end_date_iso_population(self) -> Optional[str]:
        """Return warning if >80% of active markets are missing end_date_iso.
        Catches the endDateISO vs endDate field name mismatch that caused 100% NULL end dates.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return None  # non-blocking
        try:
            from sqlalchemy import text

            async with self.db.get_session() as session:
                result = await session.execute(text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE end_date_iso IS NULL) AS null_count
                    FROM markets
                    WHERE active = TRUE
                """))
                row = result.fetchone()
            total = int(row[0] or 0) if row else 0
            null_count = int(row[1] or 0) if row else 0
            if total < 10:
                return None  # not enough data
            null_pct = null_count / total
            if null_pct > 0.80:
                return (
                    f"Schema conformance: {null_pct:.0%} of active markets have NULL end_date_iso "
                    f"({null_count}/{total}) — ingestion field name mismatch (endDate vs endDateISO)"
                )
            return None
        except Exception as e:
            return None  # non-blocking

    async def check_ingestion(self) -> GateResult:
        """
        Run after ingestion. Checks: markets exist, prices fresh, sync health, no orphans.
        """
        failures: List[str] = []
        max_markets_hours = self._get_threshold("MARKETS_FRESHNESS_HOURS", 2.0)
        max_prices_hours = self._get_threshold("PRICES_FRESHNESS_HOURS", 24.0)
        min_markets = self._get_threshold("MIN_MARKETS_COUNT", 100)
        sync_min_rate = self._get_threshold("SYNC_SUCCESS_MIN_RATE", 0.5)
        sync_lookback = self._get_threshold("SYNC_LOOKBACK_HOURS", 24.0)

        err = await self._check_freshness("markets", max_markets_hours)
        if err:
            failures.append(err)

        err = await self._check_freshness("market_prices", max_prices_hours)
        if err:
            failures.append(err)

        err = await self._check_record_count("markets", min_markets)
        if err:
            failures.append(err)

        err = await self._check_sync_success(
            lookback_hours=sync_lookback,
            min_rate=sync_min_rate,
        )
        if err:
            failures.append(err)

        err = await self._check_orphan_trades()
        if err:
            failures.append(err)

        # Schema conformance: end_date_iso must not be NULL for recently ingested markets.
        # Root cause: endDateISO vs endDate field name mismatch caused 100% NULL rate.
        # Threshold: warn if >80% of markets active in last 24h are missing end_date_iso.
        err = await self._check_end_date_iso_population()
        if err:
            failures.append(err)

        retriable = any(
            "sync" in f.lower() or "stale" in f.lower() or "empty" in f.lower()
            for f in failures
        )
        return GateResult(
            passed=len(failures) == 0,
            failures=failures,
            retriable=retriable,
            stale=any("stale" in f.lower() or "h ago" in f for f in failures),
            summary="; ".join(failures[:3]) if failures else "OK",
        )

    async def check_before_training(self) -> GateResult:
        """
        Run before training. Checks: enough resolved trades, data not stale.
        """
        failures: List[str] = []
        max_staleness_hours = self._get_threshold(
            "TRAINING_MAX_STALENESS_HOURS",
            getattr(self.settings, "LEARNING_SCHEDULER_MAX_STALENESS_HOURS", 24),
        )
        min_samples = self._get_threshold(
            "MIN_TRAINING_SAMPLES",
            getattr(self.settings, "MODEL_MIN_TRAINING_SAMPLES", 50),
        )

        freshness = None
        try:
            freshness = await self.db.get_latest_trade_timestamp()
            if freshness is None:
                freshness = await self.db.get_latest_price_timestamp()
            if freshness is None:
                freshness = await self.db.get_latest_sync_completed_at()
        except Exception as e:
            failures.append(f"freshness check failed: {e}")

        if freshness is not None:
            ft = freshness if getattr(freshness, "tzinfo", None) else freshness.replace(tzinfo=timezone.utc)
            staleness_sec = (datetime.now(timezone.utc) - ft).total_seconds()
            if staleness_sec > max_staleness_hours * 3600:
                failures.append(
                    f"data stale: latest {staleness_sec / 3600:.1f}h ago (max {max_staleness_hours}h)"
                )

        err = await self._check_record_count(
            "trades",
            min_samples,
            "market_id IN (SELECT id FROM markets WHERE resolved = TRUE AND resolution IN ('YES', 'NO'))",
        )
        if err:
            # Fallback: check paper trades + prediction log for feedback data
            # If we have enough resolved feedback, don't block training
            feedback_err = await self._check_record_count(
                "paper_trades", min_samples,
                "resolution IS NOT NULL AND resolution IN ('YES', 'NO')"
            )
            pred_feedback_err = await self._check_record_count(
                "prediction_log", min_samples,
                "was_correct IS NOT NULL AND resolution IN ('YES', 'NO')"
            )
            if feedback_err is None or pred_feedback_err is None:
                # Have enough feedback data from paper trades or predictions
                err = None
            if err:
                failures.append(err)

        return GateResult(
            passed=len(failures) == 0,
            failures=failures,
            retriable=bool(failures),
            stale=any("stale" in f.lower() for f in failures),
            summary="; ".join(failures[:3]) if failures else "OK",
        )

    async def check_before_risk(self) -> GateResult:
        """
        Run before risk decisions. Checks: position/market data fresh.
        Priority: price data (WebSocket keeps this current) > trades > sync_log.
        """
        failures: List[str] = []
        max_staleness_hours = self._get_threshold("RISK_MAX_STALENESS_HOURS", 24.0)

        freshness = None
        try:
            # Check price data FIRST — WebSocket streaming keeps market_prices
            # current even when batch ingestion hasn't run recently.
            freshness = await self.db.get_latest_price_timestamp()
            if freshness is None:
                freshness = await self.db.get_latest_trade_timestamp()
            if freshness is None:
                freshness = await self.db.get_latest_sync_completed_at()
        except Exception as e:
            failures.append(f"freshness check failed: {e}")

        if freshness is not None:
            ft = freshness if getattr(freshness, "tzinfo", None) else freshness.replace(tzinfo=timezone.utc)
            staleness_sec = (datetime.now(timezone.utc) - ft).total_seconds()
            if staleness_sec > max_staleness_hours * 3600:
                failures.append(
                    f"data stale for risk: latest {staleness_sec / 3600:.1f}h ago (max {max_staleness_hours}h)"
                )
        elif not failures:
            failures.append("no freshness data (no syncs, trades, or prices)")

        return GateResult(
            passed=len(failures) == 0,
            failures=failures,
            retriable=False,
            stale=bool(failures),
            summary="; ".join(failures[:3]) if failures else "OK",
        )
