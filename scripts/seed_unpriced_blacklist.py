#!/usr/bin/env python3
"""S168: Seed known permanently-unpriced tokens into Redis blacklist.

Usage: PYTHONPATH=/opt/polymarket-ai-v2 python3 scripts/seed_unpriced_blacklist.py
"""
import asyncio
import os

KNOWN_UNPRICED_TOKENS = [
    "2104889894785831",
    "2950043368161202",
    "1030086607624379",
    "1706083069364246",
    "8401327308972776",
]

REDIS_KEY_PREFIX = "unpriced_blacklist:"


async def main():
    import redis.asyncio as aioredis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = aioredis.from_url(redis_url)

    for token_id in KNOWN_UNPRICED_TOKENS:
        await r.set(f"{REDIS_KEY_PREFIX}{token_id}", "1")
        print(f"  Blacklisted: {token_id}")

    cursor = 0
    count = 0
    while True:
        cursor, keys = await r.scan(cursor, match=f"{REDIS_KEY_PREFIX}*", count=100)
        count += len(keys)
        if cursor == 0:
            break
    print(f"\nTotal blacklisted tokens in Redis: {count}")
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
