"""
Aligulac API Client — SC2 pro player ratings and match predictions.

API key required (free, self-service): http://aligulac.com/about/api/
Base URL: http://aligulac.com/api/v1/

Provides Elo ratings and head-to-head win probabilities for SC2 pro players.
Blended with our Glicko-2 ratings for improved SC2 predictions.

Usage::
    client = AligulacClient(api_key="YOUR_KEY")
    player_id = await client.search_player("Serral")
    prediction = await client.predict_match(player_a_id=485, player_b_id=49, best_of=5)
    # prediction = {"prob_a": 0.72, "prob_b": 0.28, ...}
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

BASE_URL = "http://aligulac.com/api/v1"
SEARCH_URL = "http://aligulac.com/search/json/"

# Module-level TTL cache: cache_key → (data, mono_ts)
_cache: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 1800  # 30 minutes
_CACHE_MAX = 150
_last_request: float = 0.0
_MIN_INTERVAL = 1.0  # 1 req/s — conservative, no formal limit published


async def _rate_limited_get(
    url: str, params: Optional[Dict] = None, cache_key: Optional[str] = None,
) -> Optional[Any]:
    """GET request with rate limiting and caching."""
    global _last_request

    if cache_key is None:
        cache_key = f"{url}:{params}"
    cached = _cache.get(cache_key)
    if cached and (_time.monotonic() - cached[1]) < _CACHE_TTL:
        return cached[0]

    # Rate limit
    now = _time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = _time.monotonic()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 429:
                    logger.debug("aligulac: rate limited", url=url)
                    return None
                if resp.status != 200:
                    logger.debug("aligulac: non-200", url=url, status=resp.status)
                    return None
                data = await resp.json()

        # Evict oldest if cache full
        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            del _cache[oldest]
        _cache[cache_key] = (data, _time.monotonic())
        return data
    except Exception as exc:
        logger.debug("aligulac: request failed", url=url, error=str(exc))
        return None


class AligulacClient:
    """Async client for Aligulac SC2 API — player ratings and predictions."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search_player(self, name: str) -> Optional[int]:
        """Search for a player by name, return Aligulac player ID.

        Uses the /search/json/ endpoint (no API key needed).
        Returns the player_id of the best match, or None.
        """
        if not name or not name.strip():
            return None

        data = await _rate_limited_get(
            SEARCH_URL, params={"q": name.strip()},
            cache_key=f"search:{name.strip().lower()}",
        )
        if not data or not isinstance(data, dict):
            return None

        players = data.get("players", [])
        if not players:
            return None

        name_lower = name.strip().lower()

        # Pass 1: exact tag match (case-insensitive)
        for p in players:
            if str(p.get("tag", "")).lower() == name_lower:
                return p.get("id")

        # Pass 2: first result (search is ranked by relevance)
        first = players[0]
        return first.get("id")

    async def get_player(self, player_id: int) -> Optional[Dict[str, Any]]:
        """Get player info including current rating.

        Returns {id, tag, race, country, rating, rating_vp, rating_vt, rating_vz, dev}.
        """
        data = await _rate_limited_get(
            f"{BASE_URL}/player/{player_id}/",
            params={"apikey": self._api_key, "format": "json"},
            cache_key=f"player:{player_id}",
        )
        if not data or not isinstance(data, dict):
            return None

        current = data.get("current_rating") or {}
        return {
            "id": data.get("id"),
            "tag": data.get("tag", ""),
            "race": data.get("race", ""),
            "country": data.get("country", ""),
            "rating": float(current.get("rating", 0)),
            "rating_vp": float(current.get("rating_vp", 0)),
            "rating_vt": float(current.get("rating_vt", 0)),
            "rating_vz": float(current.get("rating_vz", 0)),
            "dev": float(current.get("dev", 0)),
        }

    async def predict_match(
        self, player_a_id: int, player_b_id: int, best_of: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """Get head-to-head match prediction.

        Returns {prob_a, prob_b, rating_a, rating_b} or None.
        The prob_a/prob_b are Aligulac's computed win probabilities.
        """
        # best_of must be positive odd integer
        if best_of < 1:
            best_of = 1
        if best_of % 2 == 0:
            best_of += 1

        data = await _rate_limited_get(
            f"{BASE_URL}/predictmatch/{player_a_id},{player_b_id}/",
            params={"bo": best_of, "apikey": self._api_key, "format": "json"},
            cache_key=f"predict:{player_a_id}:{player_b_id}:{best_of}",
        )
        if not data or not isinstance(data, dict):
            return None

        prob_a = data.get("proba")
        prob_b = data.get("probb")
        if prob_a is None or prob_b is None:
            return None

        return {
            "prob_a": float(prob_a),
            "prob_b": float(prob_b),
            "rating_a": float(data.get("rta", 0)),
            "rating_b": float(data.get("rtb", 0)),
        }

    async def get_player_enrichment(
        self, player_name: str, opponent_name: str, best_of: int = 3,
    ) -> Optional[Dict[str, float]]:
        """Get SC2 enrichment: Aligulac's match prediction for two players.

        Returns {aligulac_prob_a, rating_a, rating_b, rating_diff} or None.
        Designed to blend with our Glicko-2 predictions.
        """
        id_a, id_b = await asyncio.gather(
            self.search_player(player_name),
            self.search_player(opponent_name),
        )
        if id_a is None or id_b is None:
            return None

        prediction = await self.predict_match(id_a, id_b, best_of=best_of)
        if prediction is None:
            return None

        return {
            "aligulac_prob_a": prediction["prob_a"],
            "rating_a": prediction["rating_a"],
            "rating_b": prediction["rating_b"],
            "rating_diff": prediction["rating_a"] - prediction["rating_b"],
        }
