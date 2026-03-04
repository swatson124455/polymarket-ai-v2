"""
Run AutoHealer once: detect failed syncs, data gaps, stale prices; apply fixes.
Use from cron (e.g. every 6 hours) or manually.
"""
import asyncio
import json
import os
import sys

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from base_engine.base_engine import BaseEngine
    from config.settings import settings

    engine = BaseEngine()
    await engine.init()
    if not engine.auto_healer:
        print(json.dumps({"success": False, "error": "AutoHealer not available (db/gap_detector/data_ingestion missing)"}))
        return
    fixes = await engine.auto_healer.auto_heal()
    print(json.dumps({"success": True, "fixes": fixes}))
    await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
