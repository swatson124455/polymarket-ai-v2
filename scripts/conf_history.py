"""Trace confidence distribution over time — when did it collapse to all >=0.80?"""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # 1. Confidence distribution by day (last 14 days)
        r = await s.execute(text(
            "SELECT"
            "  DATE(event_time) as day,"
            "  COUNT(*) as n,"
            "  ROUND(MIN((event_data->>'confidence')::numeric), 3) as min_conf,"
            "  ROUND(AVG((event_data->>'confidence')::numeric), 3) as avg_conf,"
            "  ROUND(MAX((event_data->>'confidence')::numeric), 3) as max_conf,"
            "  ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY (event_data->>'confidence')::float)::numeric, 3) as p25,"
            "  ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY (event_data->>'confidence')::float)::numeric, 3) as p50,"
            "  ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY (event_data->>'confidence')::float)::numeric, 3) as p75"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '21 days'"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== CONFIDENCE DISTRIBUTION BY DAY ===")
        print(f"{'Day':<12} {'N':>5} {'Min':>7} {'P25':>7} {'P50':>7} {'P75':>7} {'Max':>7} {'Avg':>7}")
        for row in r.fetchall():
            print(f"{str(row[0]):<12} {row[1]:>5} {row[2]:>7} {row[5]:>7} {row[6]:>7} {row[7]:>7} {row[3]:>7} {row[4]:>7}")
        print()

        # 2. What field is the "confidence" in event_data? Is it conf_upstream or the final?
        r2 = await s.execute(text(
            "SELECT"
            "  event_data->>'confidence' as confidence,"
            "  event_data->>'conf_base' as conf_base,"
            "  event_data->>'conf_upstream' as conf_upstream,"
            "  event_data->>'conf_price_adj' as conf_price_adj,"
            "  event_data->>'conf_conv_adj' as conf_conv_adj,"
            "  event_data->>'conf_cal_shadow' as conf_cal_shadow,"
            "  event_data->>'entry_price' as entry_price"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '2 hours'"
            " ORDER BY event_time DESC LIMIT 10"
        ))
        print("=== RECENT 10 ENTRIES — CONFIDENCE BREAKDOWN ===")
        print(f"{'confidence':>10} {'conf_base':>10} {'upstream':>10} {'price_adj':>10} {'conv_adj':>10} {'cal_shadow':>11} {'entry_px':>10}")
        for row in r2.fetchall():
            print(f"{row[0] or 'null':>10} {row[1] or 'null':>10} {row[2] or 'null':>10} {row[3] or 'null':>10} {row[4] or 'null':>10} {row[5] or 'null':>11} {row[6] or 'null':>10}")
        print()

        # 3. Is 'confidence' the same as what's stored in entry_price?
        # Check: does the stored confidence match the formula output or the entry_price?
        r3 = await s.execute(text(
            "SELECT"
            "  ROUND((event_data->>'confidence')::numeric, 4) as stored_conf,"
            "  ROUND((event_data->>'entry_price')::numeric, 4) as entry_price,"
            "  CASE WHEN (event_data->>'confidence')::float = (event_data->>'entry_price')::float THEN 'SAME' ELSE 'DIFF' END as match"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '6 hours'"
            " AND event_data->>'confidence' IS NOT NULL"
            " AND event_data->>'entry_price' IS NOT NULL"
            " ORDER BY event_time DESC LIMIT 20"
        ))
        rows3 = r3.fetchall()
        same = sum(1 for r in rows3 if r[2] == 'SAME')
        print(f"confidence == entry_price? {same}/{len(rows3)} match")
        if rows3:
            for row in rows3[:5]:
                print(f"  conf={row[0]} entry_px={row[1]} → {row[2]}")
        print()

        # 4. What does the trade_events confidence column have vs event_data confidence?
        r4 = await s.execute(text(
            "SELECT"
            "  confidence as te_confidence,"
            "  (event_data->>'confidence')::float as ed_confidence,"
            "  entry_price,"
            "  (event_data->>'entry_price')::float as ed_entry_price"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '2 hours'"
            " ORDER BY event_time DESC LIMIT 5"
        ))
        print("=== trade_events.confidence vs event_data.confidence ===")
        print(f"{'te.conf':>10} {'ed.conf':>10} {'te.price':>10} {'ed.price':>10}")
        for row in r4.fetchall():
            print(f"{row[0]:>10} {row[1]:>10} {row[2]:>10} {row[3] or 'null':>10}")
        print()

        # 5. Historical spread — confidence bucket P&L by week
        r5 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    DATE_TRUNC('week', e.event_time) as week,"
            "    (e.event_data->>'confidence')::float as conf"
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
        print("=== CONFIDENCE BUCKET P&L BY WEEK ===")
        print(f"{'Week':<12} {'Bucket':<10} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12}")
        for row in r5.fetchall():
            print(f"{str(row[0]):<12} {row[1]:<10} {row[2]:>5} {row[3]:>5} {row[4]:>6} {row[5]:>12}")

    await db.close()

asyncio.run(main())
