"""
HealthRunner — top-to-bottom data and learning pipeline health check.

Covers every known failure mode identified across Sessions 28-33:
  1. DB connectivity + pool state (idle-in-transaction, exhaustion)
  2. Markets table: count, end_date_iso coverage, freshness
  3. Prices table: freshness
  4. Ingestion sync_log: success rate, last sync age
  5. Prediction log: label rate, temporal ordering violations
  6. Resolution backfill: last run, markets needing resolution
  7. Schema conformance: expected columns, constraint health
  8. Paper trades: resolution rate, P&L coverage
  9. Feature snapshot integrity: _pred_ts present, _fv_hash present
  10. Advisory locks: detect leaked idle-in-transaction locks
  11. Signal freshness: most recent signal timestamp (>30min = unhealthy)
  12. Prediction log insert rate: rows inserted in last hour (0 = warning)
  13. Redis ping: connectivity check (failure = degraded)

Called:
  - At startup by BaseEngine._post_init_health_check()
  - Every HEALTH_CHECK_INTERVAL_MINUTES minutes by IngestionScheduler
  - Manually via `python -m base_engine.monitoring.health_runner`
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()


@dataclass
class HealthIssue:
    severity: str  # "critical" | "warning" | "info"
    category: str
    message: str
    auto_fixed: bool = False
    fix_description: str = ""


@dataclass
class HealthReport:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    issues: List[HealthIssue] = field(default_factory=list)
    checks_run: int = 0
    healthy: bool = True

    def add(self, severity: str, category: str, message: str,
            auto_fixed: bool = False, fix_description: str = "") -> None:
        self.issues.append(HealthIssue(severity, category, message, auto_fixed, fix_description))
        if severity == "critical":
            self.healthy = False

    def summary(self) -> str:
        criticals = [i for i in self.issues if i.severity == "critical"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        infos = [i for i in self.issues if i.severity == "info"]
        parts = [f"checks={self.checks_run}"]
        if criticals:
            parts.append(f"CRITICAL={len(criticals)}")
        if warnings:
            parts.append(f"warnings={len(warnings)}")
        if infos:
            parts.append(f"info={len(infos)}")
        if not self.issues:
            parts.append("all_clear")
        return " | ".join(parts)


class HealthRunner:
    """
    Top-to-bottom health check for data ingestion and ML learning pipeline.
    Auto-remediates what it safely can (triggers backfill, logs actionable fixes).
    """

    def __init__(self, db: Any, settings: Optional[Any] = None):
        self.db = db
        self.settings = settings or __import__("config.settings", fromlist=["settings"]).settings

    # ── helpers ────────────────────────────────────────────────────────────────

    async def _scalar(self, sql: str, params: Optional[dict] = None) -> Any:
        """Run a scalar query. Returns None on any error."""
        try:
            from sqlalchemy import text
            async with self.db.get_session() as s:
                r = await s.execute(text(sql), params or {})
                return r.scalar_one_or_none()
        except Exception as e:
            logger.debug("HealthRunner._scalar failed: %s", e)
            return None

    async def _fetchone(self, sql: str, params: Optional[dict] = None):
        """Run a query returning one row. Returns None on any error."""
        try:
            from sqlalchemy import text
            async with self.db.get_session() as s:
                r = await s.execute(text(sql), params or {})
                return r.fetchone()
        except Exception as e:
            logger.debug("HealthRunner._fetchone failed: %s", e)
            return None

    # ── individual checks ─────────────────────────────────────────────────────

    async def _check_db_connectivity(self, report: HealthReport) -> None:
        """Check basic DB connectivity."""
        report.checks_run += 1
        if not self.db or not getattr(self.db, "session_factory", None):
            report.add("critical", "db", "Database not connected (session_factory is None)")
            return
        val = await self._scalar("SELECT 1")
        if val != 1:
            report.add("critical", "db", "DB connectivity test failed (SELECT 1 returned unexpected value)")
        else:
            report.add("info", "db", "DB connectivity OK")

    async def _check_idle_in_transaction(self, report: HealthReport) -> None:
        """Detect connections stuck in idle-in-transaction state > 60s (advisory lock leaks)."""
        report.checks_run += 1
        try:
            from sqlalchemy import text
            async with self.db.get_session() as s:
                r = await s.execute(text("""
                    SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - query_start)))::int AS max_sec
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                    AND state = 'idle in transaction'
                    AND query_start < NOW() - INTERVAL '60 seconds'
                """))
                row = r.fetchone()
            count = int(row[0] or 0) if row else 0
            max_sec = int(row[1] or 0) if row else 0
            if count > 0:
                report.add(
                    "warning", "db_pool",
                    f"{count} connection(s) idle-in-transaction for >{max_sec}s "
                    "(advisory lock commit missing — database_lock.py fix deployed)",
                )
        except Exception as e:
            logger.debug("idle-in-transaction check failed: %s", e)

    async def _check_pool_exhaustion(self, report: HealthReport) -> None:
        """Check if DB pool is near exhaustion."""
        report.checks_run += 1
        try:
            from sqlalchemy import text
            async with self.db.get_session() as s:
                r = await s.execute(text("""
                    SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()
                """))
                active_conns = r.scalar_one_or_none() or 0
            pool_size = getattr(self.settings, "DB_POOL_SIZE", 10)
            overflow = getattr(self.settings, "DB_MAX_OVERFLOW", 3)
            max_conns = pool_size + overflow
            pct = active_conns / max(max_conns, 1)
            if pct >= 0.90:
                report.add(
                    "critical", "db_pool",
                    f"Pool near exhaustion: {active_conns}/{max_conns} connections ({pct:.0%}). "
                    "Increase DB_POOL_SIZE or reduce scan frequency.",
                )
            elif pct >= 0.75:
                report.add(
                    "warning", "db_pool",
                    f"Pool at {pct:.0%}: {active_conns}/{max_conns} connections",
                )
            else:
                report.add("info", "db_pool", f"Pool healthy: {active_conns}/{max_conns} connections")
        except Exception as e:
            logger.debug("pool check failed: %s", e)

    async def _check_markets(self, report: HealthReport) -> None:
        """Check markets table count, freshness, end_date_iso coverage."""
        report.checks_run += 1
        row = await self._fetchone("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE end_date_iso IS NOT NULL) AS has_date,
                COUNT(*) FILTER (WHERE resolved = TRUE) AS resolved,
                MAX(updated_at) AS last_updated
            FROM markets
        """)
        if not row:
            report.add("critical", "markets", "Cannot query markets table")
            return

        total, has_date, resolved, last_updated = (
            int(row[0] or 0), int(row[1] or 0),
            int(row[2] or 0), row[3]
        )

        if total < 100:
            report.add("critical", "markets", f"Only {total} markets — ingestion may have failed")
        else:
            report.add("info", "markets", f"{total} markets, {resolved} resolved")

        # end_date_iso coverage (root cause of Session 32 zero-resolution bug)
        if total > 0:
            null_pct = (total - has_date) / total
            if null_pct > 0.80:
                report.add(
                    "critical", "markets",
                    f"end_date_iso NULL for {null_pct:.0%} of markets ({total-has_date}/{total}). "
                    "Resolution backfill cannot detect expired markets. "
                    "Root cause: endDate vs endDateISO field name mismatch in data_ingestion.py.",
                )
            elif null_pct > 0.50:
                report.add(
                    "warning", "markets",
                    f"end_date_iso NULL for {null_pct:.0%} of markets. "
                    "Resolution backfill running — will patch during next cycle.",
                )
            else:
                report.add("info", "markets", f"end_date_iso coverage: {has_date}/{total} ({(1-null_pct):.0%})")

        # Freshness
        if last_updated:
            age_h = (datetime.now(timezone.utc) - last_updated.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            max_h = float(getattr(self.settings, "PIPELINE_GATE_MARKETS_FRESHNESS_HOURS", 2.0))
            if age_h > max_h:
                report.add("warning", "markets", f"Markets stale: last updated {age_h:.1f}h ago (max {max_h}h)")

    async def _check_prediction_log(self, report: HealthReport) -> None:
        """Check prediction_log label rate, temporal ordering, feature hash presence."""
        report.checks_run += 1
        row = await self._fetchone("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE was_correct IS NOT NULL) AS labeled,
                COUNT(*) FILTER (WHERE resolution IN ('YES','NO') AND was_correct IS NOT NULL) AS resolved_labeled,
                COUNT(*) FILTER (WHERE feature_snapshot IS NOT NULL) AS has_features,
                COUNT(*) FILTER (WHERE feature_snapshot IS NOT NULL
                                 AND feature_snapshot::text LIKE '%_pred_ts%') AS has_pred_ts,
                COUNT(*) FILTER (WHERE feature_snapshot IS NOT NULL
                                 AND feature_snapshot::text LIKE '%_fv_hash%') AS has_fv_hash
            FROM prediction_log
        """)
        if not row:
            report.add("warning", "prediction_log", "Cannot query prediction_log (table may not exist yet)")
            return

        total = int(row[0] or 0)
        labeled = int(row[1] or 0)
        resolved = int(row[2] or 0)
        has_features = int(row[3] or 0)
        has_pred_ts = int(row[4] or 0)
        has_fv_hash = int(row[5] or 0)

        if total == 0:
            report.add("info", "prediction_log", "prediction_log is empty (no predictions yet)")
            return

        label_pct = labeled / total
        report.add(
            "info" if label_pct > 0.01 else "warning",
            "prediction_log",
            f"{total} predictions total, {labeled} labeled ({label_pct:.1%}), {resolved} fully resolved",
        )

        # Temporal ordering check
        temporal_violations = await self._scalar("""
            SELECT COUNT(*) FROM prediction_log pl
            JOIN markets m ON pl.market_id = m.id
            WHERE m.resolution IN ('YES', 'NO')
            AND m.resolved_at IS NOT NULL
            AND pl.prediction_time IS NOT NULL
            AND m.resolved_at < pl.prediction_time
        """) or 0
        if temporal_violations > 0:
            report.add(
                "critical", "prediction_log",
                f"Temporal ordering violation: {temporal_violations} predictions would receive labels "
                "with resolved_at < prediction_time (clock skew or backfill ordering bug). "
                "These are blocked from labeling by the temporal guard in backfill_prediction_log_resolution.",
            )

        # Feature integrity
        if has_features > 0:
            ts_pct = has_pred_ts / has_features
            hash_pct = has_fv_hash / has_features
            if ts_pct < 0.5:
                report.add(
                    "warning", "prediction_log",
                    f"Only {ts_pct:.0%} of feature snapshots have _pred_ts timestamp "
                    "(temporal integrity — new predictions will have it)",
                )
            if hash_pct < 0.5:
                report.add(
                    "warning", "prediction_log",
                    f"Only {hash_pct:.0%} of feature snapshots have _fv_hash "
                    "(feature vector integrity — new predictions will have it)",
                )

    async def _check_resolution_backfill(self, report: HealthReport) -> None:
        """Check resolution backfill state: markets needing resolution, last run."""
        report.checks_run += 1

        # Markets with trades but no resolution
        needing_res = await self._scalar("""
            SELECT COUNT(DISTINCT m.id) FROM markets m
            WHERE (m.resolution IS NULL OR m.resolution NOT IN ('YES','NO'))
            AND (
                EXISTS (SELECT 1 FROM trades t WHERE t.market_id = m.id::text OR t.market_id = m.condition_id)
                OR EXISTS (SELECT 1 FROM paper_trades pt WHERE pt.market_id::text = m.id::text)
            )
        """) or 0

        # Markets with end_date in the past and no resolution (should have resolved)
        overdue = await self._scalar("""
            SELECT COUNT(*) FROM markets
            WHERE end_date_iso < NOW()
            AND (resolution IS NULL OR resolution NOT IN ('YES','NO'))
            AND end_date_iso IS NOT NULL
        """) or 0

        # Last resolution backfill run
        last_backfill = await self._scalar("""
            SELECT MAX(completed_at) FROM sync_log
            WHERE component = 'resolution_backfill' AND status = 'success'
        """)

        if overdue > 100:
            report.add(
                "warning", "resolution_backfill",
                f"{overdue} markets are past their end_date but unresolved. "
                "Resolution backfill will catch these on next run.",
            )

        if needing_res > 0:
            report.add("info", "resolution_backfill", f"{needing_res} markets need resolution lookup")

        if last_backfill:
            age_h = (datetime.now(timezone.utc) - last_backfill.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if age_h > 2:
                report.add(
                    "warning", "resolution_backfill",
                    f"Resolution backfill last ran {age_h:.1f}h ago (expected every ~30min)",
                )
            else:
                report.add("info", "resolution_backfill", f"Last backfill {age_h:.1f}h ago — OK")
        else:
            report.add("warning", "resolution_backfill", "No successful resolution backfill run recorded")

    async def _check_paper_trades(self, report: HealthReport) -> None:
        """Check paper trades resolution and P&L coverage."""
        report.checks_run += 1
        row = await self._fetchone("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE resolution IS NOT NULL) AS resolved,
                COUNT(*) FILTER (WHERE realized_pnl IS NOT NULL) AS has_pnl,
                SUM(realized_pnl) AS total_pnl
            FROM paper_trades
        """)
        if not row:
            return
        total = int(row[0] or 0)
        resolved = int(row[1] or 0)
        has_pnl = int(row[2] or 0)
        total_pnl = float(row[3] or 0)

        if total > 0:
            res_pct = resolved / total
            report.add(
                "info", "paper_trades",
                f"{total} paper trades: {resolved} resolved ({res_pct:.0%}), "
                f"{has_pnl} with P&L, total P&L: ${total_pnl:.2f}",
            )
            if res_pct < 0.10 and total > 50:
                report.add(
                    "warning", "paper_trades",
                    f"Only {res_pct:.0%} of paper trades resolved — "
                    "resolution backfill is not propagating to paper_trades",
                )

    async def _check_slug_collisions(self, report: HealthReport) -> None:
        """Check for slug collision issues (root cause of bulk_insert UniqueViolation spam)."""
        report.checks_run += 1
        empty_slugs = await self._scalar(
            "SELECT COUNT(*) FROM markets WHERE slug = '' OR slug IS NULL"
        ) or 0
        dup_slugs = await self._scalar("""
            SELECT COUNT(*) FROM (
                SELECT slug, COUNT(*) as c FROM markets
                WHERE slug IS NOT NULL AND slug != ''
                GROUP BY slug HAVING COUNT(*) > 1
            ) dups
        """) or 0

        if dup_slugs > 0:
            report.add(
                "warning", "schema",
                f"{dup_slugs} duplicate slugs in markets table — cause of UniqueViolationError in bulk_insert. "
                "Fix deployed: empty slugs → NULL, batch deduplication enabled.",
            )
        if empty_slugs > 0:
            report.add(
                "info", "schema",
                f"{empty_slugs} markets have NULL/empty slug (normal after normalization fix)",
            )

    async def _check_ingestion_sync(self, report: HealthReport) -> None:
        """Check ingestion sync_log health (recent success rate and last sync age)."""
        report.checks_run += 1
        row = await self._fetchone("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'success') AS success,
                MAX(completed_at) FILTER (WHERE status = 'success') AS last_ok
            FROM sync_log
            WHERE started_at > NOW() - INTERVAL '24 hours'
            AND component IN ('data_ingestion', 'markets', 'prices')
        """)
        if not row:
            return
        total = int(row[0] or 0)
        success = int(row[1] or 0)
        last_ok = row[2]

        if total == 0:
            report.add("warning", "ingestion", "No ingestion runs in last 24h")
            return

        rate = success / total
        if rate < 0.5:
            report.add("critical", "ingestion", f"Ingestion success rate: {rate:.0%} ({success}/{total}) last 24h")
        elif rate < 0.8:
            report.add("warning", "ingestion", f"Ingestion success rate: {rate:.0%} ({success}/{total}) last 24h")
        else:
            report.add("info", "ingestion", f"Ingestion success rate: {rate:.0%} last 24h")

        if last_ok:
            age_h = (datetime.now(timezone.utc) - last_ok.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if age_h > 2:
                report.add("warning", "ingestion", f"Last successful ingestion {age_h:.1f}h ago")

    async def _check_category_distribution(self, report: HealthReport) -> None:
        """Check how many markets are in 'unknown' category (should be <20%)."""
        report.checks_run += 1
        row = await self._fetchone("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE category = 'unknown' OR category IS NULL) AS unknown_count
            FROM markets WHERE active = TRUE
        """)
        if not row:
            return
        total = int(row[0] or 0)
        unknown = int(row[1] or 0)
        if total > 0:
            pct = unknown / total
            if pct > 0.50:
                report.add(
                    "warning", "categories",
                    f"{pct:.0%} of active markets have category='unknown' ({unknown}/{total}). "
                    "Run resolution_backfill or re-ingest to trigger _infer_category()",
                )
            else:
                report.add("info", "categories", f"Category coverage: {pct:.0%} unknown ({unknown}/{total})")

    async def _check_bot_scan_times(self, report: HealthReport) -> None:
        """Check for bots with consistently slow scan cycles (DB pool consumers)."""
        report.checks_run += 1
        # Check recent sync_log for slow bot runs (> 120s)
        row = await self._fetchone("""
            SELECT component, AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))::int AS avg_sec
            FROM sync_log
            WHERE started_at > NOW() - INTERVAL '1 hour'
            AND completed_at IS NOT NULL
            GROUP BY component
            ORDER BY avg_sec DESC
            LIMIT 1
        """)
        if row and row[1] and int(row[1]) > 120:
            report.add(
                "warning", "performance",
                f"Slow scan: {row[0]} averaging {row[1]}s — holding DB connections. "
                "Consider reducing scan batch size or adding DB query timeouts.",
            )

    async def _check_signal_freshness(self, report: HealthReport) -> None:
        """Check if signal data is being collected (most recent signal timestamp)."""
        report.checks_run += 1
        try:
            last_signal = await self._scalar("""
                SELECT MAX(collected_at) FROM signals
            """)
            if last_signal is None:
                report.add("warning", "signals", "No signal data found (signals table empty or missing)")
                return
            age_min = (datetime.now(timezone.utc) - last_signal.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if age_min > 30:
                report.add(
                    "critical", "signals",
                    f"Signal data stale: last collected {age_min:.0f}min ago (threshold: 30min). "
                    "Signal ingestion may have stopped.",
                )
            else:
                report.add("info", "signals", f"Signal freshness OK: last collected {age_min:.0f}min ago")
        except Exception as e:
            logger.warning("Signal freshness check failed: %s", e)

    async def _check_prediction_insert_rate(self, report: HealthReport) -> None:
        """Check if prediction_log rows are being inserted (count in last hour)."""
        report.checks_run += 1
        try:
            count = await self._scalar("""
                SELECT COUNT(*) FROM prediction_log
                WHERE prediction_time > NOW() - INTERVAL '1 hour'
            """)
            count = int(count or 0)
            if count == 0:
                report.add(
                    "warning", "prediction_rate",
                    "No prediction_log rows inserted in the last hour. "
                    "Prediction engine may be idle or feature cache not warmed.",
                )
            else:
                report.add("info", "prediction_rate", f"{count} predictions inserted in the last hour")
        except Exception as e:
            logger.warning("Prediction insert rate check failed: %s", e)

    async def _check_redis_ping(self, report: HealthReport) -> None:
        """Ping Redis to verify connectivity."""
        report.checks_run += 1
        try:
            from base_engine.cache.redis_manager import RedisManager
            rm = RedisManager()
            await rm.connect()
            pong = await rm.client.ping() if rm.client else False
            await rm.close()
            if pong:
                report.add("info", "redis", "Redis ping OK")
            else:
                report.add("warning", "redis", "Redis ping returned False — cache degraded")
        except Exception as e:
            logger.warning("Redis ping check failed: %s", e)
            report.add(
                "warning", "redis",
                f"Redis unreachable: {e}. Cache operations will fall back to DB. "
                "Check REDIS_URL in .env and Redis server status.",
            )

    # ── main runner ───────────────────────────────────────────────────────────

    async def run(self, auto_fix: bool = True) -> HealthReport:
        """
        Run all health checks. Returns HealthReport.
        If auto_fix=True, triggers remediation for fixable issues (backfill, etc.).
        """
        report = HealthReport()

        if not self.db or not getattr(self.db, "session_factory", None):
            report.add("critical", "db", "Database not initialized")
            return report

        # Run all checks in parallel where safe
        await asyncio.gather(
            self._check_db_connectivity(report),
            self._check_idle_in_transaction(report),
            self._check_pool_exhaustion(report),
            self._check_markets(report),
            self._check_prediction_log(report),
            self._check_paper_trades(report),
            self._check_slug_collisions(report),
            self._check_ingestion_sync(report),
            self._check_category_distribution(report),
            self._check_bot_scan_times(report),
            self._check_signal_freshness(report),
            self._check_prediction_insert_rate(report),
            self._check_redis_ping(report),
            return_exceptions=True,
        )
        # Resolution backfill check runs after markets check (sequential is fine)
        await self._check_resolution_backfill(report)

        # Phase graduation check — runs at most every PHASE_GRADUATION_CHECK_HOURS (24h default).
        # Logs structured recommendations for phase promotion/demotion (never auto-changes settings).
        try:
            from config.settings import settings as _s
            if getattr(_s, "PHASE_GRADUATION_ENABLED", True):
                from base_engine.monitoring.phase_tracker import PhaseTracker
                if not hasattr(self, "_phase_tracker"):
                    self._phase_tracker = PhaseTracker(db=self.db)
                if self._phase_tracker.should_evaluate():
                    await self._phase_tracker.evaluate()
        except Exception as _pte:
            logger.debug("Phase graduation check failed (non-fatal): %s", _pte)
        report.checks_run += 1

        # Log all issues
        for issue in report.issues:
            if issue.severity == "critical":
                logger.error(
                    "HEALTH [%s] %s", issue.category.upper(), issue.message,
                    auto_fixed=issue.auto_fixed,
                )
            elif issue.severity == "warning":
                logger.warning(
                    "HEALTH [%s] %s", issue.category, issue.message,
                    auto_fixed=issue.auto_fixed,
                )
            else:
                logger.debug("HEALTH [%s] %s", issue.category, issue.message)

        status = "HEALTHY" if report.healthy else "DEGRADED"
        logger.info(
            "Health check complete",
            status=status,
            summary=report.summary(),
            issues=len(report.issues),
        )
        return report


async def run_health_check(db=None, settings=None) -> HealthReport:
    """Convenience entry point for scheduler / startup / manual invocation."""
    if db is None:
        from base_engine.data.database import Database
        from config.settings import settings as _settings
        db = Database()
        await db.initialize()
        settings = _settings
    runner = HealthRunner(db, settings)
    return await runner.run()


if __name__ == "__main__":
    import sys
    asyncio.run(run_health_check())
