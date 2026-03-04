"""Test Reddit old.reddit.com RSS with proper User-Agent."""
import asyncio
import httpx

REDDIT_UA = "python:polymarket-ai-bot:v2.0 (by /u/polymarket_ai_research)"

async def main():
    tests = [
        ("old.reddit predictit RSS", "https://old.reddit.com/r/predictit/.rss"),
        ("old.reddit polymarket RSS", "https://old.reddit.com/r/polymarket/.rss"),
        ("new.reddit predictit (Mozilla UA)", "https://www.reddit.com/r/predictit/.rss"),
        ("Reddit JSON predictit", "https://www.reddit.com/r/predictit.json"),
    ]
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as c:
        for name, url in tests:
            for ua_label, ua in [("Reddit UA", REDDIT_UA), ("Mozilla UA", "Mozilla/5.0 (Windows NT 10.0)")]:
                try:
                    r = await c.get(url, headers={"User-Agent": ua})
                    tag = "OK" if r.status_code < 400 else "FAIL"
                    print(f"[{tag}] {name} [{ua_label}] HTTP {r.status_code} {len(r.content):,}b")
                    if r.status_code < 400:
                        break
                except Exception as e:
                    print(f"[ERR] {name}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
