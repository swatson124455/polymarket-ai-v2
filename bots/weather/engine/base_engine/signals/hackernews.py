"""
Hacker News Client — Signal 3: Leading Indicators

Polls HN Algolia API every 15 min for market-relevant keywords.
Free, no API key required, generous rate limits.

APIs:
  - Search: https://hn.algolia.com/api/v1/search
  - Front page: https://hn.algolia.com/api/v1/search?tags=front_page
"""
import time
import httpx
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()


class HackerNewsClient:
    """Hacker News search and monitoring via Algolia API."""

    SEARCH_URL = "https://hn.algolia.com/api/v1/search"
    SEARCH_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)
        self._cache: Dict[str, Any] = {}
        self._cache_ttl = 900  # 15 min

    async def search(
        self,
        query: str,
        tags: str = "story",
        hits_per_page: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search HN stories/comments by keyword.

        Args:
            query: Search terms
            tags: Filter (story, comment, ask_hn, show_hn)
            hits_per_page: Max results

        Returns:
            List of {title, url, points, num_comments, author, created_at}
        """
        cache_key = f"hn:{query}:{tags}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self._cache_ttl:
            return cached["data"]

        try:
            params = {
                "query": query,
                "tags": tags,
                "hitsPerPage": str(hits_per_page),
            }
            resp = await self._client.get(self.SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            results = []
            for hit in data.get("hits", []):
                results.append({
                    "title": hit.get("title", hit.get("story_title", "")),
                    "url": hit.get("url", f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"),
                    "points": hit.get("points", 0) or 0,
                    "num_comments": hit.get("num_comments", 0) or 0,
                    "author": hit.get("author", ""),
                    "created_at": hit.get("created_at", ""),
                    "object_id": hit.get("objectID", ""),
                    "source": "hackernews",
                })

            self._cache[cache_key] = {"data": results, "ts": time.time()}
            return results

        except Exception as e:
            logger.debug("HN search error: %s", e)
            return []

    async def get_front_page(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Get current HN front page stories."""
        return await self.search("", tags="front_page", hits_per_page=limit)

    async def get_mention_count(self, keyword: str, hours: int = 24) -> int:
        """
        Count mentions of a keyword in recent HN posts/comments.

        Args:
            keyword: Search term
            hours: Lookback window

        Returns:
            Number of mentions (nbHits)
        """
        cache_key = f"hn_count:{keyword}:{hours}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self._cache_ttl:
            return cached["data"]

        try:
            cutoff = int(time.time()) - (hours * 3600)
            params = {
                "query": keyword,
                "numericFilters": f"created_at_i>{cutoff}",
                "hitsPerPage": "0",  # Only need count
            }
            resp = await self._client.get(self.SEARCH_DATE_URL, params=params)
            resp.raise_for_status()
            count = resp.json().get("nbHits", 0)

            self._cache[cache_key] = {"data": count, "ts": time.time()}
            return count

        except Exception as e:
            logger.debug("HN mention count error: %s", e)
            return 0

    async def get_market_signal(self, market_question: str) -> Optional[Dict[str, Any]]:
        """
        Get HN signal for a market question.

        Extracts keywords, searches HN, computes engagement score.
        """
        # Extract keywords (>4 chars, not common words)
        stop = {"will", "does", "what", "when", "where", "which", "could", "would", "should", "about", "their", "there", "before", "after"}
        words = [w.strip("?.,!") for w in market_question.split() if len(w) > 4 and w.lower() not in stop]
        if not words:
            return None

        query = " ".join(words[:3])
        stories = await self.search(query, tags="story", hits_per_page=10)
        mention_count = await self.get_mention_count(query, hours=24)

        if not stories and mention_count == 0:
            return None

        total_points = sum(s.get("points", 0) for s in stories)
        total_comments = sum(s.get("num_comments", 0) for s in stories)

        return {
            "query": query,
            "story_count": len(stories),
            "mention_count_24h": mention_count,
            "total_points": total_points,
            "total_comments": total_comments,
            "engagement_score": min(1.0, (total_points + total_comments * 2) / 1000),
            "source": "hackernews",
        }

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()
