#!/usr/bin/env python3
"""One-time EB-scoped backlog: book RESOLUTION P&L for EsportsBot positions that were
orphan-closed without a recorded outcome.

Context (2026-06-17, eb/main): EsportsBot is in PM_EXCLUDE_BOTS (owns its own
resolution close) AND the shared resolution backfill (resolution_backfill.py /
Database.backfill_positions_resolution) does NOT run on the esports splinter. So the
orphan-close path (esports_bot.py, SELL-fail fallback) flipped resolved-market
positions to status='closed' with NO unrealized_pnl and NO EXIT/RESOLUTION
trade_event -> their win/loss was never booked. The forward fix
(_resolution_close_position) handles this going forward; this script settles the
existing backlog.

Selection: EB-family positions (EsportsBot / EsportsBotV2 only -- NEVER touches
MB/WB rows) that are status='closed', have NULL/0 unrealized_pnl, sit on a RESOLVED
market, and have NO EXIT/RESOLUTION trade_event. P&L uses the SAME payout formula as
Database.backfill_positions_resolution (side matches resolution -> 1.0 else 0.0,
minus taker fee). Idempotent: the NOT EXISTS guard + insert_trade_event idempotency
make re-runs safe.

Usage:
  python scripts/eb_resolution_backlog.py            # DRY RUN (default) -- report only
  python scripts/eb_resolution_backlog.py --execute  # apply
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from base_engine.data.database import Database  # noqa: E402
from config.settings import settings  # noqa: E402

EB_FAMILY = ("EsportsBot", "EsportsBotV2")

SELECT_SQL = """
SELECT p.id, p.market_id, p.side, p.size, p.entry_price,
       COALESCE(p.bot_id, p.source_bot) AS bot,
       m.resolution
FROM positions p
JOIN markets m ON (CAST(m.id AS TEXT) = p.market_id OR m.condition_id = p.market_id)
WHERE COALESCE(p.bot_id, p.source_bot) = ANY(:fam)
  AND p.status = 'closed'
  AND UPPER(p.side) IN ('YES', 'NO')   -- exclude 363 spurious 'SELL' exit-as-position
                                       -- rows (S214-class corruption): resolution
                                       -- payout is only defined for a YES/NO holding,
                                       -- and SELL rows duplicate their YES/NO sibling.
  AND (p.unrealized_pnl IS NULL OR p.unrealized_pnl = 0)
  AND m.resolution IN ('YES', 'NO')
  AND p.size > 0
  AND p.entry_price IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM trade_events te
      WHERE te.bot_name = COALESCE(p.bot_id, p.source_bot)
        AND te.market_id = p.market_id
        AND te.event_type IN ('EXIT', 'RESOLUTION')
  )
"""


async def main(execute: bool) -> None:
    db = Database()
    await db.init()
    fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0

    async with db.get_session() as s:
        rows = (await s.execute(text(SELECT_SQL), {"fam": list(EB_FAMILY)})).fetchall()

    if not rows:
        print("No orphaned resolved EB positions to book. Backlog clean.")
        return

    plan = []
    total = 0.0
    wins = losses = 0
    for r in rows:
        pid, mid, side, size, entry, bot, resolution = r
        side_u = str(side).upper()
        payout = 1.0 if side_u == str(resolution).upper() else 0.0
        fee = float(entry) * float(size) * fee_rate
        realized = (payout - float(entry)) * float(size) - fee
        total += realized
        if realized >= 0:
            wins += 1
        else:
            losses += 1
        plan.append((pid, mid, side_u, float(size), float(entry), resolution, payout, fee, realized, bot))

    print(f"{'EXECUTE' if execute else 'DRY RUN'} -- {len(plan)} EB positions to book")
    print(f"  net_pos(payout>=cost): {wins}   net_neg: {losses}   net realized: {total:+.2f}")
    for (pid, mid, side_u, size, entry, resolution, payout, fee, realized, bot) in plan[:50]:
        print(f"  pos={pid} {bot} {str(mid)[:14]} side={side_u} res={resolution} "
              f"size={size:.1f} entry={entry:.4f} payout={payout} pnl={realized:+.2f}")
    if len(plan) > 50:
        print(f"  ... +{len(plan) - 50} more")

    if not execute:
        print("\nDRY RUN -- nothing written. Re-run with --execute to apply.")
        return

    booked = 0
    for (pid, mid, side_u, size, entry, resolution, payout, fee, realized, bot) in plan:
        async with db.get_session() as s:
            await s.execute(
                text("UPDATE positions SET unrealized_pnl = :pnl WHERE id = :pid AND status = 'closed'"),
                {"pnl": realized, "pid": pid},
            )
            await s.commit()
        await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name=bot,
            market_id=mid,
            side=side_u,
            size=size,
            price=payout,
            fees=round(fee, 6),
            realized_pnl=round(realized, 6),
            event_data={"exit_reason": "backlog_resolution", "resolution": resolution, "backfilled": True},
        )
        booked += 1

    print(f"\nBooked {booked} RESOLUTION events (EB family only). Net realized: {total:+.2f}")
    print("Verify with: python scripts/bot_pnl.py EsportsBot 720")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply changes (default: dry run)")
    asyncio.run(main(ap.parse_args().execute))
