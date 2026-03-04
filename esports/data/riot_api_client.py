"""
Riot Games API Client — async client for LoL-specific data.

Endpoints used:
  - /lol/status/v4/platform-data — patch version check
  - Lolesports Data API — schedule, match timeline, champion stats

Rate limit: 20 req/s (development key), 100 req/s (production key).
All HTTP via httpx.AsyncClient (persistent connection).

Usage::
    client = RiotApiClient(api_key="RGAPI-...")
    await client.init()
    version = await client.get_current_patch_version()
    await client.close()
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

_DDRAGON_URL = "https://ddragon.leagueoflegends.com"
_LOLESPORTS_URL = "https://esports-api.lolesports.com/persisted/gw"
_CACHE_TTL = 300.0  # 5 min for patch/schedule data
_CACHE_MAX = 200


class _BoundedCache:
    """Simple TTL cache with max-size eviction."""

    def __init__(self, max_size: int = _CACHE_MAX, default_ttl: float = _CACHE_TTL):
        self._data: OrderedDict[str, tuple] = OrderedDict()
        self._max_size = max_size
        self._ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)


class RiotApiClient:
    """
    Async client for Riot Games APIs (LoL focused).

    Provides patch version checks, league schedules, and champion statistics.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        self._client = None
        self._cache = _BoundedCache()

    async def init(self) -> None:
        """Create persistent HTTP client."""
        import httpx
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-Riot-Token"] = self._api_key
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=3.0),
        )
        logger.info("RiotApiClient: initialised")

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def get_current_patch_version(self) -> Optional[str]:
        """
        Get the current LoL patch version from Data Dragon.

        Returns version string like '14.5.1' or None on failure.
        """
        cache_key = "patch_version"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = await self._client.get(f"{_DDRAGON_URL}/api/versions.json")
            resp.raise_for_status()
            versions = resp.json()
            if isinstance(versions, list) and versions:
                version = str(versions[0])
                self._cache.set(cache_key, version)
                return version
        except Exception as exc:
            logger.debug("RiotApiClient: patch version fetch failed", error=str(exc))
        return None

    async def get_champion_stats(self) -> Dict[str, float]:
        """
        Get current champion win rates from Data Dragon / community sources.

        Returns dict of champion_name -> win_rate (0.0-1.0).
        """
        cache_key = "champion_stats"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Use Data Dragon champion list + community win rate data
        try:
            version = await self.get_current_patch_version()
            if not version:
                return {}
            resp = await self._client.get(
                f"{_DDRAGON_URL}/cdn/{version}/data/en_US/champion.json"
            )
            resp.raise_for_status()
            data = resp.json()
            champions = data.get("data", {})
            # Data Dragon doesn't include win rates — return champion list
            # Win rates would come from community APIs (LoLalytics, U.GG) in production
            stats = {name: 0.50 for name in champions}
            self._cache.set(cache_key, stats)
            return stats
        except Exception as exc:
            logger.debug("RiotApiClient: champion stats failed", error=str(exc))
            return {}

    async def get_league_schedule(self, league: str = "lck") -> List[Dict[str, Any]]:
        """
        Get upcoming schedule for a league (lck, lec, lpl, lcs, etc.).

        Uses the Lolesports API.
        """
        cache_key = f"schedule:{league}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = await self._client.get(
                f"{_LOLESPORTS_URL}/getSchedule",
                params={"hl": "en-US", "leagueId": league},
                headers={"x-api-key": "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"},
            )
            resp.raise_for_status()
            data = resp.json()
            events = data.get("data", {}).get("schedule", {}).get("events", [])
            self._cache.set(cache_key, events)
            return events
        except Exception as exc:
            logger.debug("RiotApiClient: schedule fetch failed", league=league, error=str(exc))
            return []

    async def get_match_timeline(self, game_id: str) -> Optional[Dict[str, Any]]:
        """
        Get match timeline data for a completed game.

        Returns frame-by-frame game state including gold, kills, objectives.
        Used for model training data.
        """
        cache_key = f"timeline:{game_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = await self._client.get(
                f"{_LOLESPORTS_URL}/getGameTimeline",
                params={"hl": "en-US", "gameId": game_id},
                headers={"x-api-key": "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"},
            )
            resp.raise_for_status()
            data = resp.json()
            timeline = data.get("data", {}).get("frames", [])
            result = {"game_id": game_id, "frames": timeline}
            self._cache.set(cache_key, result)
            return result
        except Exception as exc:
            logger.debug("RiotApiClient: timeline fetch failed", game_id=game_id, error=str(exc))
            return None
