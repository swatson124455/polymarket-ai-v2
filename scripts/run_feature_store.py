"""
Run FeatureStore bulk compute once: pre-compute ML features for active markets.
Use from cron (e.g. hourly after ingestion) or manually.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from base_engine.base_engine import BaseEngine

    engine = BaseEngine()
    await engine.init()
    if not engine.feature_store:
        print(json.dumps({"success": False, "error": "FeatureStore not available (db missing)"}))
        return
    count = await engine.feature_store.bulk_compute_features(batch_size=10, limit_markets=500)
    print(json.dumps({"success": True, "computed": count}))
    await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
