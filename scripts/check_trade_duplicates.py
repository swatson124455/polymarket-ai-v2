"""
Check for duplicate trades (same market_id, user_address, token_id, timestamp). PD3.
Run before adding unique constraint (migration 010). Use --fix to delete duplicates (keeps one per group).
"""
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

from sqlalchemy import text
from structlog import get_logger

logger = get_logger()


async def check_duplicates(db) -> dict:
    """Return count of duplicate groups and total duplicate rows."""
    if not db or not getattr(db, "session_factory", None):
        return {"duplicate_groups": 0, "duplicate_rows": 0, "error": "no_db"}
    async with db.get_session() as session:
        r = await session.execute(text("""
            SELECT COUNT(*)::int AS groups, SUM(cnt - 1)::int AS extra_rows
            FROM (
                SELECT market_id, user_address, token_id, timestamp, COUNT(*) AS cnt
                FROM trades
                WHERE market_id IS NOT NULL AND user_address IS NOT NULL AND token_id IS NOT NULL AND timestamp IS NOT NULL
                GROUP BY market_id, user_address, token_id, timestamp
                HAVING COUNT(*) > 1
            ) sub
        """))
        row = r.one_or_none()
        if not row or (row[0] or 0) == 0:
            return {"duplicate_groups": 0, "duplicate_rows": 0}
        return {"duplicate_groups": row[0] or 0, "duplicate_rows": row[1] or 0}


async def fix_duplicates(db) -> int:
    """Delete duplicate trades, keeping one row per (market_id, user_address, token_id, timestamp). Returns deleted count."""
    if not db or not getattr(db, "session_factory", None):
        return 0
    async with db.get_session() as session:
        # Delete rows whose id is not the minimum id in each (market_id, user_address, token_id, timestamp) group
        r = await session.execute(text("""
            DELETE FROM trades
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY market_id, user_address, token_id, timestamp
                        ORDER BY id
                    ) AS rn
                    FROM trades
                    WHERE market_id IS NOT NULL AND user_address IS NOT NULL AND token_id IS NOT NULL AND timestamp IS NOT NULL
                ) sub
                WHERE sub.rn > 1
            )
        """))
        deleted = getattr(r, "rowcount", 0) or 0
        await session.commit()
        return deleted


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Check or fix duplicate trades (PD3)")
    ap.add_argument("--fix", action="store_true", help="Delete duplicates (keep one per group)")
    args = ap.parse_args()
    from base_engine.data.database import Database
    db = Database()

    async def _run():
        await db.init()
        info = await check_duplicates(db)
        print("Duplicate check:", info)
        if info.get("duplicate_rows", 0) > 0 and args.fix:
            deleted = await fix_duplicates(db)
            print("Deleted duplicate rows:", deleted)
        await db.close()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
