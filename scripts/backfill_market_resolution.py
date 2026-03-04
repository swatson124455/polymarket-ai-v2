#!/usr/bin/env python3
"""
Backfill resolution for markets that have trades but no resolution.
Fetches each market from Gamma API and updates DB. Run after ingest.

Also fetches and inserts missing markets (trade market_ids not in DB) so trades
can be linked. Trades use condition_id/slug from Data API; markets use Gamma id.

Uses base_engine.data.resolution_backfill for shared logic (also used by IngestionScheduler).
"""
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


async def main():
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.resolution_backfill import run_resolution_backfill
    from base_engine.data.database_lock import acquire_lock

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized")
        return 1

    client = PolymarketClient()
    async with acquire_lock(db, "resolution_backfill"):
        result = await run_resolution_backfill(
            db, client,
            missing_limit=200,
            resolution_limit=500,
            log_progress=True,
        )

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return 1
    print(f"Backfill complete: {result.get('inserted', 0)} inserted, {result.get('updated', 0)} updated")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
