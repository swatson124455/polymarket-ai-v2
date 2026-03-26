"""Trace confidence distribution over time"""
import asyncio

def fmt(v, w=7):
    return f"{v:>{w}}" if v is not None else f"{'null':>{w}}"

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # 1. Confidence distribution by day
        r = await s.execute(text(
            "SELECT"
            "  DATE(event_time) as day,"
            "  COUNT(*) as n,"
            "  ROUND(MIN((event_data->>'confidence')::numeric), 3) as min_conf,"
            "  ROUND(AVG((event_data->>'confidence')::numeric), 3) as avg_conf,"
            "  ROUND(MAX((event_data->>'confidence')::numeric), 3) as max_conf"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '21 days'"
            " AND event_data->>'confidence' IS NOT NULL"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== CONFIDENCE DISTRIBUTION BY DAY ===")
        print(f"{'Day':<12} {'N':>5} {'Min':>7} {'Avg':>7} {'Max':>7}")
        for row in r.fetchall():
            print(f"{str(row[0]):<12} {row[1]:>5} {fmt(row[2])} {fmt(row[3])} {fmt(row[4])}")
        print()

        # 2. Confidence breakdown for recent entries
        r2 = await s.execute(text(
            "SELECT"
            "  event_data->>'confidence' as confidence,"
            "  event_data->>'conf_base' as conf_base,"
            "  event_data->>'conf_upstream' as conf_upstream,"
            "  event_data->>'conf_price_adj' as conf_price_adj,"
            "  event_data->>'conf_conv_adj' as conf_conv_adj,"
            "  event_data->>'conf_cal_shadow' as conf_cal_shadow"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '2 hours'"
            " AND event_data->>'conf_base' IS NOT NULL"
            " ORDER BY event_time DESC LIMIT 15"
        ))
        print("=== RECENT ENTRIES — CONFIDENCE COMPONENTS ===")
        print(f"{'confidence':>10} {'conf_base':>10} {'upstream':>10} {'price_adj':>10} {'conv_adj':>10} {'cal_shadow':>11}")
        for row in r2.fetchall():
            vals = [str(v or 'null') for v in row]
            print(f"{vals[0]:>10} {vals[1]:>10} {vals[2]:>10} {vals[3]:>10} {vals[4]:>10} {vals[5]:>11}")
        print()

        # 3. Check: trade_events.confidence vs event_data->>'confidence'
        r3 = await s.execute(text(
            "SELECT"
            "  confidence as te_conf,"
            "  (event_data->>'confidence')::float as ed_conf,"
            "  entry_price as te_price"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '2 hours'"
            " ORDER BY event_time DESC LIMIT 10"
        ))
        print("=== trade_events columns vs event_data ===")
        print(f"{'te.conf':>10} {'ed.conf':>10} {'te.price':>10}")
        for row in r3.fetchall():
            print(f"{row[0]:>10.4f} {row[1]:>10.4f} {row[2]:>10.4f}")
        print()

        # 4. The REAL question: what value is stored in the trade_events.confidence column?
        # And what is the confidence distribution in THAT column?
        r4 = await s.execute(text(
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
        for row in r4.fetchall():
            print(f"{str(row[0]):<12} {row[1]:>5} {fmt(row[2])} {fmt(row[3])} {fmt(row[4])}")
        print()

        # 5. Bucket P&L by week using trade_events.confidence column
        r5 = await s.execute(text(
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
        print("=== CONFIDENCE BUCKET P&L BY WEEK (te.confidence) ===")
        print(f"{'Week':<12} {'Bucket':<10} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12}")
        for row in r5.fetchall():
            print(f"{str(row[0]):<12} {row[1]:<10} {row[2]:>5} {row[3]:>5} {row[4]:>6} {row[5]:>12}")
        print()

        # 6. Now check event_data confidence (the multi-factor one)
        r6 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    DATE_TRUNC('week', e.event_time) as week,"
            "    (e.event_data->>'confidence')::float as conf"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_data->>'confidence' IS NOT NULL"
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
        print("=== CONFIDENCE BUCKET P&L BY WEEK (event_data.confidence) ===")
        print(f"{'Week':<12} {'Bucket':<10} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12}")
        for row in r6.fetchall():
            print(f"{str(row[0]):<12} {row[1]:<10} {row[2]:>5} {row[3]:>5} {row[4]:>6} {row[5]:>12}")

    await db.close()

asyncio.run(main())
