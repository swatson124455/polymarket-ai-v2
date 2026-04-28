#!/usr/bin/env python3
"""
S200 one-shot: backfill orphan SHADOW_ENTRY market stubs + close in-scope
FK_MISSING_MARKET reconciliation_breaks for those markets.

Counterpart to S199 commit 5d0eefb (writer-side prevention at
base_engine/data/database.py:5498). This is the data-side cleanup of pre-S199
orphan trade_events: 28,501 SHADOW_ENTRY events on 764 markets that were
inserted before SHADOW_ENTRY joined the FK auto-heal path.

Why a manual bulk-close after backfill:
  The audit auto-close rule (base_engine/audit/result_store.py:114-229) is
  gated on "today's run produced AT LEAST ONE violation of this recon_type"
  (line 165-167: `for recon_type, detected_keys in detected_by_type.items():
  if not detected_keys: continue`). After backfill, the next FK integrity
  check produces zero FK_MISSING_MARKET violations, so the gate stays OFF
  and OPEN rows from earlier runs persist. Closing them inline in the same
  transaction is cleaner than a separate human follow-up.

Usage:
    python scripts/backfill_shadow_entry_markets.py --dry-run    # report only
    python scripts/backfill_shadow_entry_markets.py              # execute

Idempotent. Safe to re-run.
"""
import argparse
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

from sqlalchemy import text  # noqa: E402

from base_engine.data.database import Database  # noqa: E402


_RESOLUTION_NOTE = (
    "S200 backfill: orphan SHADOW_ENTRY market stubs inserted via "
    "scripts/backfill_shadow_entry_markets.py (closes S199 hygiene item 3)"
)


async def _run(dry_run: bool) -> int:
    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            r = await session.execute(text("""
                SELECT COUNT(*) AS orphan_events,
                       COUNT(DISTINCT te.market_id) AS orphan_markets
                FROM trade_events te
                LEFT JOIN markets m
                  ON CAST(m.id AS TEXT) = te.market_id
                  OR m.condition_id = te.market_id
                WHERE m.id IS NULL AND te.event_type = 'SHADOW_ENTRY'
            """))
            row = r.fetchone()
            pre_orphan_events = int(row[0]) if row else 0
            pre_orphan_markets = int(row[1]) if row else 0

            r = await session.execute(text("""
                SELECT DISTINCT te.market_id
                FROM trade_events te
                LEFT JOIN markets m
                  ON CAST(m.id AS TEXT) = te.market_id
                  OR m.condition_id = te.market_id
                WHERE m.id IS NULL AND te.event_type = 'SHADOW_ENTRY'
            """))
            orphan_market_ids = [str(row[0]) for row in r.fetchall()]

            r = await session.execute(text("""
                SELECT COUNT(*) FROM reconciliation_breaks
                WHERE status = 'OPEN' AND recon_type = 'FK_MISSING_MARKET'
            """))
            pre_open_fk_total = int(r.scalar() or 0)

            r = await session.execute(text("""
                SELECT COUNT(*) FROM reconciliation_breaks
                WHERE status = 'OPEN' AND recon_type = 'FK_MISSING_MARKET'
                  AND market_id = ANY(:ids)
            """), {"ids": orphan_market_ids})
            pre_open_fk_in_scope = int(r.scalar() or 0)

            print("Pre-flight:")
            print(f"  Orphan SHADOW_ENTRY events:         {pre_orphan_events}")
            print(f"  Distinct orphan markets:            {pre_orphan_markets}")
            print(f"  OPEN FK_MISSING_MARKET (all):       {pre_open_fk_total}")
            print(f"  OPEN FK_MISSING_MARKET (in scope):  {pre_open_fk_in_scope}")

            if dry_run:
                print()
                print(f"DRY-RUN: would INSERT {len(orphan_market_ids)} market stubs")
                print(f"         would close {pre_open_fk_in_scope} OPEN FK_MISSING_MARKET rows")
                return 0

            if not orphan_market_ids:
                print("\nNo orphan markets to backfill. Exiting cleanly.")
                return 0

            ins_result = await session.execute(text("""
                INSERT INTO markets (id, condition_id, active)
                SELECT te.market_id,
                       CASE WHEN te.market_id LIKE '0x%' THEN te.market_id ELSE NULL END,
                       true
                FROM (
                    SELECT DISTINCT te.market_id
                    FROM trade_events te
                    LEFT JOIN markets m
                      ON CAST(m.id AS TEXT) = te.market_id
                      OR m.condition_id = te.market_id
                    WHERE m.id IS NULL AND te.event_type = 'SHADOW_ENTRY'
                ) te
                ON CONFLICT (id) DO NOTHING
            """))
            inserted_n = ins_result.rowcount

            close_result = await session.execute(text("""
                UPDATE reconciliation_breaks
                SET status = 'RESOLVED',
                    resolved_at = NOW(),
                    resolution_note = :note
                WHERE status = 'OPEN'
                  AND recon_type = 'FK_MISSING_MARKET'
                  AND market_id = ANY(:ids)
            """), {"ids": orphan_market_ids, "note": _RESOLUTION_NOTE})
            closed_n = close_result.rowcount

            await session.commit()

            r = await session.execute(text("""
                SELECT COUNT(*) FROM trade_events te
                LEFT JOIN markets m
                  ON CAST(m.id AS TEXT) = te.market_id
                  OR m.condition_id = te.market_id
                WHERE m.id IS NULL
            """))
            post_orphan_total = int(r.scalar() or 0)

            r = await session.execute(text("""
                SELECT COUNT(*) FROM reconciliation_breaks
                WHERE status = 'OPEN' AND recon_type = 'FK_MISSING_MARKET'
                  AND market_id = ANY(:ids)
            """), {"ids": orphan_market_ids})
            post_open_fk_in_scope = int(r.scalar() or 0)

            print()
            print("Post-execution:")
            print(f"  Markets inserted:                   {inserted_n}")
            print(f"  Reconciliation_breaks closed:       {closed_n}")
            print(f"  Orphan trade_events (any type):     {post_orphan_total}")
            print(f"  OPEN FK_MISSING_MARKET (in scope):  {post_open_fk_in_scope}")

            if post_orphan_total != 0:
                print(f"\nFAIL: post_orphan_total={post_orphan_total}, expected 0")
                return 1
            if post_open_fk_in_scope != 0:
                print(f"\nFAIL: post_open_fk_in_scope={post_open_fk_in_scope}, expected 0")
                return 1

            print("\nOK: orphan trade_events = 0; in-scope OPEN FK breaks = 0.")
            return 0
    finally:
        await db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill orphan SHADOW_ENTRY market stubs")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report pre-flight counts only; do not modify DB")
    args = ap.parse_args()
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
