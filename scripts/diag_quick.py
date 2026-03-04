"""Quick: count predictions in last 5 minutes."""
import asyncio, sys
sys.path.insert(0, ".")
from base_engine.data.database import Database
from sqlalchemy import text

async def run():
    db = Database()
    await db.init()
    async with db.get_session() as s:
        r = await s.execute(text(
            "SELECT COUNT(*) FROM prediction_log WHERE created_at > NOW() - INTERVAL '5 minutes'"
        ))
        print(f"Predictions in last 5 min: {r.scalar()}")
        r2 = await s.execute(text(
            "SELECT MAX(confidence) FROM prediction_log WHERE created_at > NOW() - INTERVAL '5 minutes'"
        ))
        print(f"Max confidence in last 5 min: {r2.scalar()}")
        r3 = await s.execute(text(
            "SELECT COUNT(*) FROM prediction_log WHERE created_at > NOW() - INTERVAL '5 minutes' AND confidence >= 0.65"
        ))
        print(f"High confidence (>=0.65) in last 5 min: {r3.scalar()}")
        r4 = await s.execute(text(
            "SELECT COUNT(*) FROM prediction_log WHERE created_at > NOW() - INTERVAL '5 minutes' AND confidence >= 0.65 AND edge > 0.01"
        ))
        print(f"High conf + positive edge in last 5 min: {r4.scalar()}")
    await db.close()

asyncio.run(run())
