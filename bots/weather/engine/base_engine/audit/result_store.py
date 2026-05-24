"""
Persistence layer for audit runs and violations.

Key design decisions:
- violation_hash dedup: (recon_date, violation_hash) prevents duplicates while
  allowing multiple distinct violations of the same type on the same market.
- Trend delta: requires n>=5 completed runs before computing regression flag.
  Cold-start safe.
- All writes use raw SQL via sqlalchemy.text() matching existing database.py patterns.
- S196: auto_close_resolved_violations transitions OPEN rows to RESOLVED when
  today's clean check run no longer detects them (self-resolution). Conservative
  scope: only fires on recon_types that today's run produced AT LEAST ONE
  violation of, so a buggy check returning empty results never mass-closes.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from structlog import get_logger

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult

logger = get_logger(__name__)


def _violation_hash(v: AuditViolation) -> str:
    """SHA256 of type|bot|market|details → 16-char hex discriminator."""
    payload = (
        (v.recon_type or "")
        + "|"
        + (v.bot_name or "")
        + "|"
        + (v.market_id or "")
        + "|"
        + json.dumps(v.details, sort_keys=True, default=str)
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def create_audit_run(session, run_type: str, triggered_by: str = "scheduler") -> int:
    """Insert a new audit_runs row and return run_id."""
    result = await session.execute(
        text(
            "INSERT INTO audit_runs (run_type, triggered_by, started_at) "
            "VALUES (:run_type, :triggered_by, NOW()) "
            "RETURNING run_id"
        ),
        {"run_type": run_type, "triggered_by": triggered_by},
    )
    run_id = result.scalar()
    await session.commit()
    return run_id


async def store_check_results(
    session, run_id: int, results: List[CheckResult], today: Optional[date] = None
) -> int:
    """
    Persist violations from a list of CheckResults into reconciliation_breaks.
    Deduplicates on (recon_date, violation_hash) — allows multiple distinct
    violations of the same type on the same market to all be stored.
    Returns count of rows actually inserted.
    """
    if today is None:
        today = date.today()

    inserted = 0
    for result in results:
        for v in result.violations:
            vh = _violation_hash(v)
            try:
                r = await session.execute(
                    text("""
                        INSERT INTO reconciliation_breaks
                            (recon_date, recon_type, bot_name, market_id,
                             internal_value, external_value, difference,
                             severity, status, details, detected_at,
                             audit_run_id, violation_hash)
                        SELECT
                            :recon_date, :recon_type, :bot_name, :market_id,
                            :internal_value, :external_value, :difference,
                            :severity, 'OPEN', CAST(:details AS jsonb), NOW(),
                            :audit_run_id, :violation_hash
                        WHERE NOT EXISTS (
                            SELECT 1 FROM reconciliation_breaks
                            WHERE recon_date = :recon_date
                              AND violation_hash = :violation_hash
                        )
                    """),
                    {
                        "recon_date": today,
                        "recon_type": v.recon_type,
                        "bot_name": v.bot_name,
                        "market_id": v.market_id,
                        "internal_value": v.internal_value,
                        "external_value": v.external_value,
                        "difference": v.difference,
                        "severity": v.severity,
                        "details": json.dumps(v.details, default=str),
                        "audit_run_id": run_id,
                        "violation_hash": vh,
                    },
                )
                inserted += r.rowcount
            except Exception as e:
                logger.warning("audit_result_store_insert_failed", error=str(e), recon_type=v.recon_type)
    await session.commit()
    return inserted


async def auto_close_resolved_violations(
    session, run_id: int, results: List[CheckResult],
    today: Optional[date] = None,
) -> int:
    """
    Close OPEN reconciliation_breaks rows that today's clean check run has
    superseded or self-resolved.

    Two close rules (both gated by "this recon_type was detected today by a
    successfully-completed check"):

      1. SELF-RESOLVED — OPEN row whose (bot_name, market_id) is NOT in
         today's detected set for its recon_type. The condition no longer
         holds; a subsequent event (or a fix-forward commit) brought the
         underlying invariant back into balance.

      2. SUPERSEDED — OPEN row whose key IS in today's detected set AND
         recon_date < today. The audit creates a new row each day for the
         same (recon_type, bot, market) when the issue persists; the latest
         detection is the canonical OPEN row, earlier snapshots are stale.
         Without this rule, permanently-inflated historical data accumulates
         one OPEN row per day per market — drowning the audit signal.

    Conservative scope:
      - Only fires for recon_types that today's run produced AT LEAST ONE
        violation of from a successfully-completed check. A check that timed
        out, errored, or produced 0 violations of its type does NOT trigger
        auto-close. This mitigates the failure mode where a buggy check
        returning empty results would mass-close legitimate OPEN rows.
      - Multi-type checks are handled per-type independently (e.g., the
        size_invariant check emits both SIZE_INVARIANT and NEGATIVE_SIZE —
        each type's auto-close is gated by today's detection of that type).

    Returns total count of rows transitioned OPEN → RESOLVED across both rules.
    """
    if today is None:
        today = date.today()

    detected_by_type: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
    for result in results:
        # Skip checks that didn't complete cleanly — we don't have ground truth.
        if result.timed_out:
            continue
        if result.summary and result.summary.startswith("error:"):
            continue
        for v in result.violations:
            detected_by_type[v.recon_type].add(
                (v.bot_name or "", v.market_id or "")
            )

    closed_total = 0
    for recon_type, detected_keys in detected_by_type.items():
        if not detected_keys:
            continue

        # Fetch all OPEN rows of this recon_type with their recon_date.
        # Today's detected set is the ground truth for "still active today";
        # we close everything else (self-resolved) plus older same-key rows
        # superseded by today's fresh detection.
        rows = await session.execute(
            text(
                "SELECT break_id, bot_name, market_id, recon_date "
                "FROM reconciliation_breaks "
                "WHERE status = 'OPEN' AND recon_type = :recon_type"
            ),
            {"recon_type": recon_type},
        )
        ids_self_resolved: List[int] = []
        ids_superseded: List[int] = []
        for row in rows.fetchall():
            key = (row[1] or "", row[2] or "")
            row_date = row[3]
            if key not in detected_keys:
                ids_self_resolved.append(int(row[0]))
            elif row_date is not None and row_date < today:
                ids_superseded.append(int(row[0]))

        if ids_self_resolved:
            await session.execute(
                text(
                    "UPDATE reconciliation_breaks "
                    "SET status = 'RESOLVED', "
                    "    resolved_at = NOW(), "
                    "    resolution_note = :note "
                    "WHERE break_id = ANY(:ids) AND status = 'OPEN'"
                ),
                {
                    "ids": ids_self_resolved,
                    "note": (
                        f"S196 auto-close: condition self-resolved "
                        f"(run #{run_id} did not detect {recon_type} "
                        f"for this bot/market)"
                    ),
                },
            )
            closed_total += len(ids_self_resolved)

        if ids_superseded:
            await session.execute(
                text(
                    "UPDATE reconciliation_breaks "
                    "SET status = 'RESOLVED', "
                    "    resolved_at = NOW(), "
                    "    resolution_note = :note "
                    "WHERE break_id = ANY(:ids) AND status = 'OPEN'"
                ),
                {
                    "ids": ids_superseded,
                    "note": (
                        f"S196 auto-close: superseded by run #{run_id} "
                        f"(today's detection of {recon_type} for this "
                        f"bot/market is the canonical OPEN row)"
                    ),
                },
            )
            closed_total += len(ids_superseded)

    if closed_total > 0:
        await session.commit()
    return closed_total


async def _compute_trend_delta(session, check_name: str, today_count: int) -> Dict[str, Any]:
    """
    Compare today's violation count against 7-day rolling average.
    Requires n>=5 completed runs — cold-start safe.
    """
    try:
        result = await session.execute(
            text("""
                SELECT
                    COUNT(*) AS n,
                    AVG((summary->:check_name->>'total')::int) AS avg_7d
                FROM audit_runs
                WHERE status = 'completed'
                  AND started_at >= NOW() - INTERVAL '7 days'
                  AND summary ? :check_name
                  AND (summary->:check_name->>'total') IS NOT NULL
            """),
            {"check_name": check_name},
        )
        row = result.fetchone()
        n = int(row[0]) if row and row[0] else 0
        avg_7d = float(row[1]) if row and row[1] is not None else None

        if n < 5 or avg_7d is None:
            return {"today": today_count, "avg_7d": None, "delta_pct": None,
                    "regression": False, "n": n, "cold_start": True}

        delta_pct = ((today_count - avg_7d) / max(avg_7d, 0.1)) * 100
        regression = today_count > avg_7d * 2 and today_count > avg_7d + 2
        return {
            "today": today_count,
            "avg_7d": round(avg_7d, 2),
            "delta_pct": round(delta_pct, 1),
            "regression": regression,
            "n": n,
            "cold_start": False,
        }
    except Exception as e:
        logger.debug("trend_delta_failed", check_name=check_name, error=str(e))
        return {"today": today_count, "avg_7d": None, "delta_pct": None,
                "regression": False, "n": 0, "cold_start": True}


async def complete_audit_run(
    session, run_id: int, results: List[CheckResult], error_message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Finalise the audit_runs row with counts, per-check trend deltas, and status.
    Returns the summary dict.
    """
    status = "failed" if error_message else "completed"
    checks_run = len(results)
    checks_passed = sum(1 for r in results if r.passed and not r.timed_out)
    checks_failed = sum(1 for r in results if not r.passed or r.timed_out)
    checks_warned = sum(1 for r in results if r.passed and r.warning_count > 0)
    total_breaks = sum(r.violation_count for r in results)

    summary: Dict[str, Any] = {}
    for r in results:
        trend = await _compute_trend_delta(session, r.check_name, r.violation_count)
        entry: Dict[str, Any] = {"total": r.violation_count, **trend}
        if r.timed_out:
            entry["timed_out"] = True
        if r.critical_count:
            entry["critical"] = r.critical_count
        if r.warning_count:
            entry["warning"] = r.warning_count
        summary[r.check_name] = entry

    await session.execute(
        text("""
            UPDATE audit_runs SET
                completed_at  = NOW(),
                status        = :status,
                checks_run    = :checks_run,
                checks_passed = :checks_passed,
                checks_failed = :checks_failed,
                checks_warned = :checks_warned,
                total_breaks  = :total_breaks,
                summary       = CAST(:summary AS jsonb),
                error_message = :error_message
            WHERE run_id = :run_id
        """),
        {
            "run_id": run_id,
            "status": status,
            "checks_run": checks_run,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "checks_warned": checks_warned,
            "total_breaks": total_breaks,
            "summary": json.dumps(summary, default=str),
            "error_message": error_message,
        },
    )
    await session.commit()
    return summary
