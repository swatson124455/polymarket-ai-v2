"""Analyze prediction log statistics to understand trading behavior."""
import asyncio
import io
import os
import sys

os.environ["SIMULATION_MODE"] = "true"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    async with db.get_session() as s:
        # Confidence distribution
        r = await s.execute(text(
            "SELECT "
            "  COUNT(*) as total, "
            "  COUNT(CASE WHEN confidence >= 0.55 THEN 1 END) as above_threshold, "
            "  COUNT(CASE WHEN confidence >= 0.55 AND edge > 0.02 THEN 1 END) as tradeable, "
            "  COUNT(CASE WHEN trade_executed = true THEN 1 END) as traded, "
            "  AVG(confidence) as avg_conf, "
            "  MIN(confidence) as min_conf, "
            "  MAX(confidence) as max_conf, "
            "  AVG(edge) as avg_edge, "
            "  MIN(edge) as min_edge, "
            "  MAX(edge) as max_edge "
            "FROM prediction_log"
        ))
        row = r.fetchone()
        print("=== PREDICTION LOG STATISTICS ===")
        print(f"  Total predictions:    {row[0]}")
        print(f"  Conf >= 0.55:         {row[1]} ({100*row[1]/max(row[0],1):.1f}%)")
        print(f"  Conf >= 0.55 & edge>2%: {row[2]} ({100*row[2]/max(row[0],1):.1f}%)")
        print(f"  Actually traded:      {row[3]} ({100*row[3]/max(row[0],1):.1f}%)")
        print(f"  Confidence: min={row[5]:.4f} avg={row[4]:.4f} max={row[6]:.4f}")
        print(f"  Edge:       min={row[8]:.4f} avg={row[7]:.4f} max={row[9]:.4f}")

        # Confidence buckets
        r2 = await s.execute(text(
            "SELECT "
            "  CASE "
            "    WHEN confidence < 0.2 THEN '0.00-0.20' "
            "    WHEN confidence < 0.4 THEN '0.20-0.40' "
            "    WHEN confidence < 0.55 THEN '0.40-0.55' "
            "    WHEN confidence < 0.7 THEN '0.55-0.70' "
            "    WHEN confidence < 0.85 THEN '0.70-0.85' "
            "    ELSE '0.85-1.00' "
            "  END as bucket, "
            "  COUNT(*), "
            "  AVG(edge), "
            "  COUNT(CASE WHEN trade_executed = true THEN 1 END) "
            "FROM prediction_log "
            "GROUP BY bucket ORDER BY bucket"
        ))
        print("\n  Confidence Distribution:")
        print(f"  {'Bucket':12s} {'Count':>6s} {'Avg Edge':>10s} {'Traded':>8s}")
        for row in r2.fetchall():
            avg_edge = float(row[2]) if row[2] else 0
            print(f"  {row[0]:12s} {row[1]:6d} {avg_edge:10.4f} {row[3]:8d}")

        # Edge distribution for high-confidence predictions
        r3 = await s.execute(text(
            "SELECT "
            "  CASE "
            "    WHEN edge < -0.10 THEN 'edge<-10%' "
            "    WHEN edge < -0.05 THEN '-10%<edge<-5%' "
            "    WHEN edge < 0.0 THEN '-5%<edge<0%' "
            "    WHEN edge < 0.02 THEN '0%<edge<2%' "
            "    WHEN edge < 0.05 THEN '2%<edge<5%' "
            "    WHEN edge < 0.10 THEN '5%<edge<10%' "
            "    ELSE 'edge>10%' "
            "  END as bucket, "
            "  COUNT(*) "
            "FROM prediction_log "
            "WHERE confidence >= 0.55 "
            "GROUP BY bucket ORDER BY bucket"
        ))
        print("\n  Edge Distribution (conf >= 0.55 only):")
        for row in r3.fetchall():
            bar = "#" * min(row[1], 50)
            print(f"  {row[0]:16s}: {row[1]:4d} {bar}")

        # Unique markets evaluated
        r4 = await s.execute(text(
            "SELECT COUNT(DISTINCT market_id) FROM prediction_log"
        ))
        print(f"\n  Unique markets evaluated: {r4.scalar()}")

        # Time range
        r5 = await s.execute(text(
            "SELECT MIN(created_at), MAX(created_at) FROM prediction_log"
        ))
        row5 = r5.fetchone()
        print(f"  First prediction: {row5[0]}")
        print(f"  Last prediction:  {row5[1]}")

        # Predictions per hour
        r6 = await s.execute(text(
            "SELECT "
            "  date_trunc('hour', created_at) as hour, "
            "  COUNT(*) as count "
            "FROM prediction_log "
            "GROUP BY hour ORDER BY hour"
        ))
        print("\n  Predictions per hour:")
        for row in r6.fetchall():
            bar = "#" * min(row[1], 50)
            print(f"    {row[0]}: {row[1]:4d} {bar}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
