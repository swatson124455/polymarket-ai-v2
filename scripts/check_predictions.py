"""Check prediction log entries to see confidence distribution."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_engine.data.database import Database

async def main():
    db = Database()
    await db.init()

    from sqlalchemy import text
    async with db.get_session() as session:
        # Check actual columns
        result = await session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'prediction_log' ORDER BY ordinal_position"
        ))
        cols = [r[0] for r in result.fetchall()]
        print(f"prediction_log columns: {cols}")

        # Get counts
        result = await session.execute(text("SELECT COUNT(*) FROM prediction_log"))
        print(f"\nTotal predictions: {result.scalar()}")

        result = await session.execute(text("SELECT COUNT(*) FROM paper_trades"))
        print(f"Total paper trades: {result.scalar()}")

        # Get recent predictions
        result = await session.execute(text(
            "SELECT id, market_id, model_name, predicted_prob, market_price, edge, "
            "confidence, ensemble_pred, learning_conf, created_at "
            "FROM prediction_log ORDER BY created_at DESC LIMIT 20"
        ))
        rows = result.fetchall()
        print(f"\nRecent predictions ({len(rows)}):")
        for row in rows:
            print(f"  id={row[0]} market={row[1]} model={row[2]} "
                  f"pred={row[3]:.4f} price={row[4]:.4f} edge={row[5]:.4f} "
                  f"conf={row[6]:.4f} ens={row[7]} learn={row[8]} "
                  f"at={row[9]}")

        # Confidence distribution
        result = await session.execute(text(
            "SELECT MIN(confidence), MAX(confidence), AVG(confidence), COUNT(*) "
            "FROM prediction_log"
        ))
        row = result.fetchone()
        if row:
            print(f"\nConfidence stats: min={row[0]:.4f} max={row[1]:.4f} avg={row[2]:.4f} count={row[3]}")
            print(f"  Threshold: 0.55")
            if row[1] and row[1] >= 0.55:
                print(f"  SOME predictions pass the threshold!")
            else:
                print(f"  NO predictions pass the 0.55 threshold")

    await db.close()

asyncio.run(main())
