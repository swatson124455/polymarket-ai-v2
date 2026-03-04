#!/usr/bin/env python3
"""
Normalize trade market_id to canonical market id (m.id).
Trades may have market_id = condition_id or slug from Data API; this updates them to m.id.
Run after importing markets. Idempotent (already-normalized trades unchanged).
"""
import argparse
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


async def main(check_only: bool = False) -> int:
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized")
        return 1

    async with db.get_session() as session:
        if check_only:
            result = await session.execute(text("""
                SELECT COUNT(*) as cnt FROM trades t
                JOIN markets m ON (t.market_id = m.condition_id OR t.market_id = m.slug) AND t.market_id != m.id
                WHERE t.market_id IS NOT NULL
            """))
            row = result.scalar() or 0
            print(f"Would normalize {row} trade(s) to canonical market id")
            return 0
        # Update trades where market_id matches condition_id or slug to canonical m.id
        result = await session.execute(text("""
            UPDATE trades t
            SET market_id = m.id
            FROM markets m
            WHERE t.market_id IS NOT NULL
            AND (t.market_id = m.condition_id OR t.market_id = m.slug)
            AND t.market_id != m.id
        """))
        await session.commit()
        updated = result.rowcount if hasattr(result, "rowcount") else 0

    print(f"Normalized {updated} trade(s) to canonical market id")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalize trade market_id to canonical m.id")
    parser.add_argument("--check-only", action="store_true", help="Preview count without updating")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(check_only=args.check_only)))
