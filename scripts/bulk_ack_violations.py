#!/usr/bin/env python3
"""S167: Bulk-acknowledge historical violations in reconciliation_breaks.

Two ack classes:
  Class 1 — Closed-position violations on known-fixed check types
  Class 2 — WeatherBot orphan violations (bot paused 2026-04-08)

Safety: only OPEN → ACKNOWLEDGED.  Never touches current-day violations.
Run on VPS:
  PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bulk_ack_violations.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "production")


async def main() -> None:
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    async with db.get_session() as session:
        # ── Pre-counts ──────────────────────────────────────────────
        r = await session.execute(text(
            "SELECT recon_type, COUNT(*) FROM reconciliation_breaks "
            "WHERE status = 'OPEN' GROUP BY recon_type ORDER BY COUNT(*) DESC"
        ))
        print("=== OPEN violations by type (before) ===")
        total_before = 0
        for row in r.fetchall():
            print(f"  {row[0]:40s} {row[1]:>6d}")
            total_before += row[1]
        print(f"  {'TOTAL':40s} {total_before:>6d}")

        # ── Class 1: closed-position violations on known-fixed checks ──
        # size_invariant + temporal_order were inflated by EXIT side='SELL'
        # grouping (fixed in S166).  Only ack where position is closed.
        class1_result = await session.execute(text("""
            UPDATE reconciliation_breaks rb
            SET status = 'ACKNOWLEDGED',
                resolution_note = 'S167 bulk-ack: closed position + known-fixed check class'
            WHERE rb.status = 'OPEN'
              AND rb.recon_date < CURRENT_DATE
              AND rb.recon_type IN (
                  'SIZE_INVARIANT', 'TEMPORAL_ORDER', 'DUPLICATE_ENTRY'
              )
              AND EXISTS (
                  SELECT 1 FROM positions p
                  WHERE p.market_id = rb.market_id
                    AND p.status = 'closed'
              )
        """))
        class1_count = class1_result.rowcount
        print(f"\nClass 1 (closed-position + known-fixed): {class1_count} acked")

        # ── Class 2: WeatherBot orphan violations (bot paused 2026-04-08) ──
        # These violations reference markets not in the DB — Class 1 filter
        # can't reach them.  WeatherBot is stopped; violations won't recur.
        class2_result = await session.execute(text("""
            UPDATE reconciliation_breaks
            SET status = 'ACKNOWLEDGED',
                resolution_note = 'S167 bulk-ack: WeatherBot orphan (bot paused 2026-04-08)'
            WHERE status = 'OPEN'
              AND recon_date < '2026-04-09'
              AND bot_name = 'WeatherBot'
        """))
        class2_count = class2_result.rowcount
        print(f"Class 2 (WeatherBot orphans pre-pause): {class2_count} acked")

        await session.commit()

        # ── Post-counts ─────────────────────────────────────────────
        r = await session.execute(text(
            "SELECT recon_type, COUNT(*) FROM reconciliation_breaks "
            "WHERE status = 'OPEN' GROUP BY recon_type ORDER BY COUNT(*) DESC"
        ))
        print("\n=== OPEN violations by type (after) ===")
        total_after = 0
        for row in r.fetchall():
            print(f"  {row[0]:40s} {row[1]:>6d}")
            total_after += row[1]
        print(f"  {'TOTAL':40s} {total_after:>6d}")

        print(f"\nSummary: {total_before} → {total_after} OPEN violations "
              f"({class1_count + class2_count} acknowledged)")


if __name__ == "__main__":
    asyncio.run(main())
