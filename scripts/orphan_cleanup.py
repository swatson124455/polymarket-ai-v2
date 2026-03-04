"""
Orphan cleanup: remove trades (and optionally market_prices) that reference missing markets.
Phase 3 (5.2). Run manually or enable RUN_ORPHAN_CLEANUP_AFTER_INGESTION for scheduler.

Usage:
  python scripts/orphan_cleanup.py [--dry-run] [--prices]
"""
import argparse
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

def main():
    ap = argparse.ArgumentParser(description="Remove orphan trades (and optionally prices)")
    ap.add_argument("--dry-run", action="store_true", help="Report only, do not delete")
    ap.add_argument("--prices", action="store_true", help="Also delete market_prices for missing markets")
    args = ap.parse_args()
    from base_engine.data.database import Database
    from base_engine.data.orphan_cleanup import run_orphan_cleanup
    db = Database()

    async def _run():
        await db.init()
        result = await run_orphan_cleanup(db, dry_run=args.dry_run, cleanup_prices=args.prices)
        await db.close()
        return result

    result = asyncio.run(_run())
    print("Orphan cleanup:", result)
    return 0 if result.get("error") is None else 1


if __name__ == "__main__":
    sys.exit(main())
