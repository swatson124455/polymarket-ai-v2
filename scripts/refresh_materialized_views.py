"""
Refresh materialized views (#30) for fast dashboards.

Run on a schedule (e.g. cron every 5–15 min):
  python -m scripts.refresh_materialized_views
"""
import asyncio
import sys
from pathlib import Path

# Ensure project root on path
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


async def main() -> int:
    from base_engine.data.database import Database
    from config.settings import settings
    db = Database()
    try:
        await db.init()
    except Exception as e:
        print(f"Database init failed: {e}", file=sys.stderr)
        return 1
    ok = await db.refresh_materialized_view_market_stats()
    await db.close()
    print("refresh_materialized_view_market_stats:", "ok" if ok else "failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
