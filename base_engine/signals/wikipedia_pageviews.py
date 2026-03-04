"""
Wikipedia Pageviews Signal — Tier 2 #15

Free, high-signal alternative data source. Spike in pageviews for a topic
often precedes price moves in related prediction markets.

API: Wikimedia REST API (no auth needed, rate-limited to 200 req/s).
Endpoint: /metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from structlog import get_logger

logger = get_logger()

_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
_CACHE_TTL = 3600  # 1 hour


class WikipediaPageviews:
    """Fetch and score Wikipedia pageview spikes for market-relevant topics."""

    def __init__(self):
        self._cache: Dict[str, Dict] = {}

    def _extract_topic(self, question: str) -> Optional[str]:
        """Extract the most likely Wikipedia article title from a market question."""
        if not question:
            return None
        # Remove common prefixes
        q = re.sub(r"^(Will|Does|Is|Has|Can|Should|Are)\s+", "", question, flags=re.I)
        # Take first noun phrase (capitalized words)
        caps = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", q)
        if caps:
            return caps[0].replace(" ", "_")
        # Fallback: first 3 significant words
        words = [w for w in q.split() if len(w) > 3][:3]
        return "_".join(words) if words else None

    async def get_pageview_signal(
        self, question: str, days: int = 30
    ) -> Dict:
        """
        Get pageview signal for a market question.

        Returns:
            spike_ratio: recent_7d_avg / prior_avg (>1.5 = interesting)
            total_views: total views in period
            trend: "rising", "falling", or "stable"
        """
        topic = self._extract_topic(question)
        if not topic:
            return {"spike_ratio": 1.0, "total_views": 0, "trend": "stable", "topic": None}

        cache_key = f"{topic}:{days}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if (datetime.now(timezone.utc) - cached["fetched_at"]).total_seconds() < _CACHE_TTL:
                return cached["data"]

        try:
            import aiohttp
            end = datetime.now(timezone.utc) - timedelta(days=1)
            start = end - timedelta(days=days)
            url = (
                f"{_BASE}/en.wikipedia/all-access/user/{topic}/daily"
                f"/{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
            )
            # Wikimedia REST API requires a descriptive User-Agent with a contact address;
            # requests without it return HTTP 403. See: https://www.mediawiki.org/wiki/API:REST_API
            _headers = {"User-Agent": "polymarket-ai-bot/2.0 (research; contact@polymarket-ai.local)"}
            async with aiohttp.ClientSession(headers=_headers) as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.debug(
                            "Wikipedia pageviews: HTTP %d for topic %s — skipping", resp.status, topic
                        )
                        return {"spike_ratio": 1.0, "total_views": 0, "trend": "stable", "topic": topic}
                    data = await resp.json()

            items = data.get("items", [])
            if len(items) < 14:
                return {"spike_ratio": 1.0, "total_views": sum(i.get("views", 0) for i in items), "trend": "stable", "topic": topic}

            views = [i.get("views", 0) for i in items]
            total = sum(views)
            recent_7 = views[-7:]
            prior = views[:-7]
            avg_recent = sum(recent_7) / len(recent_7) if recent_7 else 0
            avg_prior = sum(prior) / len(prior) if prior else 0
            spike_ratio = avg_recent / max(avg_prior, 1)

            # Trend: compare last 3 days to prior 3 days
            if len(views) >= 6:
                last3 = sum(views[-3:]) / 3
                prev3 = sum(views[-6:-3]) / 3
                if last3 > prev3 * 1.2:
                    trend = "rising"
                elif last3 < prev3 * 0.8:
                    trend = "falling"
                else:
                    trend = "stable"
            else:
                trend = "stable"

            result = {"spike_ratio": round(spike_ratio, 2), "total_views": total, "trend": trend, "topic": topic}
            self._cache[cache_key] = {"data": result, "fetched_at": datetime.now(timezone.utc)}
            return result

        except ImportError:
            logger.debug("aiohttp not installed, Wikipedia pageviews unavailable")
            return {"spike_ratio": 1.0, "total_views": 0, "trend": "stable", "topic": topic}
        except Exception as e:
            logger.debug("Wikipedia pageviews fetch failed: %s", e)
            return {"spike_ratio": 1.0, "total_views": 0, "trend": "stable", "topic": topic}
