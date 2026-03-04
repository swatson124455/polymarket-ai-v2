"""Quick check of open positions in DB."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from base_engine.data.database import Database, Position
from sqlalchemy import select

async def main():
    db = Database()
    await db.init()
    async with db.get_session() as session:
        result = await session.execute(select(Position).where(Position.status == "open"))
        positions = result.scalars().all()
        for p in positions:
            opened = getattr(p, "opened_at", None) or getattr(p, "created_at", None)
            sz = p.size or 0
            print(f"  id={p.id} mkt={p.market_id} side={p.side} size={sz:.2f} entry={p.entry_price} opened={opened}")
        print(f"Total open: {len(positions)}")
    await db.close()

asyncio.run(main())
