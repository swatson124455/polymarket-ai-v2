"""MirrorBot 48h P&L breakdown by confidence tier."""
import asyncio
from decimal import Decimal
from base_engine.data.database import Database
from sqlalchemy import text as sa_text

QUERY = sa_text("""
WITH entries AS (
    SELECT
        te.market_id,
        te.token_id,
        te.event_data->>'conf_composite' as conf_composite,
        te.event_data->>'trade_usd' as trade_usd,
        te.side,
        te.price
    FROM trade_events te
    WHERE te.bot_name = 'MirrorBot'
      AND te.event_type = 'ENTRY'
      AND te.event_time >= NOW() - INTERVAL '48 hours'
      AND te.event_time <= NOW()
      AND COALESCE(te.event_data->>'calibration_exclude', '') = ''
),
resolutions AS (
    SELECT DISTINCT ON (te.market_id, te.token_id)
        te.market_id,
        te.token_id,
        te.realized_pnl
    FROM trade_events te
    WHERE te.bot_name = 'MirrorBot'
      AND te.event_type IN ('EXIT', 'RESOLUTION')
      AND te.realized_pnl IS NOT NULL
      AND te.event_time >= NOW() - INTERVAL '48 hours'
      AND te.event_time <= NOW()
    ORDER BY te.market_id, te.token_id, te.event_time DESC
)
SELECT
    CASE
        WHEN CAST(e.conf_composite AS numeric) < 0.50 THEN '0.45-0.50'
        WHEN CAST(e.conf_composite AS numeric) < 0.55 THEN '0.50-0.55'
        WHEN CAST(e.conf_composite AS numeric) < 0.60 THEN '0.55-0.60'
        WHEN CAST(e.conf_composite AS numeric) < 0.65 THEN '0.60-0.65'
        WHEN CAST(e.conf_composite AS numeric) < 0.70 THEN '0.65-0.70'
        ELSE '0.70+'
    END as conf_tier,
    COUNT(*) as n_entries,
    COUNT(r.market_id) as n_resolved,
    SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(SUM(COALESCE(r.realized_pnl, 0))::numeric, 2) as total_pnl,
    ROUND(AVG(CASE WHEN r.market_id IS NOT NULL THEN r.realized_pnl END)::numeric, 2) as avg_pnl,
    ROUND(MIN(CASE WHEN r.market_id IS NOT NULL THEN r.realized_pnl END)::numeric, 2) as worst,
    ROUND(MAX(CASE WHEN r.market_id IS NOT NULL THEN r.realized_pnl END)::numeric, 2) as best,
    ROUND(AVG(CAST(e.trade_usd AS numeric))::numeric, 2) as avg_size
FROM entries e
LEFT JOIN resolutions r ON e.market_id = r.market_id AND e.token_id = r.token_id
WHERE e.conf_composite IS NOT NULL
GROUP BY 1
ORDER BY 1
""")

OPEN_QUERY = sa_text("""
SELECT COUNT(*), ROUND(COALESCE(SUM(CAST(event_data->>'trade_usd' AS numeric)), 0)::numeric, 2)
FROM trade_events te
WHERE te.bot_name = 'MirrorBot'
  AND te.event_type = 'ENTRY'
  AND te.event_time >= NOW() - INTERVAL '48 hours'
  AND te.event_time <= NOW()
  AND NOT EXISTS (
      SELECT 1 FROM trade_events r
      WHERE r.bot_name = 'MirrorBot'
        AND r.event_type IN ('EXIT', 'RESOLUTION')
        AND r.market_id = te.market_id
        AND r.token_id = te.token_id
        AND r.realized_pnl IS NOT NULL
  )
""")

TOTALS_QUERY = sa_text("""
SELECT
    COUNT(*) as total_entries,
    ROUND(SUM(CAST(event_data->>'trade_usd' AS numeric))::numeric, 2) as total_deployed
FROM trade_events
WHERE bot_name = 'MirrorBot'
  AND event_type = 'ENTRY'
  AND event_time >= NOW() - INTERVAL '48 hours'
  AND event_time <= NOW()
""")


async def main():
    db = Database()
    await db.init()

    async with db.get_session() as session:
        result = await session.execute(QUERY)
        rows = result.fetchall()

    hdr = f"{'Tier':<12} {'Entries':>8} {'Resolved':>9} {'Wins':>6} {'Win%':>7} {'Total P&L':>11} {'Avg P&L':>9} {'Worst':>9} {'Best':>9} {'AvgSize':>9}"
    print("=" * len(hdr))
    print("MirrorBot 48h P&L by Confidence Tier")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))

    total_entries = 0
    total_resolved = 0
    total_wins = 0
    grand_pnl = Decimal("0")

    for row in rows:
        tier, n_entries, n_resolved, wins, total_pnl, avg_pnl, worst, best, avg_size = row
        win_pct = f"{(wins / n_resolved * 100):.1f}%" if n_resolved > 0 else "N/A"
        total_entries += n_entries
        total_resolved += n_resolved
        total_wins += wins
        grand_pnl += total_pnl or Decimal("0")
        print(
            f"{tier:<12} {n_entries:>8} {n_resolved:>9} {wins:>6} {win_pct:>7}"
            f" {'$' + str(total_pnl):>11} {'$' + str(avg_pnl):>9} {'$' + str(worst):>9}"
            f" {'$' + str(best):>9} {'$' + str(avg_size):>9}"
        )

    print("-" * len(hdr))
    overall_wp = f"{(total_wins / total_resolved * 100):.1f}%" if total_resolved > 0 else "N/A"
    print(f"{'TOTAL':<12} {total_entries:>8} {total_resolved:>9} {total_wins:>6} {overall_wp:>7} {'$' + str(grand_pnl):>11}")

    async with db.get_session() as session:
        result = await session.execute(OPEN_QUERY)
        unres = result.fetchone()
    print(f"\nOpen (unresolved): {unres[0]} positions, ${unres[1]} deployed")

    async with db.get_session() as session:
        result = await session.execute(TOTALS_QUERY)
        totals = result.fetchone()
    print(f"Total 48h entries: {totals[0]}, total deployed: ${totals[1]}")


if __name__ == "__main__":
    asyncio.run(main())
