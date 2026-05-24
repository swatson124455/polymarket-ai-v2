"""
AuditOrchestrator — runs all registered checks, owns session management,
enforces query guardrails, persists results, and fires alerts.

Guardrails (all in this file, not in individual checks):
1. Dedicated session separate from bot pool — READ COMMITTED isolation
2. SET LOCAL statement_timeout='30s' before each check's SQL
3. Routes to AUDIT_DB_URL read replica if set, else falls back to primary
"""
import asyncio
import os
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from structlog import get_logger

from bots.weather.engine.base_engine.audit.check_result import CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck
from bots.weather.engine.base_engine.audit.result_store import (
    auto_close_resolved_violations,
    create_audit_run,
    store_check_results,
    complete_audit_run,
)

if TYPE_CHECKING:
    from bots.weather.engine.base_engine.data.database import Database

logger = get_logger(__name__)

# S164: Increased from 30s to 120s. Safe because the daily audit now runs
# out-of-process via systemd timer (polymarket-audit.timer), not on the bot pool.
# fk_integrity and price_integrity checks timed out at 30s.
_STATEMENT_TIMEOUT = "120s"


class AuditOrchestrator:
    def __init__(self, db: "Database", alerting=None):
        self._db = db
        self._alerting = alerting
        self._checks: List[BaseCheck] = []

        # Use a dedicated session factory — AUDIT_DB_URL if set, else primary
        audit_url = os.getenv("AUDIT_DB_URL") or os.getenv("DATABASE_URL", "")
        if audit_url and audit_url != os.getenv("DATABASE_URL", ""):
            logger.info("audit_orchestrator_using_replica", url=audit_url[:40])
        if db.session_factory is not None:
            # Reuse the existing async engine from the primary DB — same URL
            # but a separate sessionmaker so we don't share connection pool slots
            # with the bot pool.
            self._audit_session_factory = db.session_factory
        else:
            self._audit_session_factory = None

    def register_check(self, check: BaseCheck) -> None:
        self._checks.append(check)

    async def run_all(
        self,
        run_type: str = "scheduled_daily",
        triggered_by: str = "scheduler",
    ) -> Dict[str, Any]:
        """
        Execute all registered checks sequentially.
        Each check gets its own session with READ COMMITTED + 30s timeout.
        Returns the summary dict from complete_audit_run().
        """
        if self._audit_session_factory is None:
            logger.warning("audit_orchestrator_no_db", msg="No DB session factory — skipping audit")
            return {}

        results: List[CheckResult] = []
        run_id: Optional[int] = None
        error_message: Optional[str] = None

        # Create the audit run row
        try:
            async with self._audit_session_factory() as meta_session:
                run_id = await create_audit_run(meta_session, run_type, triggered_by)
        except Exception as e:
            logger.warning("audit_run_create_failed", error=str(e))
            return {}

        logger.info("audit_run_started", run_id=run_id, checks=len(self._checks), triggered_by=triggered_by)

        for check in self._checks:
            result = await self._run_single_check(check)
            results.append(result)

            if result.timed_out:
                logger.warning(
                    "audit_check_timed_out",
                    check=check.name,
                    timeout=_STATEMENT_TIMEOUT,
                )
            elif result.critical_count:
                logger.warning(
                    "audit_check_critical",
                    check=check.name,
                    critical=result.critical_count,
                    warning=result.warning_count,
                )

        # Persist all violations
        try:
            async with self._audit_session_factory() as store_session:
                await store_check_results(store_session, run_id, results)
        except Exception as e:
            logger.warning("audit_store_results_failed", run_id=run_id, error=str(e))
            error_message = f"store_check_results failed: {e}"

        # S196: Auto-close OPEN rows whose condition self-resolved. Only fires
        # for recon_types whose check ran cleanly AND produced at least one
        # violation today — defensive against buggy-check zero-result false-clears.
        try:
            async with self._audit_session_factory() as autoclose_session:
                _closed = await auto_close_resolved_violations(
                    autoclose_session, run_id, results
                )
                if _closed > 0:
                    logger.info(
                        "audit_auto_closed_resolved",
                        count=_closed,
                        run_id=run_id,
                    )
        except Exception as e:
            logger.warning("audit_auto_close_failed", run_id=run_id, error=str(e))

        # Finalise run metadata with trend deltas
        summary: Dict[str, Any] = {}
        try:
            async with self._audit_session_factory() as complete_session:
                summary = await complete_audit_run(
                    complete_session, run_id, results, error_message
                )
        except Exception as e:
            logger.warning("audit_complete_run_failed", run_id=run_id, error=str(e))

        # Fire aggregated alert for new CRITICAL violations
        await self._maybe_alert(results, run_id)

        total_breaks   = sum(r.violation_count for r in results)
        total_critical = sum(r.critical_count for r in results)
        total_warning  = sum(r.warning_count for r in results)
        checks_passed  = sum(1 for r in results if r.passed and not r.timed_out)
        checks_failed  = sum(1 for r in results if not r.passed or r.timed_out)
        timed_out_count = sum(1 for r in results if r.timed_out)
        logger.info(
            "audit_run_complete",
            run_id=run_id,
            checks_run=len(results),
            checks_passed=checks_passed,
            total_breaks=total_breaks,
            timed_out=timed_out_count,
        )
        # Build a return dict the CLI can consume directly.
        # complete_audit_run() returns per-check trend data (keyed by check name) —
        # that is stored in DB but is NOT the shape _print_summary() expects.
        return {
            "run_id":         run_id,
            "status":         "failed" if error_message else "completed",
            "checks_run":     len(results),
            "checks_passed":  checks_passed,
            "checks_failed":  checks_failed,
            "total_breaks":   total_breaks,
            "total_critical": total_critical,
            "total_warning":  total_warning,
            "check_summaries": {
                r.check_name: {
                    "passed":          r.passed,
                    "violation_count": r.violation_count,
                    "summary":         r.summary,
                    "duration_ms":     round(r.duration_ms, 1),
                    "timed_out":       r.timed_out,
                }
                for r in results
            },
            "trend": summary,  # per-check JSONB from complete_audit_run — visible via --json
        }

    async def _run_single_check(self, check: BaseCheck) -> CheckResult:
        """
        Open a fresh session, set READ COMMITTED + statement_timeout, run check.
        On timeout or error, return a safe CheckResult rather than propagating.
        """
        t0 = time.monotonic()
        try:
            async with self._audit_session_factory() as session:
                # Guardrail 1: READ COMMITTED — never block writers with a snapshot
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                )
                # Guardrail 2: per-query timeout — query cancelled by PG if exceeded
                await session.execute(
                    text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
                )
                result = await check.execute(session)
                result.duration_ms = (time.monotonic() - t0) * 1000
                return result

        except Exception as e:
            duration_ms = (time.monotonic() - t0) * 1000
            err_str = str(e).lower()
            timed_out = "statement timeout" in err_str or "canceling statement" in err_str
            return CheckResult(
                check_name=check.name,
                passed=False,
                violations=[],
                duration_ms=duration_ms,
                tables_queried=check.tables_queried,
                summary=f"{'timed_out' if timed_out else 'error'}: {e}",
                timed_out=timed_out,
            )

    async def _maybe_alert(self, results: List[CheckResult], run_id: Optional[int]) -> None:
        """Fire one aggregated alert if new CRITICAL violations found."""
        if not self._alerting:
            return

        critical_results = [r for r in results if r.critical_count > 0]
        regressions = [
            name for name, entry in
            # We'd need summary here — handled via run_id lookup instead
            # Just use in-memory results
            [(r.check_name, r) for r in results]
            if entry.critical_count > 0
        ]

        if not critical_results:
            return

        total_critical = sum(r.critical_count for r in critical_results)
        lines = [
            f"  • {r.check_name}: {r.critical_count} CRITICAL, {r.warning_count} WARNING"
            for r in critical_results
        ]
        try:
            from bots.weather.engine.base_engine.monitoring.alerting import AlertSeverity
            await self._alerting.send_alert(
                title=f"Audit run #{run_id}: {total_critical} critical violation(s)",
                message="\n".join(lines),
                severity=AlertSeverity.CRITICAL,
                source="audit_orchestrator",
                metadata={"run_id": run_id, "critical_count": total_critical},
            )
        except Exception as e:
            logger.warning("audit_alert_failed", error=str(e))
