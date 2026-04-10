#!/usr/bin/env python3
"""
Resolution Verification — Compare DB resolutions against Polymarket API.

Usage:
    python scripts/verify_resolutions.py              # All resolved markets with trades
    python scripts/verify_resolutions.py --limit 100  # First 100

Fetches each resolved market from Polymarket Gamma API and compares the
resolution outcome against what's stored in the DB. Reports mismatches.

Rate limiting: 5 concurrent requests, 0.5s delay between batches.
Exponential backoff on 429 responses (2s, 4s, 8s, 16s, cap 60s).

S169: Data quality verification pipeline.
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

from base_engine.data.database import Database
from base_engine.data.polymarket_client import PolymarketClient


BATCH_SIZE = 5
BATCH_DELAY = 0.5
MAX_BACKOFF = 60


async def verify_resolutions(limit: int = 500):
    db = Database()
    await db.init()
    client = PolymarketClient()

    async with db.get_session() as s:
        from sqlalchemy import text

        # Get resolved markets that have trade_events
        result = await s.execute(text("""
            SELECT DISTINCT m.id, m.condition_id, m.resolution, m.resolved_at
            FROM markets m
            JOIN trade_events te ON CAST(m.id AS TEXT) = te.market_id
            WHERE m.resolved = TRUE
              AND m.resolution IN ('YES', 'NO')
            ORDER BY m.resolved_at DESC NULLS LAST
            LIMIT :limit
        """), {"limit": limit})
        markets = result.fetchall()

    print(f"Verifying {len(markets)} resolved markets against Polymarket API...")
    print(f"Rate: {BATCH_SIZE} concurrent, {BATCH_DELAY}s between batches\n")

    mismatches = []
    errors = []
    verified = 0
    backoff = 2

    for i in range(0, len(markets), BATCH_SIZE):
        batch = markets[i:i + BATCH_SIZE]
        tasks = [_check_one(client, m) for m in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for market_row, result in zip(batch, results):
            market_id, condition_id, db_resolution, resolved_at = market_row

            if isinstance(result, Exception):
                err_str = str(result)
                if "429" in err_str:
                    # Rate limited — exponential backoff
                    print(f"  429 rate limited. Backing off {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    errors.append({"market_id": str(market_id), "error": "rate_limited"})
                else:
                    errors.append({"market_id": str(market_id), "error": err_str[:100]})
                continue

            # Reset backoff on success
            backoff = 2

            if result is None:
                errors.append({"market_id": str(market_id), "error": "market_not_found_in_api"})
                continue

            api_resolution = result.get("resolution") or result.get("outcome")
            verified += 1

            if api_resolution and api_resolution != db_resolution:
                mismatches.append({
                    "market_id": str(market_id),
                    "condition_id": condition_id,
                    "db_resolution": db_resolution,
                    "api_resolution": api_resolution,
                    "resolved_at": str(resolved_at),
                })
                print(f"  MISMATCH: {str(market_id)[:16]}.. DB={db_resolution} API={api_resolution}")

        # Progress
        done = min(i + BATCH_SIZE, len(markets))
        if done % 50 == 0 or done == len(markets):
            print(f"  Progress: {done}/{len(markets)} checked, {len(mismatches)} mismatches, {len(errors)} errors")

        if i + BATCH_SIZE < len(markets):
            await asyncio.sleep(BATCH_DELAY)

    # Summary
    print(f"\n{'=' * 60}")
    print("RESOLUTION VERIFICATION SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Markets checked:  {len(markets)}")
    print(f"  Verified OK:      {verified - len(mismatches)}")
    print(f"  Mismatches:       {len(mismatches)}")
    print(f"  API errors:       {len(errors)}")

    if mismatches:
        print(f"\nMISMATCHES:")
        print(f"  {'Market':<18} {'DB':>5} {'API':>5} {'Resolved At'}")
        print(f"  {'-' * 55}")
        for m in mismatches:
            mid = m["market_id"][:16] + ".."
            print(f"  {mid:<18} {m['db_resolution']:>5} {m['api_resolution']:>5} {m['resolved_at'][:19]}")

    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors[:20]:
            print(f"  {e['market_id'][:16]}.. — {e['error']}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    await db.close()


async def _check_one(client: PolymarketClient, market_row) -> dict:
    """Fetch a single market from API. Returns market dict or raises."""
    market_id, condition_id, _, _ = market_row
    # Try condition_id first (more reliable), fall back to market id
    lookup_id = condition_id or str(market_id)
    return await client.get_market(lookup_id, use_cache=False)


if __name__ == "__main__":
    limit = 500
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--limit" and i < len(sys.argv) - 1:
            limit = int(sys.argv[i + 1])

    asyncio.run(verify_resolutions(limit))
