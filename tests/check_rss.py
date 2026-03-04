"""Verify every RSS feed currently in news_sources.py is reachable."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from base_engine.signals.news_sources import RSS_FEEDS

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


async def main():
    ok = fail = 0
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": UA}) as c:
        for url in RSS_FEEDS:
            try:
                r = await c.get(url)
                if r.status_code < 400:
                    ok += 1
                    print(f"OK  {r.status_code} {len(r.content):>8,}b  {url}")
                else:
                    fail += 1
                    print(f"BAD {r.status_code}           {url}")
            except Exception as e:
                fail += 1
                print(f"ERR             {url[:60]}  -- {str(e)[:50]}")

    print(f"\nTOTAL: {ok} OK / {fail} failed out of {ok+fail} RSS feeds")


if __name__ == "__main__":
    asyncio.run(main())
