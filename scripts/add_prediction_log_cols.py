"""Add missing columns to prediction_log table."""
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
        # Add ensemble_pred if missing
        try:
            await session.execute(text("ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS ensemble_pred FLOAT"))
            print("Added ensemble_pred column")
        except Exception as e:
            print(f"ensemble_pred: {e}")

        # Add learning_conf if missing
        try:
            await session.execute(text("ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS learning_conf FLOAT"))
            print("Added learning_conf column")
        except Exception as e:
            print(f"learning_conf: {e}")

        await session.commit()
        print("Done")

    await db.close()

asyncio.run(main())
