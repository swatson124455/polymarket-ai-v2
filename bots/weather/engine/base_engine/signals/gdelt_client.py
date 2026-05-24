"""
GDELT Client — Signal 4: News Sentiment

Polls GDELT 2.0 API every 15 minutes for global news events with tone scores.
Free, no API key required. Returns articles with sentiment tone.

API docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-queries/
"""
import time
import httpx
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()


class GDELTClient:
    """GDELT 2.0 news event and tone API client."""

    BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=20.0)
        self._cache: Dict[str, Any] = {}
        self._cache_ttl = 900  # 15 min

    async def search_events(
        self,
        keywords: List[str],
        timespan: str = "15min",
        max_records: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Search GDELT for recent news articles matching keywords.

        Args:
            keywords: Search terms (OR'd together)
            timespan: Time window (15min, 1h, 4h, 24h)
            max_records: Max articles to return

        Returns:
            List of articles with title, url, domain, tone, seendate
        """
        query = " OR ".join(f'"{kw}"' if " " in kw else kw for kw in keywords[:5])
        cache_key = f"gdelt:{query}:{timespan}"

        # Check cache
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self._cache_ttl:
            return cached["data"]

        try:
            params = {
                "query": query,
                "mode": "ArtList",
                "maxrecords": str(max_records),
                "format": "json",
                "timespan": timespan,
                "sort": "DateDesc",
            }
            resp = await self._client.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            articles = []
            for article in data.get("articles", []):
                articles.append({
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "domain": article.get("domain", ""),
                    "tone": float(article.get("tone", 0)),
                    "seendate": article.get("seendate", ""),
                    "language": article.get("language", "English"),
                    "source": "gdelt",
                })

            self._cache[cache_key] = {"data": articles, "ts": time.time()}
            return articles

        except Exception as e:
            logger.debug("GDELT search error: %s", e)
            return []

    async def get_tone_for_topic(self, topic: str) -> Dict[str, Any]:
        """
        Get aggregate tone data for a topic.

        Returns:
            avg_tone (float), tone_trend (rising/falling/stable), article_count (int)
        """
        articles = await self.search_events([topic], timespan="24h", max_records=100)
        if not articles:
            return {"avg_tone": 0.0, "tone_trend": "stable", "article_count": 0}

        tones = [a["tone"] for a in articles if a.get("tone")]
        if not tones:
            return {"avg_tone": 0.0, "tone_trend": "stable", "article_count": len(articles)}

        avg_tone = sum(tones) / len(tones)

        # Compute trend: compare first half vs second half
        mid = len(tones) // 2
        if mid > 0:
            first_half_avg = sum(tones[:mid]) / mid
            second_half_avg = sum(tones[mid:]) / len(tones[mid:])
            diff = second_half_avg - first_half_avg
            if diff > 0.5:
                trend = "rising"
            elif diff < -0.5:
                trend = "falling"
            else:
                trend = "stable"
        else:
            trend = "stable"

        return {
            "avg_tone": round(avg_tone, 3),
            "tone_trend": trend,
            "article_count": len(articles),
        }

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()
