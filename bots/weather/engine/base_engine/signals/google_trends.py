"""
Google Trends Integration
==========================
Leading indicator for market moves. Uses pytrends (no API key needed).

Anti-ban hardening:
- Random delay 10-30s between requests
- User-Agent rotation
- Redis cache (2-4h TTL)
- 1h backoff on TooManyRequestsError
- Retry with exponential backoff (max 3)
"""
import asyncio
import random
import time
from typing import Dict, List, Optional
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()

# User-Agent rotation pool
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Module-level backoff state
_last_429_time: float = 0.0
_BACKOFF_SECONDS: float = 3600.0  # 1 hour backoff after 429


def _fetch_trends_sync(keywords: List[str], timeframe: str = "today 7-d") -> Dict:
    """Synchronous pytrends fetch with anti-ban hardening (run in executor)."""
    global _last_429_time

    # Check backoff
    if time.time() - _last_429_time < _BACKOFF_SECONDS:
        remaining = int(_BACKOFF_SECONDS - (time.time() - _last_429_time))
        logger.debug("Google Trends in backoff (%ds remaining)", remaining)
        return {"scores": {kw: 0.5 for kw in keywords}, "signal": "neutral", "backoff": True}

    # Random delay 10-30s to avoid detection
    import time as _time
    _time.sleep(random.uniform(10, 30))

    max_retries = 3
    for attempt in range(max_retries):
        try:
            from pytrends.request import TrendReq
            ua = random.choice(_USER_AGENTS)
            pytrends = TrendReq(
                hl="en-US", tz=360,
                requests_args={"headers": {"User-Agent": ua}},
            )
            pytrends.build_payload(keywords[:5], timeframe=timeframe, geo="")
            df = pytrends.interest_over_time()
            if df is None or df.empty or "isPartial" not in df.columns:
                return {"scores": {kw: 0.5 for kw in keywords}, "signal": "neutral"}
            df = df.drop(columns=["isPartial"], errors="ignore")
            scores = {}
            for kw in keywords:
                if kw in df.columns:
                    scores[kw] = float(df[kw].mean()) / 100.0
                else:
                    scores[kw] = 0.5
            return {"scores": scores, "signal": "neutral"}
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "too many" in err_str:
                _last_429_time = time.time()
                logger.warning("Google Trends 429 — backoff for %ds", int(_BACKOFF_SECONDS))
                return {"scores": {kw: 0.5 for kw in keywords}, "signal": "neutral", "rate_limited": True}
            if attempt < max_retries - 1:
                backoff = (2 ** attempt) * random.uniform(5, 15)
                logger.debug("pytrends retry %d/%d in %.0fs: %s", attempt + 1, max_retries, backoff, e)
                _time.sleep(backoff)
            else:
                logger.debug("pytrends fetch failed after %d retries: %s", max_retries, e)
                return {"scores": {kw: 0.5 for kw in keywords}, "signal": "neutral"}


class GoogleTrendsClient:
    """
    Google Trends integration for market signals.
    Uses pytrends (no API key required).
    Anti-ban: random delays, UA rotation, Redis cache, exponential backoff.
    """

    def __init__(self, api_key: Optional[str] = None, cache=None):
        self.api_key = api_key
        self.enabled = getattr(settings, "USE_GOOGLE_TRENDS", True)
        self._cache = cache  # RedisCache instance (optional)
        self._cache_ttl = random.randint(7200, 14400)  # 2-4h random TTL

    async def get_trend_score(self, keywords: List[str], timeframe: str = "7d") -> Dict:
        """
        Get trend score for keywords with Redis caching.

        Args:
            keywords: List of keywords to search
            timeframe: "7d", "30d", "90d", "1y"

        Returns:
            Dict with trend scores and signals
        """
        if not self.enabled or not keywords:
            return {
                "enabled": self.enabled,
                "keywords": keywords,
                "timeframe": timeframe,
                "scores": {kw: 0.5 for kw in keywords},
                "signal": "neutral"
            }

        # Check Redis cache first
        cache_key = f"gtrends:{','.join(sorted(keywords[:5]))}:{timeframe}"
        if self._cache:
            try:
                cached = await self._cache.get(cache_key)
                if cached:
                    logger.debug("Google Trends cache hit: %s", cache_key)
                    return cached
            except Exception:
                pass

        tf_map = {"7d": "today 7-d", "30d": "today 1-m", "90d": "today 3-m", "1y": "today 12-m"}
        pt_timeframe = tf_map.get(timeframe, "today 7-d")

        try:
            result = await asyncio.to_thread(_fetch_trends_sync, keywords, pt_timeframe)
        except Exception as e:
            logger.debug("Google Trends async fetch failed: %s", e)
            result = {"scores": {kw: 0.5 for kw in keywords}, "signal": "neutral"}

        scores = result.get("scores", {kw: 0.5 for kw in keywords})
        avg = sum(scores.values()) / len(scores) if scores else 0.5
        signal = "bullish" if avg > 0.7 else ("bearish" if avg < 0.3 else "neutral")
        result["enabled"] = True
        result["signal"] = signal
        result["keywords"] = keywords
        result["timeframe"] = timeframe

        # Cache result in Redis (2-4h TTL)
        if self._cache and not result.get("rate_limited") and not result.get("backoff"):
            try:
                await self._cache.set(cache_key, result, ttl=self._cache_ttl)
            except Exception:
                pass

        return result

    async def get_market_signal(self, market_question: str) -> Dict:
        """
        Extract keywords from market question and get trend signal.

        Args:
            market_question: Market question text

        Returns:
            Dict with trend signal
        """
        keywords = self._extract_keywords(market_question)

        if not keywords:
            return {
                "signal": "neutral",
                "reason": "no_keywords"
            }

        trend_data = await self.get_trend_score(keywords)

        if not trend_data.get("enabled"):
            return trend_data

        scores = trend_data.get("scores", {})
        avg_score = sum(scores.values()) / len(scores) if scores else 0.5

        if avg_score > 0.7:
            signal = "bullish"
        elif avg_score < 0.3:
            signal = "bearish"
        else:
            signal = "neutral"

        return {
            "signal": signal,
            "trend_score": avg_score,
            "keywords": keywords,
            "timeframe": trend_data.get("timeframe")
        }

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from text."""
        stop_words = {"will", "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "this", "that"}
        words = text.lower().split()
        keywords = [w for w in words if len(w) > 3 and w not in stop_words]
        return keywords[:5]
