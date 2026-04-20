#!/usr/bin/env python3
"""
Data integrity audit CLI.

Usage examples:
  python scripts/run_audit.py                        # run all checks (24h lookback)
  python scripts/run_audit.py --check size_invariant # single check
  python scripts/run_audit.py --bot MirrorBot        # filter output to one bot
  python scripts/run_audit.py --hours 48             # 48-hour lookback
  python scripts/run_audit.py --json                 # machine-readable JSON output
  python scripts/run_audit.py --verbose              # per-violation detail
  python scripts/run_audit.py --list-open            # show all open unacknowledged violations
  python scripts/run_audit.py --ack 42 --reason "polymarket data issue"  # acknowledge a break

Exit codes:
  0 = all checks passed (clean)
  1 = warnings only
  2 = critical violations found
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# S182: structured log emission for audit_run_complete so Phase 5 sentinel
# can detect script crashes that SuccessExitStatus=1 2 would otherwise mask.
import structlog
_logger = structlog.get_logger()

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def _run_checks(
    check_name: Optional[str],
    bot_filter: Optional[str],
    hours: int,
    output_json: bool,
    verbose: bool,
    triggered_by: str = "unlabeled",
) -> int:
    """Run checks and return exit code (0=clean, 1=warning, 2=critical)."""
    from base_engine.data.database import Database
    from base_engine.audit.factory import build_audit_orchestrator

    db = Database()
    await db.init()

    try:
        orchestrator = build_audit_orchestrator(db=db)

        # If single check requested, filter to just that one
        if check_name:
            orchestrator._checks = [
                c for c in orchestrator._checks if c.name == check_name
            ]
            if not orchestrator._checks:
                print(f"ERROR: No check named '{check_name}'. Available checks:")
                from base_engine.audit.factory import build_audit_orchestrator as _bao
                all_orch = _bao(db=db)
                for c in all_orch._checks:
                    print(f"  {c.name}")
                await db.close()
                return 2

        # S183: run_type and triggered_by were hardcoded "cli" prior to this
        # commit, making systemd-timer invocations indistinguishable from
        # manual CLI runs in audit_runs. Default is "unlabeled" (not "cli")
        # so any future invocation that forgets --triggered-by is visibly
        # unlabeled in the data, not silently misclassified as CLI.
        # S183 Hotfix: audit_runs.triggered_by has a CHECK constraint on
        # {scheduler, cli, health_check, post_resolution, manual}. The
        # free-form flag value (e.g. "scheduled_daily", "unlabeled") would
        # violate it. Map invocation kind → source type; flag value remains
        # on run_type (unconstrained) for sentinel heartbeat detection.
        _KIND_TO_SOURCE = {
            "scheduled_daily": "scheduler",
            "cli": "cli",
            "manual": "manual",
            "health_check": "health_check",
            "post_resolution": "post_resolution",
            "unlabeled": "cli",
        }
        summary = await orchestrator.run_all(
            run_type=triggered_by,
            triggered_by=_KIND_TO_SOURCE[triggered_by],
        )

        # Collect results from the orchestrator's last run
        results = orchestrator._checks  # already ran — re-query from summary
        # summary already has the structured data; emit it
        total_critical = summary.get("total_critical", 0) if summary else 0
        total_warning  = summary.get("total_warning", 0) if summary else 0
        total_breaks   = summary.get("total_breaks", 0) if summary else 0

        if output_json:
            print(json.dumps(summary, indent=2, default=str))
        else:
            _print_summary(summary, bot_filter, verbose)

        if total_critical > 0:
            rc = 2
            status_str = "critical"
        elif total_warning > 0 or total_breaks > 0:
            rc = 1
            status_str = "warning"
        else:
            rc = 0
            status_str = "pass"

        # S182: emit a completion heartbeat so the Phase 5 sentinel can detect
        # crashes that SuccessExitStatus=1 2 would mask. Landed alongside the
        # unit-file SuccessExitStatus change. Keep this as the final observable
        # action before returning — if it doesn't appear, the script didn't
        # reach here cleanly.
        _logger.info(
            "audit_run_complete",
            status=status_str,
            findings_count=total_breaks,
            critical_count=total_critical,
            warning_count=total_warning,
            exit_code=rc,
            run_id=summary.get("run_id") if summary else None,
        )
        return rc

    finally:
        await db.close()


def _print_summary(summary: dict, bot_filter: Optional[str], verbose: bool) -> None:
    if not summary:
        print("No audit results returned.")
        return

    run_id       = summary.get("run_id")
    status       = summary.get("status", "unknown")
    total_breaks = summary.get("total_breaks", 0)
    checks_run   = summary.get("checks_run", 0)
    checks_passed = summary.get("checks_passed", 0)
    checks_failed = summary.get("checks_failed", 0)

    print(f"\n=== Audit Run #{run_id} — {status.upper()} ===")
    print(f"Checks: {checks_run} run, {checks_passed} passed, {checks_failed} failed")
    print(f"Total violations: {total_breaks}")

    check_summaries = summary.get("check_summaries", {})
    if check_summaries:
        print("\nPer-check results:")
        for check_name, data in check_summaries.items():
            count    = data.get("violation_count", 0)
            passed   = data.get("passed", count == 0)
            status_s = "PASS" if passed else "FAIL"
            dur      = data.get("duration_ms", 0)
            print(f"  [{status_s}] {check_name}: {data.get('summary', '')} ({dur:.0f}ms)")


async def _list_open() -> int:
    """Print all current OPEN unacknowledged violations."""
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            rows = await session.execute(text("""
                SELECT break_id, recon_type, bot_name, market_id, severity,
                       details, recon_date, created_at
                FROM reconciliation_breaks
                WHERE status = 'OPEN'
                ORDER BY severity DESC, created_at DESC
                LIMIT 200
            """))
            results = rows.fetchall()

        if not results:
            print("No OPEN violations found.")
            return 0

        print(f"\n=== {len(results)} OPEN Violation(s) ===")
        for row in results:
            break_id, recon_type, bot_name, market_id, severity, details, recon_date, created_at = row
            print(
                f"  #{break_id} [{severity}] {recon_type}"
                f" — bot={bot_name or '(any)'}"
                f" market={str(market_id or '')[:20]}"
                f" date={recon_date}"
            )
            if details:
                detail_str = json.dumps(details, default=str) if isinstance(details, dict) else str(details)
                print(f"    {detail_str[:120]}")
        return 1 if results else 0
    finally:
        await db.close()


async def _acknowledge(break_id: int, reason: str) -> int:
    """Acknowledge a reconciliation_break by ID."""
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            result = await session.execute(text("""
                UPDATE reconciliation_breaks
                SET status = 'ACKNOWLEDGED',
                    resolution_note = :reason,
                    resolved_at = NOW()
                WHERE break_id = :break_id
                  AND status = 'OPEN'
                RETURNING break_id, recon_type, bot_name
            """), {"break_id": break_id, "reason": reason})
            updated = result.fetchone()
            await session.commit()

        if updated:
            print(f"Acknowledged break #{updated[0]}: {updated[1]} — {updated[2] or '(any bot)'}")
            return 0
        else:
            print(f"ERROR: No OPEN break found with ID {break_id}")
            return 2
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Data integrity audit CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--check", metavar="NAME", help="Run only this check by name")
    parser.add_argument("--bot", metavar="NAME", help="Filter violation output to one bot")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24, 0=all-time)")
    parser.add_argument("--json", dest="output_json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--verbose", action="store_true", help="Per-violation detail")
    parser.add_argument("--list-open", dest="list_open", action="store_true", help="Show all open unacknowledged violations")
    parser.add_argument("--ack", type=int, metavar="BREAK_ID", help="Acknowledge a reconciliation_break by ID")
    parser.add_argument("--reason", metavar="TEXT", help="Acknowledgment reason (required with --ack)")
    parser.add_argument(
        "--triggered-by",
        dest="triggered_by",
        metavar="LABEL",
        default="unlabeled",
        choices=["scheduled_daily", "cli", "manual", "health_check", "post_resolution", "unlabeled"],
        help=(
            "Invocation kind recorded in audit_runs.run_type. Default 'unlabeled' "
            "(intentionally NOT 'cli' — a missing-label invocation must be "
            "distinguishable from an explicit manual CLI run so downstream queries "
            "and the Phase 5 sentinel can detect wiring drift). Use 'scheduled_daily' "
            "from the systemd timer unit, 'cli' for explicit manual runs. The "
            "audit_runs.triggered_by column is derived via _KIND_TO_SOURCE mapping "
            "because it carries a CHECK constraint on {scheduler, cli, health_check, "
            "post_resolution, manual}."
        ),
    )

    args = parser.parse_args()

    if args.list_open:
        rc = asyncio.run(_list_open())
        sys.exit(rc)

    if args.ack is not None:
        if not args.reason:
            print("ERROR: --reason is required with --ack")
            sys.exit(2)
        rc = asyncio.run(_acknowledge(args.ack, args.reason))
        sys.exit(rc)

    rc = asyncio.run(_run_checks(
        check_name=args.check,
        bot_filter=args.bot,
        hours=args.hours,
        output_json=args.output_json,
        verbose=args.verbose,
        triggered_by=args.triggered_by,
    ))
    sys.exit(rc)


if __name__ == "__main__":
    main()
