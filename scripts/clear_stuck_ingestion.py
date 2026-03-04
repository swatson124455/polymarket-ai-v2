"""
Clear stuck 'running' entries in sync_log so Pull data / backfill can run again.
Use when you see: "Full ingestion already in progress (check sync_log for running entry)".

Usage:
  python scripts/clear_stuck_ingestion.py           # Clear full + backfill running
  python scripts/clear_stuck_ingestion.py --full    # Clear only full ingestion
  python scripts/clear_stuck_ingestion.py --backfill  # Clear only backfill
"""
import argparse
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Clear stuck sync_log 'running' entries")
    parser.add_argument("--full", action="store_true", help="Clear only full ingestion")
    parser.add_argument("--backfill", action="store_true", help="Clear only backfill")
    args = parser.parse_args()

    from base_engine.data.database import Database

    db = Database()
    await db.init()
    try:
        if args.full:
            n = await db.clear_stuck_sync_running(component="data_ingestion", sync_type="full")
            print(f"Cleared {n} stuck 'full' ingestion run(s).")
        elif args.backfill:
            n = await db.clear_stuck_sync_running(component="data_ingestion", sync_type="backfill")
            print(f"Cleared {n} stuck 'backfill' run(s).")
        else:
            n_full = await db.clear_stuck_sync_running(component="data_ingestion", sync_type="full")
            n_bf = await db.clear_stuck_sync_running(component="data_ingestion", sync_type="backfill")
            print(f"Cleared {n_full} full + {n_bf} backfill stuck run(s).")
            n = n_full + n_bf
        return 0 if n >= 0 else 1
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
