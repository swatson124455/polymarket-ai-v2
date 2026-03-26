"""Compare ML-scored entries vs regular entries — MirrorBot"""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # 1. Count split
        r = await s.execute(text(
            "SELECT "
            "  SUM(CASE WHEN event_data::text LIKE '%%ml_score_xgb%%' THEN 1 ELSE 0 END) as ml,"
            "  SUM(CASE WHEN event_data::text NOT LIKE '%%ml_score_xgb%%' THEN 1 ELSE 0 END) as regular,"
            "  COUNT(*) as total,"
            "  MIN(event_time)::text as first_entry,"
            "  MAX(event_time)::text as last_entry"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
        ))
        row = r.fetchone()
        print(f"=== ENTRY COUNT SPLIT ===")
        print(f"ML-scored:  {row[0]}")
        print(f"Regular:    {row[1]}")
        print(f"Total:      {row[2]}")
        print(f"Date range: {row[3]} to {row[4]}")
        print()

        # 2. When did ML scoring start?
        r2 = await s.execute(text(
            "SELECT MIN(event_time)::text, MAX(event_time)::text"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_data::text LIKE '%%ml_score_xgb%%'"
        ))
        row2 = r2.fetchone()
        print(f"ML scoring active from: {row2[0]} to {row2[1]}")
        print()

        # 3. Sample ML entry event_data keys
        r3 = await s.execute(text(
            "SELECT event_data::text"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_data::text LIKE '%%ml_score_xgb%%'"
            " ORDER BY event_time DESC LIMIT 1"
        ))
        print(f"=== SAMPLE ML ENTRY event_data ===")
        import json
        ml_data = json.loads(r3.scalar())
        for k, v in sorted(ml_data.items()):
            print(f"  {k}: {v}")
        print()

        # 4. Sample regular entry event_data keys
        r4 = await s.execute(text(
            "SELECT event_data::text"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_data::text NOT LIKE '%%ml_score_xgb%%'"
            " ORDER BY event_time DESC LIMIT 1"
        ))
        print(f"=== SAMPLE REGULAR ENTRY event_data ===")
        reg_data = json.loads(r4.scalar())
        for k, v in sorted(reg_data.items()):
            print(f"  {k}: {v}")
        print()

        # 5. P&L comparison: ML vs Regular (resolved only)
        r5 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    CASE WHEN e.event_data::text LIKE '%%ml_score_xgb%%' THEN 'ML' ELSE 'Regular' END as group_type"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  e.group_type,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print(f"=== ML vs REGULAR — RESOLVED P&L ===")
        print(f"{'Group':<10} {'N':>6} {'Wins':>6} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r5.fetchall():
            print(f"{row[0]:<10} {row[1]:>6} {row[2]:>6} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # 6. What keys exist ONLY in ML entries (diff the key sets)
        r6 = await s.execute(text(
            "SELECT DISTINCT jsonb_object_keys(event_data) as k"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_data::text LIKE '%%ml_score_xgb%%'"
            " ORDER BY k"
        ))
        ml_keys = set(row[0] for row in r6.fetchall())

        r7 = await s.execute(text(
            "SELECT DISTINCT jsonb_object_keys(event_data) as k"
            " FROM trade_events"
            " WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
            " AND event_data::text NOT LIKE '%%ml_score_xgb%%'"
            " ORDER BY k"
        ))
        reg_keys = set(row[0] for row in r7.fetchall())

        print(f"=== KEY DIFF ===")
        print(f"ML-only keys:      {sorted(ml_keys - reg_keys)}")
        print(f"Regular-only keys: {sorted(reg_keys - ml_keys)}")
        print(f"Shared keys:       {sorted(ml_keys & reg_keys)}")

    await db.close()

asyncio.run(main())
