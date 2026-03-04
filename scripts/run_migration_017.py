"""Run migration 017: Add neg_risk columns to markets table."""
import asyncio
import os
import sys

os.environ["SIMULATION_MODE"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    try:
        # Use engine.begin() — bypasses the session pool entirely (critical for DDL).
        # get_session() competes with bot scans for the pool; DDL via get_session() can time out.
        # engine.begin() acquires a fresh direct connection.
        async with db.engine.begin() as conn:
            print("Adding neg_risk column...")
            await conn.execute(text("ALTER TABLE markets ADD COLUMN IF NOT EXISTS neg_risk BOOLEAN DEFAULT false"))
            print("Adding outcome_count column...")
            await conn.execute(text("ALTER TABLE markets ADD COLUMN IF NOT EXISTS outcome_count INTEGER DEFAULT 2"))
            print("Creating index...")
            await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_markets_neg_risk ON markets (neg_risk) WHERE neg_risk = true"))
            print("Migration 017 applied successfully!")

            # Verify
            r = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'markets' AND column_name IN ('neg_risk', 'outcome_count')"
            ))
            cols = [row[0] for row in r.fetchall()]
            print(f"Verified columns: {cols}")
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
