"""Diagnose why no new trades after initial 2."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from base_engine.data.database import Database, Position
from sqlalchemy import select, text

async def main():
    db = Database()
    await db.init()
    async with db.get_session() as session:
        # Open positions
        result = await session.execute(select(Position).where(Position.status.in_(["open", "reserving"])))
        open_pos = result.scalars().all()
        open_mids = set(str(p.market_id) for p in open_pos)
        print(f"Open positions: {len(open_pos)} on markets: {open_mids}")

        # Recent prediction counts
        r = await session.execute(text(
            "SELECT COUNT(*), MAX(created_at) FROM prediction_log WHERE created_at > NOW() - INTERVAL '60 minutes'"
        ))
        row = r.fetchone()
        print(f"Predictions last 60min: {row[0]}, latest: {row[1]}")

        r = await session.execute(text(
            "SELECT COUNT(*) FROM prediction_log WHERE created_at > NOW() - INTERVAL '60 minutes' AND confidence >= 0.65"
        ))
        print(f"High-conf (>=65%) last 60min: {r.scalar()}")

        # Top predictions with edge
        r = await session.execute(text("""
            SELECT pl.market_id, pl.confidence, pl.predicted_prob, pl.market_price, pl.edge, pl.trade_side, pl.created_at
            FROM prediction_log pl
            WHERE pl.created_at > NOW() - INTERVAL '60 minutes'
            AND pl.confidence >= 0.65
            ORDER BY pl.confidence DESC
            LIMIT 30
        """))
        rows = r.fetchall()
        print(f"\nTop 30 high-conf predictions last 60min:")
        print(f"  {'Market':<10} {'Conf':>6} {'Pred':>6} {'Price':>6} {'Edge':>7} {'Side':<5} {'Held?':<10} Time")
        print(f"  {'-'*70}")
        for row in rows:
            mid = str(row[0])
            held = "HELD" if mid in open_mids else ""
            edge_val = row[4] or 0
            edge_sign = "+" if edge_val > 0 else ""
            print(f"  {mid:<10} {row[1]:>5.1%} {row[2]:>6.3f} {row[3]:>6.3f} {edge_sign}{edge_val:>6.3f} {(row[5] or '?'):<5} {held:<10} {str(row[6])[11:19]}")

        # Count by edge sign
        r = await session.execute(text("""
            SELECT
                SUM(CASE WHEN edge > 0.01 THEN 1 ELSE 0 END) as positive_edge,
                SUM(CASE WHEN edge <= 0.01 AND edge > -0.01 THEN 1 ELSE 0 END) as near_zero,
                SUM(CASE WHEN edge <= -0.01 THEN 1 ELSE 0 END) as negative_edge
            FROM prediction_log
            WHERE created_at > NOW() - INTERVAL '60 minutes'
            AND confidence >= 0.65
        """))
        row = r.fetchone()
        print(f"\nEdge distribution (high-conf): +edge={row[0]}, ~zero={row[1]}, -edge={row[2]}")

        # Markets with positive edge NOT currently held
        r = await session.execute(text("""
            SELECT DISTINCT pl.market_id, pl.confidence, pl.edge
            FROM prediction_log pl
            WHERE pl.created_at > NOW() - INTERVAL '60 minutes'
            AND pl.confidence >= 0.65
            AND pl.edge > 0.01
            ORDER BY pl.edge DESC
        """))
        rows = r.fetchall()
        tradeable = [r for r in rows if str(r[0]) not in open_mids]
        print(f"\nPositive-edge markets NOT held: {len(tradeable)} of {len(rows)} total")
        for row in tradeable[:10]:
            print(f"  mkt={row[0]} conf={row[1]:.3f} edge={row[2]:.3f}")

        # Check if those markets exist in positions table (closed rows blocking?)
        if tradeable:
            for row in tradeable[:5]:
                mid = str(row[0])
                r2 = await session.execute(text(
                    f"SELECT id, bot_id, side, status FROM positions WHERE market_id = '{mid}'"
                ))
                pos_rows = r2.fetchall()
                if pos_rows:
                    print(f"  -> market {mid} has {len(pos_rows)} position rows: {[(r[0], r[2], r[3]) for r in pos_rows]}")

    await db.close()

asyncio.run(main())
