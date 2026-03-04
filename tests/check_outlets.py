"""Quick connectivity check for all free signal outlets."""
import asyncio
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

TESTS = [
    ("BBC World RSS",         "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Business RSS",      "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC Politics RSS",      "https://feeds.bbci.co.uk/news/politics/rss.xml"),
    ("NYT World RSS",         "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("NYT Politics RSS",      "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
    ("NYT Business RSS",      "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Reuters Top News",      "https://feeds.reuters.com/reuters/topNews"),
    ("Reuters Business",      "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters US Politics",   "https://feeds.reuters.com/reuters/USPoliticsNews"),
    ("NPR World",             "https://www.npr.org/rss/rss.php?id=1001"),
    ("NPR Business",          "https://www.npr.org/rss/rss.php?id=1006"),
    ("Guardian World",        "https://www.theguardian.com/world/rss"),
    ("Guardian Business",     "https://www.theguardian.com/business/rss"),
    ("Guardian US",           "https://www.theguardian.com/us/rss"),
    ("Politico (main)",       "https://www.politico.com/rss/politics08.xml"),
    ("Politico (alt)",        "https://rss.politico.com/politics-news.xml"),
    ("Reddit Polymarket",     "https://www.reddit.com/r/polymarket/.rss"),
    ("Reddit PredictIt",      "https://www.reddit.com/r/predictit/.rss"),
    ("Reddit WSB",            "https://www.reddit.com/r/wallstreetbets/.rss"),
    ("HackerNews Algolia",    "https://hn.algolia.com/api/v1/search?query=bitcoin&tags=story&hitsPerPage=5"),
    ("GDELT 15min",           "https://api.gdeltproject.org/api/v2/doc/doc?query=election&mode=artlist&maxrecords=5&format=json&timespan=15min"),
    ("Wikipedia REST",        "https://en.wikipedia.org/api/rest_v1/page/summary/Bitcoin"),
    ("Wikipedia Pageviews",   "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/Bitcoin/daily/20260201/20260210"),
    ("NOAA Weather",          "https://api.weather.gov/"),
]


async def main():
    ok_list = []
    fail_list = []
    async with httpx.AsyncClient(
        timeout=12.0,
        follow_redirects=True,
        headers={"User-Agent": UA},
    ) as client:
        for name, url in TESTS:
            try:
                r = await client.get(url)
                if r.status_code < 400:
                    ok_list.append((name, r.status_code, len(r.content)))
                else:
                    fail_list.append((name, r.status_code, url))
            except Exception as exc:
                fail_list.append((name, "ERR", str(exc)[:80]))

    print("\n=== Signal Outlet Health Check ===\n")
    print("WORKING:")
    for name, code, size in ok_list:
        print(f"  [OK ] {name:<30} HTTP {code}  ({size:,} bytes)")

    print("\nFAILING / BLOCKED:")
    for name, code, info in fail_list:
        print(f"  [!  ] {name:<30} {code}  {info}")

    print(f"\nSummary: {len(ok_list)} OK, {len(fail_list)} failed out of {len(TESTS)} outlets")


if __name__ == "__main__":
    asyncio.run(main())
