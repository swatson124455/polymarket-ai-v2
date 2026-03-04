"""Test parallel scan performance with prefetch + caching.

Measures time for the EnsembleBot scan loop to complete one cycle.
"""
import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base_engine.data.database import Database
from base_engine.prediction.prediction_engine import PredictionEngine
from base_engine.learning.learning_engine import LearningEngine
from config.settings import settings


async def test_scan():
    print("=== Parallel Scan Performance Test ===\n")

    # Init DB
    db = Database()
    await db.init()
    _eng = getattr(db, "engine", None) or getattr(db, "_engine", None)
    if _eng:
        print(f"DB pool: pool_size={_eng.pool.size()}, overflow={_eng.pool.overflow()}")
    else:
        print("DB engine attribute not found")

    # Init prediction engine
    le = LearningEngine(db)
    pe = PredictionEngine(db, le)
    await pe.init()

    if not pe.models:
        print("ERROR: No models loaded. Cannot test predictions.")
        await db.close()
        return

    print(f"Models loaded: {len(pe.models)} models, {len(pe.feature_columns)} features")

    # Get tradeable markets
    from sqlalchemy import text
    async with db.get_session() as session:
        r = await session.execute(text("""
            SELECT m.id, m.question, m.condition_id, m.yes_token_id, m.no_token_id,
                   m.liquidity, m.volume, m.category
            FROM markets m
            WHERE m.active = TRUE
            AND CAST(m.liquidity AS FLOAT) >= 100
            LIMIT 100
        """))
        rows = r.fetchall()

    print(f"Fetched {len(rows)} tradeable markets\n")

    if not rows:
        print("No tradeable markets found.")
        await db.close()
        return

    market_ids = [str(row[0]) for row in rows]

    # Test 1: Prefetch
    t0 = time.perf_counter()
    loaded = await pe.prefetch_markets(market_ids)
    t_prefetch = time.perf_counter() - t0
    print(f"Prefetch: {loaded} markets in {t_prefetch:.2f}s")

    # Test 2: Sequential extraction (10 markets)
    sample = market_ids[:10]
    t0 = time.perf_counter()
    for mid in sample:
        try:
            feats = await pe._extract_features(mid, 0.5, None)
        except Exception as e:
            print(f"  Market {mid}: ERROR {e}")
    t_seq = time.perf_counter() - t0
    print(f"Sequential (10 markets): {t_seq:.2f}s ({t_seq/10*1000:.0f}ms/market)")

    # Test 3: Parallel extraction (10 markets, concurrency=5 to match ENSEMBLE_SCAN_CONCURRENCY)
    sem = asyncio.Semaphore(5)

    async def _one(mid):
        async with sem:
            return await pe._extract_features(mid, 0.5, None)

    t0 = time.perf_counter()
    tasks = [_one(mid) for mid in sample]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    t_par = time.perf_counter() - t0
    ok = sum(1 for r in results if not isinstance(r, Exception))
    errs = sum(1 for r in results if isinstance(r, Exception))
    print(f"Parallel  (10 markets, concurrency=5): {t_par:.2f}s ({ok} ok, {errs} errors)")

    # Test 4: Parallel with warm cache (second pass - should be instant)
    t0 = time.perf_counter()
    tasks2 = [_one(mid) for mid in sample]
    results2 = await asyncio.gather(*tasks2, return_exceptions=True)
    t_warm = time.perf_counter() - t0
    ok2 = sum(1 for r in results2 if not isinstance(r, Exception))
    print(f"Warm cache (10 markets, concurrency=5): {t_warm:.2f}s ({ok2} ok)")

    # Test 5: Full 100-market parallel scan
    t0 = time.perf_counter()
    all_tasks = [_one(mid) for mid in market_ids]
    all_results = await asyncio.gather(*all_tasks, return_exceptions=True)
    t_full = time.perf_counter() - t0
    ok_full = sum(1 for r in all_results if not isinstance(r, Exception))
    err_full = sum(1 for r in all_results if isinstance(r, Exception))
    print(f"\nFull 100-market parallel scan: {t_full:.2f}s ({ok_full} ok, {err_full} errors)")
    print(f"Projected 800-market scan: ~{t_full * 8:.0f}s (cold) or ~{t_warm * 80:.0f}s (warm)")

    # Print error samples
    for r in all_results:
        if isinstance(r, Exception):
            print(f"  Error sample: {r}")
            break

    await db.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(test_scan())
