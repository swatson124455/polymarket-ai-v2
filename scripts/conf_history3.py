"""Confidence history — fixed columns"""
import asyncio

def fmt(v, w=7):
    return f"{v:>{w}}" if v is not None else f"{'null':>{w}}"

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # 1. trade_events.confidence column by day
        r = await s.execute(text(
            "SELECT"
            "  DATE(event_time) as day,"
            "  COUNT(*) as n,"
            "  ROUND(MIN(confidence)::numeric, 3) as min_conf,"
            "  ROUND(AVG(confidence)::numeric, 3) as avg_conf,"
            "  ROUND(MAX(confidence)::numeric, 3) as max_conf"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '21 days'"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== trade_events.confidence COLUMN BY DAY ===")
        print(f"{'Day':<12} {'N':>5} {'Min':>7} {'Avg':>7} {'Max':>7}")
        for row in r.fetchall():
            print(f"{str(row[0]):<12} {row[1]:>5} {fmt(row[2])} {fmt(row[3])} {fmt(row[4])}")
        print()

        # 2. Bucket P&L using trade_events.confidence
        r2 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    DATE_TRUNC('week', e.event_time) as week,"
            "    e.confidence as conf"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  e.week::date as week,"
            "  CASE"
            "    WHEN e.conf < 0.50 THEN '<0.50'"
            "    WHEN e.conf < 0.55 THEN '0.50-0.54'"
            "    WHEN e.conf < 0.60 THEN '0.55-0.59'"
            "    WHEN e.conf < 0.65 THEN '0.60-0.64'"
            "    WHEN e.conf < 0.70 THEN '0.65-0.69'"
            "    WHEN e.conf < 0.80 THEN '0.70-0.79'"
            "    ELSE '>=0.80'"
            "  END as bucket,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " GROUP BY 1, 2 ORDER BY 1, 2"
        ))
        print("=== CONFIDENCE BUCKET P&L BY WEEK (te.confidence column) ===")
        print(f"{'Week':<12} {'Bucket':<10} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12}")
        for row in r2.fetchall():
            print(f"{str(row[0]):<12} {row[1]:<10} {row[2]:>5} {row[3]:>5} {row[4]:>6} {row[5]:>12}")
        print()

        # 3. What IS trade_events.confidence storing?
        r3 = await s.execute(text(
            "SELECT confidence, (event_data->>'conf_base')::float as conf_base,"
            "  (event_data->>'conf_upstream')::float as upstream"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '2 hours'"
            " AND event_data->>'conf_base' IS NOT NULL"
            " ORDER BY event_time DESC LIMIT 10"
        ))
        print("=== te.confidence vs event_data components ===")
        print(f"{'te.conf':>10} {'conf_base':>10} {'upstream':>10}")
        for row in r3.fetchall():
            print(f"{row[0]:>10.4f} {row[1]:>10.4f} {row[2]:>10.4f}")
        print()

        # 4. How many entries have event_data->>'confidence' NULL?
        r4 = await s.execute(text(
            "SELECT"
            "  SUM(CASE WHEN event_data->>'confidence' IS NULL THEN 1 ELSE 0 END) as null_ed,"
            "  SUM(CASE WHEN event_data->>'confidence' IS NOT NULL THEN 1 ELSE 0 END) as has_ed,"
            "  COUNT(*) as total"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
        ))
        row4 = r4.fetchone()
        print(f"event_data.confidence: NULL={row4[0]}, present={row4[1]}, total={row4[2]}")
        print()

        # 5. What was confidence like in the GOOD old days?
        # The early weeks when spread was clean
        r5 = await s.execute(text(
            "SELECT"
            "  DATE(event_time) as day,"
            "  COUNT(*) as n,"
            "  ROUND(MIN(confidence)::numeric, 3) as min_conf,"
            "  ROUND(AVG(confidence)::numeric, 3) as avg_conf,"
            "  ROUND(MAX(confidence)::numeric, 3) as max_conf"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time < NOW() - INTERVAL '14 days'"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== EARLY DAYS — te.confidence (>14 days ago) ===")
        print(f"{'Day':<12} {'N':>5} {'Min':>7} {'Avg':>7} {'Max':>7}")
        for row in r5.fetchall():
            print(f"{str(row[0]):<12} {row[1]:>5} {fmt(row[2])} {fmt(row[3])} {fmt(row[4])}")

    await db.close()

asyncio.run(main())
