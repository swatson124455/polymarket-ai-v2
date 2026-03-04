#!/usr/bin/env python3
"""
Load test: measure bulk_insert_trades throughput (trades/sec, batch timing).
Run from project root: python scripts/load_test_ingestion.py [--trades N] [--batch B]
"""
import argparse
import asyncio
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


def _naive_utc(dt: datetime) -> datetime:
    if getattr(dt, "tzinfo", None):
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def generate_fake_trade(market_id: str, index: int) -> dict:
    """Generate a single trade dict matching schema and validation."""
    ts = _naive_utc(datetime.now(timezone.utc))
    return {
        "id": f"loadtest-{market_id}-{index}-{ts.timestamp()}",
        "market_id": market_id,
        "token_id": f"token_{index % 2}",
        "user_address": "0x" + "0" * 39 + "1",
        "side": "YES" if index % 2 else "NO",
        "size": max(0.01, random.random() * 100),
        "price": random.random(),
        "timestamp": ts,
    }


async def run_load_test(
    num_trades: int = 10_000,
    batch_size: int = 1000,
    market_id: str = "loadtest-market",
) -> None:
    """Run load test: generate trades, bulk_insert in batches, report throughput."""
    from base_engine.data.database import Database

    db = Database()
    await db.init()
    if not db.session_factory:
        print("Error: Database not initialized (check DATABASE_URL)")
        sys.exit(1)

    print("=" * 60)
    print("Load test: bulk_insert_trades")
    print(f"  Total trades: {num_trades:,}")
    print(f"  Batch size:   {batch_size}")
    print(f"  Market ID:    {market_id}")
    print("=" * 60)

    all_trades = [generate_fake_trade(market_id, i) for i in range(num_trades)]
    batches = [all_trades[i : i + batch_size] for i in range(0, len(all_trades), batch_size)]
    print(f"  Batches:      {len(batches)}")

    start = time.perf_counter()
    durations = []
    for i, batch in enumerate(batches):
        t0 = time.perf_counter()
        try:
            await db.bulk_insert_trades(batch)
            elapsed = time.perf_counter() - t0
            durations.append(elapsed)
            print(f"  Batch {i + 1}/{len(batches)}: {len(batch)} trades in {elapsed:.2f}s")
        except Exception as e:
            print(f"  Batch {i + 1} FAILED: {e}")
    total_time = time.perf_counter() - start

    if not durations:
        print("No successful batches.")
        return
    throughput = num_trades / total_time
    print("=" * 60)
    print("Results:")
    print(f"  Total time:     {total_time:.2f}s")
    print(f"  Throughput:     {throughput:.2f} trades/sec")
    print(f"  Avg batch time: {sum(durations) / len(durations):.2f}s")
    print(f"  Min batch time: {min(durations):.2f}s")
    print(f"  Max batch time: {max(durations):.2f}s")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load test bulk_insert_trades")
    parser.add_argument("--trades", type=int, default=10_000, help="Total trades to insert")
    parser.add_argument("--batch", type=int, default=1000, help="Trades per batch")
    parser.add_argument("--market", type=str, default="loadtest-market", help="Fake market_id")
    args = parser.parse_args()
    asyncio.run(run_load_test(num_trades=args.trades, batch_size=args.batch, market_id=args.market))
    return 0


if __name__ == "__main__":
    sys.exit(main())
