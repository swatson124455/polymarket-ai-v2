"""Check actual entry prices vs confidence — sizing sanity check"""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # What entry prices is MirrorBot actually trading at?
        r = await s.execute(text(
            "SELECT"
            "  CASE"
            "    WHEN price < 0.20 THEN '<0.20'"
            "    WHEN price < 0.35 THEN '0.20-0.34'"
            "    WHEN price < 0.50 THEN '0.35-0.49'"
            "    WHEN price < 0.65 THEN '0.50-0.64'"
            "    WHEN price < 0.80 THEN '0.65-0.79'"
            "    ELSE '>=0.80'"
            "  END as price_bucket,"
            "  COUNT(*) as n,"
            "  ROUND(MIN(confidence)::numeric, 3) as min_conf,"
            "  ROUND(AVG(confidence)::numeric, 3) as avg_conf,"
            "  ROUND(MAX(confidence)::numeric, 3) as max_conf"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '7 days'"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== ENTRY PRICE vs CONFIDENCE (7 days) ===")
        print(f"{'Price':<12} {'N':>5} {'MinConf':>8} {'AvgConf':>8} {'MaxConf':>8}")
        for row in r.fetchall():
            print(f"{row[0]:<12} {row[1]:>5} {row[2]:>8} {row[3]:>8} {row[4]:>8}")
        print()

        # How many trades have confidence > price (Kelly would size > 0)?
        r2 = await s.execute(text(
            "SELECT"
            "  SUM(CASE WHEN confidence > price THEN 1 ELSE 0 END) as edge_yes,"
            "  SUM(CASE WHEN confidence <= price THEN 1 ELSE 0 END) as edge_no,"
            "  COUNT(*) as total"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '7 days'"
        ))
        row2 = r2.fetchone()
        print(f"Confidence > Price (Kelly sizes >0): {row2[0]}/{row2[2]}")
        print(f"Confidence <= Price (Kelly returns 0): {row2[1]}/{row2[2]}")
        print()

        # Recent 10 entries: confidence vs price vs size
        r3 = await s.execute(text(
            "SELECT confidence, price, size,"
            "  ROUND((size * price)::numeric, 2) as usd_value,"
            "  CASE WHEN confidence > price THEN 'EDGE' ELSE 'NO_EDGE' END as edge"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '2 hours'"
            " ORDER BY event_time DESC LIMIT 10"
        ))
        print("=== RECENT ENTRIES: conf vs price vs size ===")
        print(f"{'Conf':>8} {'Price':>8} {'Shares':>10} {'USD':>10} {'Edge?':>8}")
        for row in r3.fetchall():
            print(f"{row[0]:>8.4f} {row[1]:>8.4f} {row[2]:>10.2f} {row[3]:>10} {row[4]:>8}")

    await db.close()

asyncio.run(main())
