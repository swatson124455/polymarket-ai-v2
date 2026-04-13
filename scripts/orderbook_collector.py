#!/usr/bin/env python3
"""
1J: Orderbook Collection — Polls best_bid/best_ask every 60s for active markets.

Designed to run as a systemd timer or cron job (every 60s).
Prioritizes markets by volume. Respects API rate limits.

Usage:
    python scripts/orderbook_collector.py              # Default: top 200 markets
    python scripts/orderbook_collector.py --limit 500  # Top 500 markets
    python scripts/orderbook_collector.py --once        # Single run (for cron)
"""
import asyncio
import argparse
import time
from datetime import datetime, timezone
from base_engine.data.database import Database
from base_engine.data.polymarket_client import PolymarketClient
from dotenv import load_dotenv
from structlog import get_logger

load_dotenv()
logger = get_logger()

# Max concurrent API calls to avoid rate limiting
MAX_CONCURRENT = 10
# Pause between batches (seconds)
BATCH_PAUSE = 0.5
POLL_INTERVAL = 60


async def collect_orderbooks(limit: int = 200):
    """Single collection pass: fetch orderbooks and persist snapshots."""
    db = Database()
    await db.init()
    client = PolymarketClient()

    try:
        # Get active markets with token IDs, sorted by volume
        async with db.get_session() as session:
            from sqlalchemy import text
            result = await session.execute(text("""
                SELECT m.id AS market_id, m.condition_id,
                       m.yes_token_id, m.no_token_id
                FROM markets m
                WHERE m.active = TRUE
                  AND m.resolved = FALSE
                  AND (m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
                ORDER BY COALESCE(m.volume, 0) DESC NULLS LAST
                LIMIT :limit
            """), {"limit": limit})
            markets = result.fetchall()

        if not markets:
            logger.info("orderbook_collector_no_markets")
            return 0

        # Build token list: (token_id, market_id, condition_id)
        tokens = []
        for m in markets:
            mid = m[0]
            cid = m[1] or mid
            if m[2]:
                tokens.append((m[2], mid, cid))
            if m[3]:
                tokens.append((m[3], mid, cid))

        logger.info("orderbook_collector_start", markets=len(markets), tokens=len(tokens))

        # Fetch in batches with concurrency limit
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        snapshots = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC for asyncpg

        async def fetch_one(token_id, market_id, condition_id):
            async with sem:
                try:
                    book = await client.get_orderbook(
                        market_id=condition_id,
                        token_id=token_id
                    )
                    if not book:
                        return None

                    bids = book.get("bids", [])
                    asks = book.get("asks", [])

                    if not bids and not asks:
                        return None

                    best_bid = float(bids[0]["price"]) if bids else None
                    best_ask = float(asks[0]["price"]) if asks else None

                    spread = None
                    mid = None
                    if best_bid is not None and best_ask is not None:
                        spread = best_ask - best_bid
                        mid = (best_bid + best_ask) / 2

                    # Depth within 1% and 5% of mid
                    bid_d1 = bid_d5 = ask_d1 = ask_d5 = 0.0
                    if mid and mid > 0:
                        for b in bids:
                            p, s = float(b["price"]), float(b["size"])
                            if abs(p - mid) <= mid * 0.01:
                                bid_d1 += s
                            if abs(p - mid) <= mid * 0.05:
                                bid_d5 += s
                        for a in asks:
                            p, s = float(a["price"]), float(a["size"])
                            if abs(p - mid) <= mid * 0.01:
                                ask_d1 += s
                            if abs(p - mid) <= mid * 0.05:
                                ask_d5 += s

                    # Imbalance (top 5 levels)
                    bv = sum(float(b.get("size", 0)) for b in bids[:5])
                    av = sum(float(a.get("size", 0)) for a in asks[:5])
                    imbalance = (bv - av) / (bv + av) if (bv + av) > 0 else 0.0

                    return {
                        "token_id": token_id,
                        "market_id": market_id,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "spread": spread,
                        "mid_price": mid,
                        "bid_depth_1pct": bid_d1,
                        "ask_depth_1pct": ask_d1,
                        "bid_depth_5pct": bid_d5,
                        "ask_depth_5pct": ask_d5,
                        "imbalance": round(imbalance, 4),
                        "snapshot_time": now,
                    }
                except Exception as e:
                    logger.debug("orderbook_fetch_error", token_id=token_id[:12], error=str(e))
                    return None

        # Process in batches
        for i in range(0, len(tokens), MAX_CONCURRENT):
            batch = tokens[i:i + MAX_CONCURRENT]
            results = await asyncio.gather(
                *[fetch_one(t, m, c) for t, m, c in batch]
            )
            snapshots.extend([r for r in results if r is not None])
            if i + MAX_CONCURRENT < len(tokens):
                await asyncio.sleep(BATCH_PAUSE)

        # Bulk insert
        if snapshots:
            async with db.get_session() as session:
                from sqlalchemy import text
                await session.execute(
                    text("""
                        INSERT INTO orderbook_snapshots
                            (token_id, market_id, best_bid, best_ask, spread, mid_price,
                             bid_depth_1pct, ask_depth_1pct, bid_depth_5pct, ask_depth_5pct,
                             imbalance, snapshot_time)
                        VALUES
                            (:token_id, :market_id, :best_bid, :best_ask, :spread, :mid_price,
                             :bid_depth_1pct, :ask_depth_1pct, :bid_depth_5pct, :ask_depth_5pct,
                             :imbalance, :snapshot_time)
                    """),
                    snapshots,
                )
                await session.commit()

        logger.info("orderbook_collector_done",
                     snapshots=len(snapshots),
                     tokens_polled=len(tokens),
                     elapsed_s=round(time.monotonic() - _start, 1))
        return len(snapshots)

    finally:
        await db.close()


async def run_loop(limit: int, once: bool):
    """Run collection in a loop or once."""
    global _start
    while True:
        _start = time.monotonic()
        try:
            n = await collect_orderbooks(limit=limit)
            logger.info("orderbook_collector_cycle", inserted=n)
        except Exception as e:
            logger.error("orderbook_collector_error", error=str(e))

        if once:
            break
        await asyncio.sleep(POLL_INTERVAL)


_start = time.monotonic()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orderbook snapshot collector")
    parser.add_argument("--limit", type=int, default=200, help="Max markets to poll")
    parser.add_argument("--once", action="store_true", help="Single run then exit")
    args = parser.parse_args()
    asyncio.run(run_loop(limit=args.limit, once=args.once))
