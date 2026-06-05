"""
News Sources - Integrate with NewsAPI and other news sources.

Sources:
- NewsAPI (requires API key)
- RSS feeds (free, no API key)
- Government feeds
- PR wire services
"""
import asyncio
import httpx
import feedparser
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()

# Free RSS feeds - no API key needed. Polymarket-relevant: politics, crypto, Fed, elections.
# Last verified: 2026-02-21. Reuters feeds.reuters.com is DNS-dead; replaced with
# Reuters via Yahoo Finance and AP News. Politico main RSS returns 403; use rss.politico.com.
# Reddit PredictIt returns 403 with default UA; removed (polymarket + wsb still work).
RSS_FEEDS = [
    # BBC (3 feeds — reliable, no rate limits)
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/politics/rss.xml",
    # NYT (3 feeds — verified OK)
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    # AP News — replaces dead Reuters feeds.reuters.com domain (DNS dead as of 2026-02)
    # Using hub RSS format (verified OK 2026-02-21)
    "https://apnews.com/hub/ap-top-news?format=rss",   # AP Top News
    "https://apnews.com/hub/business?format=rss",       # AP Business
    "https://apnews.com/hub/politics?format=rss",       # AP Politics
    # CBS News (verified OK)
    "https://www.cbsnews.com/latest/rss/main",
    # ABC News
    "https://abcnews.go.com/abcnews/topstories",
    # NPR (2 feeds — verified OK)
    "https://www.npr.org/rss/rss.php?id=1001",   # World
    "https://www.npr.org/rss/rss.php?id=1006",   # Business
    # Politico — main URL 403; use rss.politico.com subdomain (verified OK)
    "https://rss.politico.com/politics-news.xml",
    # Guardian (3 feeds — verified OK)
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/business/rss",
    "https://www.theguardian.com/us/rss",
    # Reddit (no API key) - Polymarket/prediction markets
    "https://www.reddit.com/r/polymarket/.rss",
    "https://www.reddit.com/r/wallstreetbets/.rss",
    # Reddit PredictIt returns 403 with bot UA — removed
]


class NewsAPIClient:
    """Client for NewsAPI integration."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(settings, 'NEWSAPI_KEY', None)
        self.base_url = "https://newsapi.org/v2"
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def fetch_news(
        self,
        query: Optional[str] = None,
        sources: Optional[List[str]] = None,
        language: str = "en",
        sort_by: str = "publishedAt",
        page_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch news articles.
        
        Args:
            query: Search query
            sources: List of source IDs
            language: Language code
            sort_by: Sort order (publishedAt, popularity, relevancy)
            page_size: Number of articles (max 100)
        
        Returns:
            List of news articles
        """
        if not self.api_key:
            logger.debug("NewsAPI key not configured - news fetching disabled")
            return []
        
        try:
            # Use everything endpoint if no query/sources
            if query or sources:
                url = f"{self.base_url}/everything"
                params = {
                    "apiKey": self.api_key,
                    "language": language,
                    "sortBy": sort_by,
                    "pageSize": min(page_size, 100)
                }
                if query:
                    params["q"] = query
                if sources:
                    params["sources"] = ",".join(sources)
            else:
                # Use top headlines
                url = f"{self.base_url}/top-headlines"
                params = {
                    "apiKey": self.api_key,
                    "language": language,
                    "pageSize": min(page_size, 100)
                }
            
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("status") == "ok":
                articles = data.get("articles", [])
                
                # Normalize article format
                normalized = []
                for article in articles:
                    normalized.append({
                        "title": article.get("title", ""),
                        "description": article.get("description", ""),
                        "text": f"{article.get('title', '')} {article.get('description', '')}",
                        "source": article.get("source", {}).get("name", "unknown"),
                        "url": article.get("url", ""),
                        "published_at": article.get("publishedAt"),
                        "is_breaking": self._is_breaking(article),
                        "timestamp": datetime.now(timezone.utc)
                    })
                
                logger.debug(f"Fetched {len(normalized)} news articles")
                return normalized
            else:
                logger.warning(f"NewsAPI returned error: {data.get('message')}")
                return []
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("NewsAPI rate limit exceeded")
            else:
                logger.error(f"NewsAPI HTTP error: {str(e)}", exc_info=True)
            return []
        except Exception as e:
            logger.error(f"NewsAPI fetch error: {str(e)}", exc_info=True)
            return []
    
    def _is_breaking(self, article: Dict[str, Any]) -> bool:
        """Determine if article is breaking news."""
        # Check if published in last hour
        published_str = article.get("publishedAt")
        if published_str:
            try:
                from dateutil.parser import parse
                published = parse(published_str)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                
                age = datetime.now(timezone.utc) - published
                if age < timedelta(hours=1):
                    return True
            except Exception:
                pass
        
        # Check title for breaking keywords
        title = article.get("title", "").lower()
        breaking_keywords = ["breaking", "urgent", "alert", "just in", "developing"]
        if any(keyword in title for keyword in breaking_keywords):
            return True
        
        return False
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


class RSSFeedReader:
    """Read RSS feeds for news. No API key required."""

    def __init__(self, feed_urls: Optional[List[str]] = None):
        self.client = httpx.AsyncClient(timeout=15.0)
        self.feed_urls = feed_urls or self._load_feeds_from_yaml()

    @staticmethod
    def _load_feeds_from_yaml() -> List[str]:
        """Load RSS feeds from config/rss_feeds.yaml, fall back to hardcoded."""
        try:
            import yaml
            import os
            yaml_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "rss_feeds.yaml")
            yaml_path = os.path.normpath(yaml_path)
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            feeds = []
            for category, urls in (data or {}).items():
                if isinstance(urls, list):
                    feeds.extend(urls)
            if feeds:
                logger.info("Loaded %d RSS feeds from YAML config", len(feeds))
                return feeds
        except Exception as e:
            logger.debug("YAML feed config not available, using defaults: %s", e)
        return RSS_FEEDS

    async def fetch_feed(self, feed_url: str) -> List[Dict[str, Any]]:
        """
        Fetch and parse RSS feed.

        Args:
            feed_url: RSS feed URL

        Returns:
            List of feed items (normalized format)
        """
        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()
            parsed = feedparser.parse(response.text)

            items = []
            for entry in parsed.entries[:30]:  # Limit per feed
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    from time import struct_time
                    st: struct_time = entry.published_parsed
                    published = datetime(*st[:6], tzinfo=timezone.utc).isoformat()
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    st = entry.updated_parsed
                    published = datetime(*st[:6], tzinfo=timezone.utc).isoformat()

                text = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                title = getattr(entry, "title", "") or ""
                items.append({
                    "title": title,
                    "description": text[:500] if isinstance(text, str) else "",
                    "text": f"{title} {text[:500]}" if isinstance(text, str) else title,
                    "source": parsed.feed.get("title", feed_url) or "rss",
                    "url": getattr(entry, "link", "") or "",
                    "published_at": published,
                    "is_breaking": False,
                    "timestamp": datetime.now(timezone.utc),
                })
            logger.debug(f"RSS {feed_url}: {len(items)} items")
            return items
        except Exception as e:
            logger.debug(f"RSS feed error {feed_url}: {e}")
            return []

    async def fetch_all_feeds(self) -> List[Dict[str, Any]]:
        """Fetch all configured RSS feeds concurrently."""
        tasks = [self.fetch_feed(url) for url in self.feed_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_items = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.debug(f"RSS feed {self.feed_urls[i]} failed: {r}")
            elif isinstance(r, list):
                all_items.extend(r)
        return all_items

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


class NewsAggregator:
    """
    Aggregate news from multiple sources.
    RSS feeds work without API keys; NewsAPI requires key.
    """

    def __init__(self):
        self.newsapi = NewsAPIClient()
        self.rss_reader = RSSFeedReader()
        self.sources = []

    async def fetch_all_news(
        self,
        keywords: Optional[List[str]] = None,
        max_articles: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Fetch news from all configured sources.
        RSS always runs (free). NewsAPI runs if key configured.

        Args:
            keywords: Optional keywords to search for
            max_articles: Maximum articles to return

        Returns:
            Aggregated list of news articles
        """
        all_articles = []

        # 1. RSS feeds - always run, no API key
        try:
            rss_items = await self.rss_reader.fetch_all_feeds()
            all_articles.extend(rss_items)
        except Exception as e:
            logger.debug(f"RSS fetch failed: {e}")

        # 2. NewsAPI (if key configured)
        try:
            if keywords:
                for keyword in keywords[:5]:
                    articles = await self.newsapi.fetch_news(
                        query=keyword,
                        page_size=50
                    )
                    all_articles.extend(articles)
            else:
                articles = await self.newsapi.fetch_news(page_size=100)
                all_articles.extend(articles)
        except Exception as e:
            logger.debug(f"NewsAPI fetch failed: {e}")

        # Deduplicate by title (exact + fuzzy)
        unique_articles = self._fuzzy_dedup(all_articles)

        # Sort by timestamp (newest first)
        def _ts(a):
            t = a.get("timestamp")
            if t is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            return t if getattr(t, "tzinfo", None) else t.replace(tzinfo=timezone.utc)

        unique_articles.sort(key=_ts, reverse=True)
        return unique_articles[:max_articles]
    
    @staticmethod
    def _fuzzy_dedup(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate articles by exact + fuzzy title matching."""
        import re
        seen_keys = []
        unique = []
        for article in articles:
            title = (article.get("title") or "").strip()
            if not title:
                continue
            # Normalize: lowercase, strip punctuation, collapse whitespace
            normalized = re.sub(r"[^\w\s]", "", title.lower())
            normalized = re.sub(r"\s+", " ", normalized).strip()
            key = normalized[:60]  # First 60 chars for comparison

            # Check fuzzy match against existing keys
            is_dup = False
            for seen in seen_keys:
                # Simple overlap check: if 80%+ of chars match
                min_len = min(len(key), len(seen))
                if min_len < 10:
                    is_dup = key == seen
                else:
                    matching = sum(1 for a, b in zip(key, seen) if a == b)
                    if matching / min_len >= 0.8:
                        is_dup = True
                        break
            if not is_dup:
                seen_keys.append(key)
                unique.append(article)
        return unique

    async def close(self):
        """Close all clients."""
        await self.newsapi.close()
        await self.rss_reader.close()
