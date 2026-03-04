"""Debug Bluesky API endpoints."""
import asyncio
import httpx

async def main():
    urls = [
        ("public api search", "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=polymarket&limit=5"),
        ("bsky api search", "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=polymarket&limit=5"),
        ("describe server", "https://public.api.bsky.app/xrpc/com.atproto.server.describeServer"),
        ("search no auth", "https://bsky.social/xrpc/app.bsky.feed.searchPosts?q=polymarket&limit=5"),
    ]
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
        for name, url in urls:
            try:
                r = await c.get(url, headers={"User-Agent": "polymarket-ai/2.0"})
                print(f"{name}: HTTP {r.status_code} {len(r.content)}b  {r.text[:120]}")
            except Exception as e:
                print(f"{name}: ERR {str(e)[:60]}")

asyncio.run(main())
