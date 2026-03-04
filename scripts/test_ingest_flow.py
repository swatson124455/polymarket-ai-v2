#!/usr/bin/env python3
"""Test ingest flow: get_elite_traders, ingest_top_users, update_elite_status. Verifies is_likely_market_maker fix."""
import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / ".env")


async def main():
    from base_engine.data.database import Database
    from base_engine.learning.elite_detector import EliteUserDetector

    db = Database()
    await db.init()
    if not db.session_factory:
        print("SKIP: Database not initialized (no DATABASE_URL)")
        return 0

    errors = []
    try:
        # 1. get_elite_traders (fixed to not require is_likely_market_maker)
        traders = await db.get_elite_traders(limit=5)
        print(f"OK get_elite_traders: {len(traders)} traders")
    except Exception as e:
        errors.append(f"get_elite_traders: {e}")
        print(f"FAIL get_elite_traders: {e}")

    try:
        # 2. update_elite_status (needs is_likely_market_maker column for _update_market_maker_flags)
        detector = EliteUserDetector(db)
        await detector.update_elite_status()
        print("OK update_elite_status")
    except Exception as e:
        errors.append(f"update_elite_status: {e}")
        print(f"FAIL update_elite_status: {e}")

    # 3. Full ingest flow (optional; requires API; skip to avoid slow/timeout)
    # Run manually: python scripts/test_ingest_flow.py --full
    if "--full" in sys.argv:
        try:
            from base_engine.base_engine import BaseEngine
            engine = BaseEngine()
            await engine.init()
            if engine.data_ingestion and engine.db and engine.db.session_factory:
                n_users = await engine.data_ingestion.ingest_top_users()
                print(f"OK ingest_top_users: {n_users} users")
                n_activity = await engine.data_ingestion.ingest_elite_trader_activity()
                print(f"OK ingest_elite_trader_activity: {n_activity} trades")
                if engine.elite_detector:
                    await engine.elite_detector.update_elite_status()
                    print("OK update_elite_status (post-ingest)")
            else:
                print("SKIP full ingest: engine not fully initialized")
        except Exception as e:
            err_str = str(e).lower()
            if "connection" in err_str or "timeout" in err_str or "forbidden" in err_str:
                print(f"SKIP full ingest: API unavailable ({e})")
            else:
                errors.append(f"full ingest: {e}")
                print(f"FAIL full ingest: {e}")
    else:
        print("OK core ingest flow (get_elite_traders, update_elite_status)")
        print("Run with --full to test API ingest (ingest_top_users, ingest_elite_trader_activity)")

    await db.close()

    if errors:
        print("\nFAILED:", errors)
        return 1
    print("\nAll ingest flow checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
