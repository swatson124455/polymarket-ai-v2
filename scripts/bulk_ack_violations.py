#!/usr/bin/env python3
"""S167+: Bulk-acknowledge historical violations in reconciliation_breaks.

Ack classes (each gated by a distinct safety filter):
  Class 1 — Closed-position violations on known-fixed check types (S167)
  Class 2 — WeatherBot orphan violations (bot paused 2026-04-08, S167)
  Class 3 — TRADED_MARKETS_DRIFT pre-S192 fix (recon_date < 2026-04-23,
            commit edcf93e shipped that day fixed the CSV-bot_names emission
            bug that produced these as legacy false-positives, S195 §S192
            close item 4.2)
  Class 4 — STALE_POSITION pre-S184 (S184 close: STALE_POSITION P0
            resolved via run_reconciliation() rewrite + new
            TradedMarketsStatusDriftCheck shipped d60ae17; remaining
            STALE_POSITION rows are pre-S184 legacy)
  Class 5 — Quiescent SIZE_INVARIANT/TEMPORAL_ORDER/DUPLICATE_ENTRY
            (S196). Markets with no trade_events activity in the past 7
            days have frozen disposal vs entry sums — no further events
            will unfreeze the relationship. ACK silences the daily audit
            noise. The S196 audit auto-close (commits e7149eb + 037c603)
            handles same-key supersede on each daily run; Class 5 then
            silences the canonical today's row for permanently-frozen
            data. Re-run periodically as new markets quiesce.

S196 update: SIZE_INVARIANT was previously skipped per S185 reclassification.
After S196 forward-audit, the audit check is correct and the data is real.
The Phase 4b-alt fix (commit 0e1f2e0) closed the upstream emission gap; the
RESOLUTION over-size guard (commit a76d9d8) is defense-in-depth. Class 5
addresses the historical residue that the now-corrected source can't reach.

Safety: only OPEN → ACKNOWLEDGED.  Never touches current-day violations
in Classes 1-4. Class 5 may touch current-day rows if the underlying market
has been quiescent for 7+ days — the recon_date is incidental, the data
state is the gate.

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

        # ── Class 3: TRADED_MARKETS_DRIFT pre-S192 fix ──────────────
        # S192 commit edcf93e (deployed 2026-04-23) fixed the CSV-bot_names
        # SQL+emission bug. Violations dated before that commit are
        # legacy false-positives — same root cause, no longer reachable.
        # Post-S192 TMD violations are real and stay OPEN for triage.
        class3_result = await session.execute(text("""
            UPDATE reconciliation_breaks
            SET status = 'ACKNOWLEDGED',
                resolution_note = 'S195 bulk-ack: TRADED_MARKETS_DRIFT pre-S192 fix (edcf93e 2026-04-23)'
            WHERE status = 'OPEN'
              AND recon_type = 'TRADED_MARKETS_DRIFT'
              AND recon_date < '2026-04-23'
        """))
        class3_count = class3_result.rowcount
        print(f"Class 3 (TRADED_MARKETS_DRIFT pre-S192): {class3_count} acked")

        # ── Class 4: STALE_POSITION pre-S184 ─────────────────────────
        # S184 (commit 535c14e + d60ae17, deployed 20260420_121852) shipped
        # TradedMarketsStatusDriftCheck and rewrote run_reconciliation();
        # the STALE_POSITION P0 was resolved. Residual STALE_POSITION rows
        # are pre-S184 legacy (3 rows on prod at S195 close per the
        # operator's audit). Cap the date filter at 2026-04-20 — the
        # S184 deploy day — so any post-fix recurrence stays OPEN for
        # triage.
        class4_result = await session.execute(text("""
            UPDATE reconciliation_breaks
            SET status = 'ACKNOWLEDGED',
                resolution_note = 'S195 bulk-ack: STALE_POSITION pre-S184 fix (d60ae17 2026-04-20)'
            WHERE status = 'OPEN'
              AND recon_type = 'STALE_POSITION'
              AND recon_date < '2026-04-20'
        """))
        class4_count = class4_result.rowcount
        print(f"Class 4 (STALE_POSITION pre-S184):       {class4_count} acked")

        # ── Class 5: Quiescent SIZE_INVARIANT/TEMPORAL_ORDER/DUPLICATE_ENTRY ──
        # S196 forward-audit: after commits 0e1f2e0 (Phase 4b-alt size fix) +
        # a76d9d8 (RESOLUTION over-size guard) closed the inflation source,
        # remaining OPEN violations on (bot, market) pairs with no
        # trade_events activity in the past 7 days represent frozen
        # historical data — no further events can unfreeze the
        # SUM(disposal) vs SUM(ENTRY) relationship. ACK silences the
        # daily audit re-emission for these quiescent markets.
        # Safety: 7-day quiescent window is conservative — a recently-
        # active market stays OPEN for triage. Re-run as more markets
        # quiesce; idempotent (NOT EXISTS only matches current-OPEN rows).
        class5_result = await session.execute(text("""
            UPDATE reconciliation_breaks rb
            SET status = 'ACKNOWLEDGED',
                resolution_note = 'S196 bulk-ack: quiescent (bot, market) — no trade_events in past 7 days; data frozen post-Phase 4b-alt fix (0e1f2e0)'
            WHERE rb.status = 'OPEN'
              AND rb.recon_type IN (
                  'SIZE_INVARIANT', 'TEMPORAL_ORDER', 'DUPLICATE_ENTRY'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te
                  WHERE te.bot_name = rb.bot_name
                    AND te.market_id = rb.market_id
                    AND te.event_time >= NOW() - INTERVAL '7 days'
              )
        """))
        class5_count = class5_result.rowcount
        print(f"Class 5 (S196 quiescent historical):     {class5_count} acked")

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
              f"({class1_count + class2_count + class3_count + class4_count + class5_count} acknowledged)")


if __name__ == "__main__":
    asyncio.run(main())
