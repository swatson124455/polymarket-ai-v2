#!/usr/bin/env python3
"""
S201 one-shot: backfill ENTRY trade_events for the Bug A cohort — positions
that pre-date the trade_events ledger (commit 7bbf930, 2026-03-13) and were
disposed (RESOLUTION/EXIT) without a corresponding ENTRY event ever being
emitted. Counterpart to S200 commit 50b892a's market-stub backfill: same
shape, different orphan class.

Cohort definition (per AGENT_HANDOFF_S201_CLOSE.md §Phase 2):
  - 73 markets across all 3 bots: 64 WB + 9 MB + 1 EB
  - Identifier: SUM(ENTRY)=0 AND SUM(EXIT+RESOLUTION size)>0 in trade_events
  - WB sub-cohort: positions opened 2026-03-08 to 2026-03-12 (pre-ledger)
  - MB sub-cohort: positions opened 2026-03-13 to 2026-03-14 (ledger-rollout
    race; 1/9 still has a positions row, 8/9 cleaned up)
  - EB sub-cohort: 1 position opened 2026-03-14 (same day as ledger commit)

Source for backfilled size:
  size = positions.entry_cost / positions.entry_price

  This is the cost-basis-derived original ENTRY size — the size the position
  had at first fill, before any post-entry mutation. We deliberately do NOT
  use positions.size (currently 0 — Phase 4b-alt cleared it on RESOLUTION
  emission) and we do NOT use the RESOLUTION trade_event's size (which is
  the post-inflation value Phase 4b-alt copied from positions.size at sweep
  time). Truth-preserving: ENTRY events show the real entry size; the
  RESOLUTION → ENTRY size mismatch remains visible to the SIZE_INVARIANT
  audit check as an intentional residue marker for the historical inflation.

What this resolves:
  - ORPHAN_RESOLUTION reconciliation_breaks: now have an ENTRY event paired
    with the RESOLUTION, audit's "RESOLUTION without ENTRY" detection passes
  - bot_pnl.py block 4a (whole-history integrity): reduces violation count
    by however many markets balance to within 1.001 tolerance after backfill
    (likely few, since RESOLUTION sizes are inflated ~67× vs entry_cost-
    derived sizes — see 0x562e: entry 0.66 vs RESOLUTION 43.93)
  - Phase 7 elevation gate evaluations: cleaner CLEAN cohort eligibility

What this does NOT resolve (intentional):
  - SIZE_INVARIANT detections: RESOLUTION size (43.93) > ENTRY size (0.66)
    is still flagged. That flag now has clear semantics: "this market had
    a historical Phase-4b-alt-emitter inflation, the RESOLUTION P&L is
    inflated relative to the real position." Operators can manually ACK.
  - POSITION_SIZE_MISMATCH: not in scope here (positions.size is currently
    0 for these markets, so the check may already be self-resolved).
  - 8 MB markets without positions rows: can't be sourced from positions.
    Filed as §S201 hygiene item — needs paper_trades-based backfill.

Idempotency:
  Only inserts ENTRY events when no ENTRY exists yet for (bot_name,
  market_id). The trade_events idempotency unique index (event_time,
  bot_name, market_id, event_type, side) provides defense-in-depth.

Usage:
    python scripts/backfill_pre_ledger_entries.py --dry-run    # report only
    python scripts/backfill_pre_ledger_entries.py              # execute

Safe to re-run.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

from sqlalchemy import text  # noqa: E402

from base_engine.data.database import Database  # noqa: E402


_BACKFILL_SOURCE = "S201_pre_ledger"
_RESOLUTION_NOTE = (
    "S201 backfill: pre-ledger ENTRY events emitted from positions.entry_cost "
    "via scripts/backfill_pre_ledger_entries.py (resolves Bug A ORPHAN_RESOLUTION "
    "subset; SIZE_INVARIANT residue intentional)"
)


# Cohort SQL: markets where SUM(ENTRY)=0 in trade_events but disposal>0.
# Joined to positions on (market_id, bot_id OR source_bot) — the positions
# table uses both columns historically (bot_id legacy, source_bot post-S125).
_COHORT_SQL = """
    WITH bug_a AS (
        SELECT bot_name, market_id
        FROM trade_events
        GROUP BY bot_name, market_id
        HAVING SUM(CASE WHEN event_type = 'ENTRY' THEN 1 ELSE 0 END) = 0
           AND SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION')
                        THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) > 0
    )
    SELECT b.bot_name,
           b.market_id,
           p.id           AS position_id,
           p.side,
           p.entry_price,
           p.entry_cost,
           p.opened_at,
           CAST(p.entry_cost / p.entry_price AS NUMERIC(18,8)) AS derived_size
    FROM bug_a b
    JOIN positions p
      ON p.market_id = b.market_id
     AND (p.bot_id = b.bot_name OR p.source_bot = b.bot_name)
    WHERE p.entry_price IS NOT NULL AND p.entry_price > 0
      AND p.entry_cost IS NOT NULL AND p.entry_cost > 0
      AND p.side IN ('YES','NO')
    ORDER BY b.bot_name, b.market_id, p.id
"""


async def _run(dry_run: bool) -> int:
    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            r = await session.execute(text("""
                SELECT bot_name, COUNT(*) AS n
                FROM (
                    SELECT bot_name, market_id
                    FROM trade_events
                    GROUP BY bot_name, market_id
                    HAVING SUM(CASE WHEN event_type = 'ENTRY' THEN 1 ELSE 0 END) = 0
                       AND SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION')
                                    THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) > 0
                ) t
                GROUP BY bot_name
                ORDER BY bot_name
            """))
            cohort_breakdown = {row[0]: int(row[1]) for row in r.fetchall()}
            cohort_total = sum(cohort_breakdown.values())

            r = await session.execute(text(_COHORT_SQL))
            in_scope = r.fetchall()
            in_scope_market_ids = sorted({str(row[1]) for row in in_scope})

            r = await session.execute(text("""
                SELECT COUNT(*)
                FROM reconciliation_breaks
                WHERE status = 'OPEN'
                  AND recon_type = 'ORPHAN_RESOLUTION'
                  AND market_id = ANY(:ids)
            """), {"ids": in_scope_market_ids})
            pre_open_orphan_res = int(r.scalar() or 0)

            r = await session.execute(text("""
                SELECT COUNT(*)
                FROM reconciliation_breaks
                WHERE status = 'OPEN'
                  AND recon_type = 'SIZE_INVARIANT'
                  AND market_id = ANY(:ids)
            """), {"ids": in_scope_market_ids})
            pre_open_size_invariant = int(r.scalar() or 0)

            print("Pre-flight:")
            print(f"  Bug A cohort total:                      {cohort_total}")
            for bot, n in sorted(cohort_breakdown.items()):
                print(f"    {bot:<14}                       {n}")
            print(f"  In-scope rows (positions joinable):      {len(in_scope)}")
            print(f"  In-scope distinct markets:               {len(in_scope_market_ids)}")
            print(f"  OPEN ORPHAN_RESOLUTION (in scope):       {pre_open_orphan_res}")
            print(f"  OPEN SIZE_INVARIANT  (in scope):         "
                  f"{pre_open_size_invariant} (NOT closed by backfill — see docstring)")

            if dry_run:
                print()
                print(f"DRY-RUN: would INSERT up to {len(in_scope)} ENTRY trade_events")
                print(f"         would close {pre_open_orphan_res} OPEN ORPHAN_RESOLUTION rows")
                print(f"         (out-of-scope: cohort_total - in_scope_markets = "
                      f"{cohort_total - len(in_scope_market_ids)} markets without "
                      f"joinable positions row — separate hygiene)")
                return 0

            if not in_scope:
                print("\nNo in-scope rows. Exiting cleanly.")
                return 0

            inserted_n = 0
            skipped_n = 0
            for row in in_scope:
                bot_name, market_id, pos_id, side, entry_price, entry_cost, opened_at, derived_size = row

                event_data = json.dumps({
                    "backfill_source": _BACKFILL_SOURCE,
                    "positions_id": int(pos_id),
                    "entry_cost_usd": float(entry_cost),
                    "note": "size derived from positions.entry_cost / positions.entry_price; "
                            "RESOLUTION size mismatch is the historical inflation residue",
                })

                ins = await session.execute(text("""
                    INSERT INTO trade_events (
                        event_type, execution_mode, event_time,
                        bot_name, market_id, side, size, price,
                        fees, realized_pnl, event_data
                    )
                    SELECT 'ENTRY', 'paper', :event_time,
                           :bot_name, :market_id, :side, :size, :price,
                           0, NULL, CAST(:event_data AS JSONB)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM trade_events
                        WHERE bot_name = :bot_name
                          AND market_id = :market_id
                          AND event_type = 'ENTRY'
                    )
                    ON CONFLICT DO NOTHING
                """), {
                    "event_time": opened_at,
                    "bot_name": bot_name,
                    "market_id": market_id,
                    "side": side,
                    "size": derived_size,
                    "price": entry_price,
                    "event_data": event_data,
                })
                if ins.rowcount and ins.rowcount > 0:
                    inserted_n += 1
                else:
                    skipped_n += 1

            close_result = await session.execute(text("""
                UPDATE reconciliation_breaks
                SET status = 'RESOLVED',
                    resolved_at = NOW(),
                    resolution_note = :note
                WHERE status = 'OPEN'
                  AND recon_type = 'ORPHAN_RESOLUTION'
                  AND market_id = ANY(:ids)
            """), {"ids": in_scope_market_ids, "note": _RESOLUTION_NOTE})
            closed_n = close_result.rowcount

            await session.commit()

            r = await session.execute(text("""
                SELECT COUNT(*)
                FROM (
                    SELECT bot_name, market_id
                    FROM trade_events
                    WHERE bot_name = ANY(:bots)
                      AND market_id = ANY(:ids)
                    GROUP BY bot_name, market_id
                    HAVING SUM(CASE WHEN event_type = 'ENTRY' THEN 1 ELSE 0 END) = 0
                       AND SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION')
                                    THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) > 0
                ) t
            """), {"bots": list(cohort_breakdown.keys()), "ids": in_scope_market_ids})
            post_in_scope_still_orphan = int(r.scalar() or 0)

            r = await session.execute(text("""
                SELECT COUNT(*)
                FROM reconciliation_breaks
                WHERE status = 'OPEN'
                  AND recon_type = 'ORPHAN_RESOLUTION'
                  AND market_id = ANY(:ids)
            """), {"ids": in_scope_market_ids})
            post_open_orphan_res = int(r.scalar() or 0)

            print()
            print("Post-execution:")
            print(f"  ENTRY events inserted:                   {inserted_n}")
            print(f"  ENTRY events skipped (already exist):    {skipped_n}")
            print(f"  ORPHAN_RESOLUTION breaks closed:         {closed_n}")
            print(f"  In-scope markets still orphan (ENTRY=0): {post_in_scope_still_orphan}")
            print(f"  OPEN ORPHAN_RESOLUTION (in scope):       {post_open_orphan_res}")

            ok = True
            if post_in_scope_still_orphan != 0:
                print(f"\nFAIL: {post_in_scope_still_orphan} in-scope markets still report ENTRY=0")
                ok = False
            if post_open_orphan_res != 0:
                print(f"\nFAIL: {post_open_orphan_res} OPEN ORPHAN_RESOLUTION rows remain in scope")
                ok = False

            if ok:
                print("\nOK: in-scope ENTRY=0 cohort cleared; in-scope OPEN ORPHAN_RESOLUTION = 0.")
                return 0
            return 1
    finally:
        await db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill pre-ledger ENTRY trade_events from positions")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report pre-flight counts only; do not modify DB")
    args = ap.parse_args()
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
