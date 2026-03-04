"""Verify Bluesky public API is reachable and returns posts."""
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base_engine.signals.social_sources import BlueSkyClient


async def main():
    client = BlueSkyClient()
    try:
        posts = await client.search_posts("polymarket OR prediction market", limit=10)
        print(f"Bluesky: fetched {len(posts)} posts")
        for p in posts[:3]:
            print(f"  @{p['author']}: {p['text'][:80]}")
        if posts:
            print("STATUS: OK - Bluesky is working")
        else:
            print("STATUS: No posts returned (may be rate-limited or query too narrow)")
    except Exception as e:
        print(f"STATUS: FAIL - {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
