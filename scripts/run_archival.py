"""
Run data archival: archive market_prices older than MARKET_PRICES_RETENTION_DAYS.
Use from cron (e.g. weekly) or manually. See docs/PRICE_PULLING_AND_STORAGE_SWOT.md.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> dict:
    from base_engine.data.database import Database
    from base_engine.data.data_archival import DataArchival
    from config.settings import settings

    retention_days = getattr(settings, "MARKET_PRICES_RETENTION_DAYS", 730)
    if retention_days <= 0:
        return {"success": True, "skipped": True, "reason": "MARKET_PRICES_RETENTION_DAYS=0 (archival disabled)"}

    db = Database()
    await db.init()
    if not db.session_factory:
        return {"success": False, "error": "Database not available"}

    archival = DataArchival(
        days_to_keep_hot=7,
        days_to_keep_warm=retention_days,
        days_to_keep_cold=retention_days + 365,
    )
    async with db.session_factory() as session:
        stats = await archival.archive_old_data(session, "market_prices")
    return {"success": True, "stats": stats}


if __name__ == "__main__":
    result = asyncio.run(main())
    print(json.dumps(result, default=str))
    sys.exit(0 if result.get("success") else 1)
