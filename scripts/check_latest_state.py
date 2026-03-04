"""Quick state check: paper trades, predictions, sync_log after latest run."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from base_engine.data.database import Database
from sqlalchemy import text


async def check():
    db = Database()
    await db.init()
    async with db.get_session() as session:
        # Paper trades (latest 5)
        r = await session.execute(text(
            "SELECT id, bot_name, market_id, side, price, size, confidence, created_at "
            "FROM paper_trades ORDER BY created_at DESC LIMIT 5"
        ))
        rows = r.fetchall()
        print(f"\n=== LATEST PAPER TRADES (showing last 5 of total) ===")
        for row in rows:
            tid, bot, mid, side, price, size, conf, created = row
            print(f"  #{tid}  {bot}  {side}  price={price:.4f}  size={size:.2f}  "
                  f"conf={conf:.3f}  at={str(created)[:19]}")

        # Total paper trades
        r2 = await session.execute(text("SELECT COUNT(*) FROM paper_trades"))
        total = r2.scalar_one()
        print(f"  Total paper trades: {total}")

        # Predictions today
        r3 = await session.execute(text(
            "SELECT COUNT(*), AVG(predicted_prob), MIN(predicted_prob), MAX(predicted_prob) "
            "FROM prediction_log WHERE created_at > NOW() - INTERVAL '1 hour'"
        ))
        pred = r3.fetchone()
        if pred and pred[0] > 0:
            print(f"\n=== PREDICTIONS (last 1h) ===")
            print(f"  Count: {pred[0]}  Avg: {pred[1]:.3f}  Min: {pred[2]:.3f}  Max: {pred[3]:.3f}")
        else:
            print(f"\n=== PREDICTIONS (last 1h): NONE ===")

        # Sync log latest
        r4 = await session.execute(text(
            "SELECT id, sync_type, status, records_processed, started_at "
            "FROM sync_log ORDER BY started_at DESC LIMIT 5"
        ))
        print(f"\n=== LATEST SYNC LOG ===")
        for row in r4.fetchall():
            sid, stype, status, recs, started = row
            print(f"  #{sid}  type={stype or 'N/A':15s}  status={status or '':8s}  "
                  f"recs={recs or 0:>6d}  at={str(started)[:19]}")

        # Market prices freshness
        r5 = await session.execute(text(
            "SELECT COUNT(*), MAX(timestamp) FROM market_prices"
        ))
        mp = r5.fetchone()
        print(f"\n=== DATA FRESHNESS ===")
        print(f"  market_prices: {mp[0]} rows, latest={str(mp[1])[:19] if mp[1] else 'N/A'}")

        r6 = await session.execute(text(
            "SELECT COUNT(*), MAX(updated_at) FROM markets"
        ))
        mkt = r6.fetchone()
        print(f"  markets: {mkt[0]} rows, latest={str(mkt[1])[:19] if mkt[1] else 'N/A'}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(check())
