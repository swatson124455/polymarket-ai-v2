"""
Standalone bulk price backfill for ALL markets missing price data.
Bypasses the scheduler entirely — fetches directly from CLOB API.
Stores prices under m.id (matching historical ingestion format).

Usage: python scripts/backfill_all_prices.py [--days 30] [--concurrent 10] [--batch 500]
"""
import asyncio
import io
import os
import sys
import time
import argparse
from datetime import datetime, timezone, timedelta

os.environ["SIMULATION_MODE"] = "true"
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    parser = argparse.ArgumentParser(description="Backfill prices for all markets missing price data")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch (default: 30)")
    parser.add_argument("--concurrent", type=int, default=10, help="Max concurrent API fetches (default: 10)")
    parser.add_argument("--batch", type=int, default=200, help="DB insert batch size (default: 200)")
    parser.add_argument("--limit", type=int, default=0, help="Max markets to process (0=all)")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between API calls in seconds")
    args = parser.parse_args()

    from base_engine.data.database import Database, _naive_utc
    from base_engine.data.polymarket_client import PolymarketClient
    from sqlalchemy import text
    from config.settings import settings

    db = Database()
    await db.init()

    client = PolymarketClient()

    # 1. Find ALL active markets with tokens but NO price data
    async with db.get_session() as s:
        r = await s.execute(text("""
            SELECT m.id, m.condition_id, m.yes_token_id, m.no_token_id,
                   COALESCE(m.liquidity, 0) as liquidity,
                   LEFT(m.question, 60) as question
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
            ORDER BY COALESCE(m.liquidity, 0) DESC
        """))
        missing = r.fetchall()

    total = len(missing)
    if args.limit > 0:
        missing = missing[:args.limit]
        print(f"Processing {len(missing)} of {total} markets (limited by --limit)")
    else:
        print(f"Processing ALL {total} markets with no price data")

    if total == 0:
        print("No markets need price backfill!")
        await db.close()
        return

    # 2. Set up time range
    to_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - (args.days * 24 * 60 * 60)
    print(f"Fetching {args.days} days of history ({datetime.fromtimestamp(from_ts, tz=timezone.utc).strftime('%Y-%m-%d')} to now)")
    print(f"Concurrency: {args.concurrent}, Delay: {args.delay}s, Batch size: {args.batch}")
    print(f"{'='*80}")

    semaphore = asyncio.Semaphore(args.concurrent)
    stats = {
        "processed": 0, "success": 0, "empty": 0, "failed": 0,
        "total_points": 0, "db_inserted": 0,
    }
    all_price_data = []
    t_start = time.time()

    async def fetch_one_token(market_id: str, token_id: str, side: str):
        """Fetch price history for a single token."""
        async with semaphore:
            try:
                # Try interval=max first (single API call)
                resp = await client.get_price_history(token_id=token_id, interval="max")
                if resp and isinstance(resp, dict):
                    history = resp.get("history") or []
                    if isinstance(history, list) and len(history) > 0:
                        filtered = [p for p in history if isinstance(p, dict) and from_ts <= p.get("t", 0) <= to_ts]
                        if filtered:
                            return (market_id, token_id, side, filtered)

                # Fallback: chunked 30-day windows
                await asyncio.sleep(args.delay)
                out = []
                w_start = from_ts
                while w_start < to_ts:
                    w_end = min(w_start + 30 * 86400, to_ts)
                    for attempt in range(3):
                        try:
                            resp = await client.get_price_history(
                                token_id=token_id, start_ts=w_start, end_ts=w_end, interval="1h"
                            )
                            if resp and isinstance(resp, dict):
                                h = resp.get("history") or []
                                if isinstance(h, list):
                                    out.extend(h)
                            break
                        except Exception:
                            if attempt < 2:
                                await asyncio.sleep(1.0 * (2 ** attempt))
                    await asyncio.sleep(args.delay)
                    w_start = w_end
                return (market_id, token_id, side, out)
            except Exception as e:
                return (market_id, token_id, side, [], str(e))

    async def flush_prices(price_batch):
        """Bulk insert a batch of prices to DB."""
        if not price_batch or not db.session_factory:
            return 0
        try:
            inserted = await db.bulk_insert_prices_raw(price_batch, batch_size=args.batch)
            return inserted or len(price_batch)
        except Exception as e:
            print(f"  DB insert failed ({len(price_batch)} rows): {e}")
            return 0

    # 3. Process all markets
    for i, row in enumerate(missing):
        market_id = str(row[0])
        condition_id = str(row[1]) if row[1] else ""
        yes_token = str(row[2]) if row[2] else ""
        no_token = str(row[3]) if row[3] else ""
        liquidity = float(row[4])
        question = (row[5] or "").encode("ascii", "replace").decode("ascii")

        # Fetch both tokens concurrently
        tasks = []
        if yes_token.strip():
            tasks.append(fetch_one_token(market_id, yes_token, "YES"))
        if no_token.strip():
            tasks.append(fetch_one_token(market_id, no_token, "NO"))

        if not tasks:
            continue

        results = await asyncio.gather(*tasks, return_exceptions=True)
        market_points = 0

        for r in results:
            if isinstance(r, Exception):
                stats["failed"] += 1
                continue
            if len(r) == 5:  # error tuple
                stats["failed"] += 1
                continue
            _mid, _tid, _side, history = r
            for point in history:
                t_ts = point.get("t")
                p_val = point.get("p")
                if t_ts is None or p_val is None:
                    continue
                try:
                    ts_dt = datetime.fromtimestamp(t_ts, tz=timezone.utc)
                    all_price_data.append({
                        "market_id": _mid,  # Store under m.id (consistent with historical ingestion)
                        "token_id": _tid,
                        "price": float(p_val),
                        "timestamp": _naive_utc(ts_dt),
                        "side": _side,
                    })
                    market_points += 1
                except (ValueError, TypeError):
                    continue

        stats["processed"] += 1
        stats["total_points"] += market_points
        if market_points > 0:
            stats["success"] += 1
        else:
            stats["empty"] += 1

        # Flush every 2000 points to avoid memory bloat
        if len(all_price_data) >= 2000:
            inserted = await flush_prices(all_price_data)
            stats["db_inserted"] += inserted
            all_price_data.clear()

        # Progress every 50 markets or on high-value ones
        if (i + 1) % 50 == 0 or liquidity >= 100000:
            elapsed = time.time() - t_start
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            eta = (len(missing) - stats["processed"]) / rate if rate > 0 else 0
            print(
                f"[{stats['processed']:4d}/{len(missing)}] "
                f"ok={stats['success']} empty={stats['empty']} fail={stats['failed']} "
                f"points={stats['total_points']:,} db={stats['db_inserted']:,} "
                f"rate={rate:.1f}/s ETA={eta/60:.0f}m "
                f"| liq=${liquidity:,.0f} {question[:40]}"
            )

    # Final flush
    if all_price_data:
        inserted = await flush_prices(all_price_data)
        stats["db_inserted"] += inserted
        all_price_data.clear()

    elapsed = time.time() - t_start
    print(f"\n{'='*80}")
    print(f"BACKFILL COMPLETE in {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"  Markets processed: {stats['processed']}")
    print(f"  Success (got data): {stats['success']}")
    print(f"  Empty (no history): {stats['empty']}")
    print(f"  Failed (API error): {stats['failed']}")
    print(f"  Total price points: {stats['total_points']:,}")
    print(f"  DB rows inserted:   {stats['db_inserted']:,}")
    print(f"{'='*80}")

    # 4. Final count — how many markets now have price data?
    async with db.get_session() as s:
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON (mp.market_id = m.id OR mp.market_id = m.condition_id) "
            "WHERE m.active = true AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)"
        ))
        print(f"\n  Active markets with price data NOW: {r.scalar()}")

        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON (mp.market_id = m.id OR mp.market_id = m.condition_id) "
            "WHERE m.active = true AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL) "
            "AND COALESCE(m.liquidity, 0) >= 100"
        ))
        print(f"  Scannable (liq >= $100): {r.scalar()}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
