"""
Run add_market_prices_unique_constraint migration.
Enables fast bulk insert (ON CONFLICT DO NOTHING) for historical price ingestion.
Safe to run multiple times (idempotent).
"""
import asyncio
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.chdir(_project_root)

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


async def run():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized", file=sys.stderr)
        return 1

    try:
        async with db.session_factory() as session:
            # Add constraint if not exists. If duplicates exist, run schema/add_market_prices_unique_constraint.sql (dedup first).
            await session.execute(text("""
                DO $$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_market_prices_market_token_timestamp'
                  ) THEN
                    ALTER TABLE market_prices
                    ADD CONSTRAINT uq_market_prices_market_token_timestamp
                    UNIQUE (market_id, token_id, timestamp);
                  END IF;
                END $$
            """))
            await session.commit()
        print("Migration completed: uq_market_prices_market_token_timestamp")
        return 0
    except Exception as e:
        if "already exists" in str(e).lower() or "uq_market_prices" in str(e):
            print("Constraint already exists (OK)")
            return 0
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run())
    sys.exit(exit_code)
