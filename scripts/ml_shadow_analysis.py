"""ML Shadow Race Analysis — MirrorBot S130"""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # 1. Resolved count
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT e.market_id) "
            "FROM trade_events e "
            "JOIN trade_events res ON res.market_id = e.market_id "
            "  AND res.bot_name = 'MirrorBot' AND res.event_type = 'RESOLUTION' "
            "WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY' "
            "AND e.event_data::text LIKE '%%ml_score_xgb%%'"
        ))
        print(f"ML-scored entries with RESOLUTION: {r.scalar()}")
        print()

        # 2. XGB buckets
        r2 = await s.execute(text(
            "WITH ml_entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'ml_score_xgb')::float as xgb,"
            "    (e.event_data->>'ml_score_ql')::float as ql,"
            "    (e.event_data->>'ml_score_combo')::float as combo"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_data::text LIKE '%%ml_score_xgb%%'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  CASE"
            "    WHEN m.xgb IS NULL THEN 'null'"
            "    WHEN m.xgb < 0.3 THEN '<0.30'"
            "    WHEN m.xgb < 0.4 THEN '0.30-0.39'"
            "    WHEN m.xgb < 0.5 THEN '0.40-0.49'"
            "    WHEN m.xgb < 0.6 THEN '0.50-0.59'"
            "    WHEN m.xgb < 0.7 THEN '0.60-0.69'"
            "    ELSE '>=0.70'"
            "  END as bucket,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM ml_entries m"
            " JOIN resolutions r ON r.market_id = m.market_id AND r.side = m.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== XGB SCORE vs RESOLUTION P&L ===")
        print(f"{'Bucket':<12} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r2.fetchall():
            print(f"{row[0]:<12} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # 3. QL buckets
        r3 = await s.execute(text(
            "WITH ml_entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'ml_score_ql')::float as ql"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_data::text LIKE '%%ml_score_ql%%'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  CASE"
            "    WHEN m.ql IS NULL THEN 'null'"
            "    WHEN m.ql < 0.3 THEN '<0.30'"
            "    WHEN m.ql < 0.4 THEN '0.30-0.39'"
            "    WHEN m.ql < 0.5 THEN '0.40-0.49'"
            "    WHEN m.ql < 0.6 THEN '0.50-0.59'"
            "    WHEN m.ql < 0.7 THEN '0.60-0.69'"
            "    ELSE '>=0.70'"
            "  END as bucket,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM ml_entries m"
            " JOIN resolutions r ON r.market_id = m.market_id AND r.side = m.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== Q-LEARNING SCORE vs RESOLUTION P&L ===")
        print(f"{'Bucket':<12} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r3.fetchall():
            print(f"{row[0]:<12} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # 4. Combo buckets
        r4 = await s.execute(text(
            "WITH ml_entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'ml_score_combo')::float as combo"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_data::text LIKE '%%ml_score_combo%%'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT"
            "  CASE"
            "    WHEN m.combo IS NULL THEN 'null'"
            "    WHEN m.combo < 0.3 THEN '<0.30'"
            "    WHEN m.combo < 0.4 THEN '0.30-0.39'"
            "    WHEN m.combo < 0.5 THEN '0.40-0.49'"
            "    WHEN m.combo < 0.6 THEN '0.50-0.59'"
            "    WHEN m.combo < 0.7 THEN '0.60-0.69'"
            "    ELSE '>=0.70'"
            "  END as bucket,"
            "  COUNT(*) as n,"
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl,"
            "  ROUND(AVG(r.realized_pnl)::numeric, 2) as avg_pnl"
            " FROM ml_entries m"
            " JOIN resolutions r ON r.market_id = m.market_id AND r.side = m.side"
            " GROUP BY 1 ORDER BY 1"
        ))
        print("=== COMBO SCORE vs RESOLUTION P&L ===")
        print(f"{'Bucket':<12} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12} {'AvgPnL':>10}")
        for row in r4.fetchall():
            print(f"{row[0]:<12} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12} {row[5]:>10}")
        print()

        # 5. Binary high/low split
        r5 = await s.execute(text(
            "WITH ml_entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'ml_score_xgb')::float as xgb,"
            "    (e.event_data->>'ml_score_ql')::float as ql,"
            "    (e.event_data->>'ml_score_combo')::float as combo"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "  AND e.event_data::text LIKE '%%ml_score_xgb%%'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            "), joined AS ("
            "  SELECT m.xgb, m.ql, m.combo, r.realized_pnl"
            "  FROM ml_entries m"
            "  JOIN resolutions r ON r.market_id = m.market_id AND r.side = m.side"
            ") SELECT label, n, wins, wr, total_pnl FROM ("
            "  SELECT 'XGB>=0.5' as label, COUNT(*) as n,"
            "    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,"
            "    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr,"
            "    ROUND(SUM(realized_pnl)::numeric, 2) as total_pnl, 1 as ord"
            "  FROM joined WHERE xgb >= 0.5"
            "  UNION ALL"
            "  SELECT 'XGB<0.5', COUNT(*),"
            "    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),"
            "    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1),"
            "    ROUND(SUM(realized_pnl)::numeric, 2), 2"
            "  FROM joined WHERE xgb < 0.5"
            "  UNION ALL"
            "  SELECT 'QL>=0.5', COUNT(*),"
            "    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),"
            "    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1),"
            "    ROUND(SUM(realized_pnl)::numeric, 2), 3"
            "  FROM joined WHERE ql >= 0.5"
            "  UNION ALL"
            "  SELECT 'QL<0.5', COUNT(*),"
            "    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),"
            "    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1),"
            "    ROUND(SUM(realized_pnl)::numeric, 2), 4"
            "  FROM joined WHERE ql < 0.5"
            "  UNION ALL"
            "  SELECT 'COMBO>=0.5', COUNT(*),"
            "    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),"
            "    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1),"
            "    ROUND(SUM(realized_pnl)::numeric, 2), 5"
            "  FROM joined WHERE combo >= 0.5"
            "  UNION ALL"
            "  SELECT 'COMBO<0.5', COUNT(*),"
            "    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),"
            "    ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1),"
            "    ROUND(SUM(realized_pnl)::numeric, 2), 6"
            "  FROM joined WHERE combo < 0.5"
            ") t ORDER BY ord"
        ))
        print("=== HIGH vs LOW SPLIT (0.5 threshold) ===")
        print(f"{'Model':<14} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>12}")
        for row in r5.fetchall():
            print(f"{row[0]:<14} {row[1]:>5} {row[2]:>5} {row[3]:>6} {row[4]:>12}")
        print()

        # 6. Category P&L (7 days)
        r6 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    e.event_data->>'category' as cat,"
            "    (e.event_data->>'confidence')::float as conf"
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

        # 7. Confidence tier
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

        # 8. Current state
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

    await db.close()

asyncio.run(main())
