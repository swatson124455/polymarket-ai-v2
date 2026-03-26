"""ML Shadow Race Analysis Part 2 — category, confidence, current state"""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # Category P&L (7 days)
        r6 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    e.event_data->>'category' as cat"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_time >= NOW() - INTERVAL '7 days'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  COALESCE(e.cat, 'unknown') as category,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " GROUP BY 1 ORDER BY total_pnl ASC"
        ))
        print("=== CATEGORY P&L (7 days, worst first) ===")
        print(f"{'Category':<20} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r6.fetchall():
            print(f"{row[0]:<20} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # Confidence tier
        r7 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'confidence')::float as conf"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_time >= NOW() - INTERVAL '7 days'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  CASE"
            "    WHEN e.conf < 0.55 THEN '<0.55'"
            "    WHEN e.conf < 0.60 THEN '0.55-0.59'"
            "    WHEN e.conf < 0.65 THEN '0.60-0.64'"
            "    WHEN e.conf < 0.70 THEN '0.65-0.69'"
            "    WHEN e.conf < 0.75 THEN '0.70-0.74'"
            "    WHEN e.conf < 0.80 THEN '0.75-0.79'"
            "    ELSE '>=0.80'"
            "  END as conf_bucket,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== CONFIDENCE TIER P&L (7 days) ===")
        print(f"{'ConfBucket':<12} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r7.fetchall():
            print(f"{row[0]:<12} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # Price bucket P&L
        r_price = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'entry_price')::float as price"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_time >= NOW() - INTERVAL '7 days'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  CASE"
            "    WHEN e.price < 0.20 THEN '<0.20'"
            "    WHEN e.price < 0.35 THEN '0.20-0.34'"
            "    WHEN e.price < 0.50 THEN '0.35-0.49'"
            "    WHEN e.price < 0.65 THEN '0.50-0.64'"
            "    WHEN e.price < 0.80 THEN '0.65-0.79'"
            "    ELSE '>=0.80'"
            "  END as price_bucket,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== ENTRY PRICE BUCKET P&L (7 days) ===")
        print(f"{'PriceBucket':<12} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r_price.fetchall():
            print(f"{row[0]:<12} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # Side split
        r_side = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_time >= NOW() - INTERVAL '7 days'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  e.side,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== SIDE SPLIT (7 days) ===")
        print(f"{'Side':<6} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12}")
        for row in r_side.fetchall():
            print(f"{row[0]:<6} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12}")
        print()

        # Current state
        r8 = await s.execute(text(
            "SELECT COUNT(*) FROM positions WHERE bot_name = 'MirrorBot' AND status = 'OPEN'"
        ))
        print(f"Open positions: {r8.scalar()}")

        r9 = await s.execute(text(
            "SELECT COUNT(*), ROUND(SUM(realized_pnl)::numeric, 2)"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            " AND event_time >= NOW() - INTERVAL '24 hours'"
        ))
        row9 = r9.fetchone()
        print(f"Resolutions last 24h: {row9[0]}, P&L: {row9[1]}")

        r10 = await s.execute(text(
            "SELECT COUNT(*) FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_time >= NOW() - INTERVAL '24 hours'"
        ))
        print(f"Entries last 24h: {r10.scalar()}")

        # All-time snapshot
        r11 = await s.execute(text(
            "SELECT event_type, COUNT(*), ROUND(SUM(realized_pnl)::numeric, 2)"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type IN ('EXIT', 'RESOLUTION')"
            " GROUP BY 1 ORDER BY 1"
        ))
        print()
        print("=== ALL-TIME P&L ===")
        for row in r11.fetchall():
            print(f"{row[0]}: count={row[1]}, P&L={row[2]}")

    await db.close()

asyncio.run(main())
