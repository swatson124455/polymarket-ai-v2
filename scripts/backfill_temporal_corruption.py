#!/usr/bin/env python3
"""Backfill forward-dated resolution-observation timestamps.

Pre-conditions:
  - Migration 077 applied (CHECK constraints in place — script writes NOW()
    which trivially passes the constraint).
  - Commit 1 (source fix) deployed and running — no new corrupt rows being
    written. Without this, the script may need to be re-run.

What it does:
  Per affected table, UPDATEs rows where the resolution-observation timestamp
  is > NOW():
    - If the underlying market is resolved=TRUE → set to NOW()
    - If resolved=FALSE or no matching market → set to NULL

Tables (counts at audit time):
  markets.resolved_at                   898 rows  (all resolved=TRUE → NOW())
  paper_trades.resolved_at               32 rows  (per-row CASE)
  prediction_log.resolved_at         35,824 rows  (35,448 → NOW, 376 → NULL)
  mirror_rejected_signals.resolved_at 4,120 rows  (3,739 → NOW, 381 → NULL)
  traded_markets.resolved_at              2 rows  (per-row CASE)

  trade_events.event_time                22 rows  SKIPPED — append-only table
    blocked by trg_trade_events_immutable. Stage 4 bot_pnl.py upper-bound
    fix excludes them from windowed P&L queries.

Re-runnable: WHERE resolved_at > NOW() filter means a second run touches zero
rows once corruption is cleared.

Usage:
  python scripts/backfill_temporal_corruption.py --dry-run    # preview, no writes
  python scripts/backfill_temporal_corruption.py --apply       # commit
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_engine.data.database import Database
from dotenv import load_dotenv

load_dotenv()


# Each table gets TWO UPDATEs:
#   (1) JOIN-based: set resolved_at=NOW() for rows whose market is resolved=TRUE
#   (2) Catch-all:  set resolved_at=NULL for remaining corrupt rows (unresolved
#                   markets or no matching market row)
# JOIN-based is far faster than the EXISTS-correlated-subquery form because
# planner can hash-join markets once instead of scanning per row.
# prediction_log has 35K+ corrupt rows — correlated EXISTS hits statement_timeout.
TABLES = [
    {
        "name": "markets",
        "updates": [
            # markets is the source of truth — V1 confirmed 100% resolved=TRUE.
            ("UPDATE markets SET resolved_at = NOW() WHERE resolved_at > NOW()", "all"),
        ],
        "count_before": "SELECT COUNT(*) FROM markets WHERE resolved_at > NOW()",
    },
    {
        "name": "paper_trades",
        "updates": [
            ("""
            UPDATE paper_trades pt SET resolved_at = NOW()
            FROM markets m
            WHERE pt.resolved_at > NOW()
              AND (CAST(m.id AS TEXT) = pt.market_id OR m.condition_id = pt.market_id)
              AND m.resolved = TRUE
            """, "resolved_to_now"),
            ("UPDATE paper_trades SET resolved_at = NULL WHERE resolved_at > NOW()",
             "remaining_to_null"),
        ],
        "count_before": "SELECT COUNT(*) FROM paper_trades WHERE resolved_at > NOW()",
    },
    {
        "name": "prediction_log",
        "updates": [
            ("""
            UPDATE prediction_log pl SET resolved_at = NOW()
            FROM markets m
            WHERE pl.resolved_at > NOW()
              AND (CAST(m.id AS TEXT) = pl.market_id OR m.condition_id = pl.market_id)
              AND m.resolved = TRUE
            """, "resolved_to_now"),
            ("UPDATE prediction_log SET resolved_at = NULL WHERE resolved_at > NOW()",
             "remaining_to_null"),
        ],
        "count_before": "SELECT COUNT(*) FROM prediction_log WHERE resolved_at > NOW()",
    },
    {
        "name": "mirror_rejected_signals",
        "updates": [
            ("""
            UPDATE mirror_rejected_signals mrs SET resolved_at = NOW()
            FROM markets m
            WHERE mrs.resolved_at > NOW()
              AND (CAST(m.id AS TEXT) = mrs.market_id OR m.condition_id = mrs.market_id)
              AND m.resolved = TRUE
            """, "resolved_to_now"),
            ("UPDATE mirror_rejected_signals SET resolved_at = NULL WHERE resolved_at > NOW()",
             "remaining_to_null"),
        ],
        "count_before": "SELECT COUNT(*) FROM mirror_rejected_signals WHERE resolved_at > NOW()",
    },
    {
        "name": "traded_markets",
        "updates": [
            ("""
            UPDATE traded_markets tm SET resolved_at = NOW()
            FROM markets m
            WHERE tm.resolved_at > NOW()
              AND (CAST(m.id AS TEXT) = tm.market_id OR m.condition_id = tm.market_id)
              AND m.resolved = TRUE
            """, "resolved_to_now"),
            ("UPDATE traded_markets SET resolved_at = NULL WHERE resolved_at > NOW()",
             "remaining_to_null"),
        ],
        "count_before": "SELECT COUNT(*) FROM traded_markets WHERE resolved_at > NOW()",
    },
]


async def main():
    dry_run = "--dry-run" in sys.argv
    apply = "--apply" in sys.argv

    if not dry_run and not apply:
        print("Specify --dry-run (preview) or --apply (commit). Refusing to run silently.")
        sys.exit(2)
    if dry_run and apply:
        print("Pass either --dry-run OR --apply, not both.")
        sys.exit(2)

    db = Database()
    await db.init()

    from sqlalchemy import text

    print(f"=== Temporal corruption backfill ({'DRY-RUN' if dry_run else 'APPLY'}) ===")
    print()

    async with db.get_session() as s:
        # Loosen statement_timeout for the duration of this session. JOIN-based
        # UPDATE on 35K-row prediction_log can exceed the default 60s.
        await s.execute(text("SET LOCAL statement_timeout = '600s'"))

        # Before counts
        before_counts = {}
        for t in TABLES:
            r = await s.execute(text(t["count_before"]))
            before_counts[t["name"]] = r.scalar_one_or_none() or 0

        print("Before:")
        for name, n in before_counts.items():
            print(f"  {name:32s} {n:>8d} corrupt rows")
        total_before = sum(before_counts.values())
        print(f"  {'TOTAL':32s} {total_before:>8d}")
        print()

        if total_before == 0:
            print("No corruption found. Nothing to do.")
            return

        if dry_run:
            print("--dry-run: no writes. Re-run with --apply to execute.")
            return

        # Apply — each table runs 1+ UPDATE statements in sequence.
        applied_counts = {}
        for t in TABLES:
            total_for_table = 0
            for sql, label in t["updates"]:
                r = await s.execute(text(sql))
                n = r.rowcount or 0
                total_for_table += n
                print(f"  {t['name']:32s} {label:<22s} updated {n:>8d} rows")
            applied_counts[t["name"]] = total_for_table

        await s.commit()
        print()
        print("Committed.")
        print()

        # After counts (sanity)
        after_counts = {}
        for t in TABLES:
            r = await s.execute(text(t["count_before"]))
            after_counts[t["name"]] = r.scalar_one_or_none() or 0

        print("After:")
        for name, n in after_counts.items():
            print(f"  {name:32s} {n:>8d} corrupt rows remaining")
        total_after = sum(after_counts.values())
        print(f"  {'TOTAL':32s} {total_after:>8d}")

        if total_after > 0:
            print()
            print(f"WARNING: {total_after} corrupt rows remain. Check logs above.")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
