"""
HLTV / Liquipedia Scraper — team ratings, map win rates, patch notes.

Covers:
  - HLTV.org: CS2 team ratings, map pool stats, match results
  - Liquipedia: LoL/Dota2/Valorant tournament data, rosters

All sync scraping via asyncio.to_thread() to avoid blocking the event loop.
300s cache TTL to respect rate limits and avoid hammering.

Usage::
    scraper = HLTVScraper()
    rating = await scraper.get_team_rating("navi", game="cs2")
    map_rates = await scraper.get_map_win_rates("navi")
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

_CACHE_TTL = 300.0  # 5 min
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


# CS2 active map pool (updated when Valve changes it)
CS2_MAP_POOL = [
    "ancient", "anubis", "dust2", "inferno",
    "mirage", "nuke", "vertigo",
]

# Default CT/T side win rates per map (CS2 professional average)
CS2_DEFAULT_MAP_SIDES: Dict[str, Dict[str, float]] = {
    "nuke":    {"ct": 0.57, "t": 0.43},
    "ancient": {"ct": 0.55, "t": 0.45},
    "anubis":  {"ct": 0.54, "t": 0.46},
    "vertigo": {"ct": 0.54, "t": 0.46},
    "inferno": {"ct": 0.53, "t": 0.47},
    "mirage":  {"ct": 0.52, "t": 0.48},
    "dust2":   {"ct": 0.48, "t": 0.52},
}


class HLTVScraper:
    """
    Scraper for HLTV.org (CS2) and Liquipedia (multi-game) data.

    All scraping operations run in asyncio.to_thread() to avoid blocking.
    Results are cached with 300s TTL.
    """

    def __init__(self) -> None:
        self._cache = _BoundedCache()

    async def get_team_rating(self, team_name: str, game: str = "cs2") -> Optional[float]:
        """
        Get team rating (0-2.0 scale for HLTV, normalised for others).

        Returns None if team not found or scraping fails.
        """
        cache_key = f"rating:{game}:{team_name.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if game == "cs2":
                rating = await asyncio.to_thread(self._scrape_hltv_team_rating, team_name)
            else:
                # Liquipedia doesn't have ratings — use recent form as proxy
                results = await self.get_recent_results(team_name, game=game, n=20)
                if results:
                    wins = sum(1 for r in results if r.get("won"))
                    rating = wins / len(results) if results else 0.5
                else:
                    rating = None

            if rating is not None:
                self._cache.set(cache_key, rating)
            return rating
        except Exception as exc:
            logger.debug("HLTVScraper: team rating failed", team=team_name, error=str(exc))
            return None

    async def get_map_win_rates(self, team_name: str) -> Dict[str, float]:
        """
        Get CS2 map-specific win rates for a team.

        Returns dict of map_name -> win_rate (0.0-1.0).
        Falls back to neutral 0.50 for maps with insufficient data.
        """
        cache_key = f"maps:{team_name.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            rates = await asyncio.to_thread(self._scrape_hltv_map_stats, team_name)
            if not rates:
                rates = {m: 0.50 for m in CS2_MAP_POOL}
            self._cache.set(cache_key, rates)
            return rates
        except Exception as exc:
            logger.debug("HLTVScraper: map win rates failed", team=team_name, error=str(exc))
            return {m: 0.50 for m in CS2_MAP_POOL}

    async def get_recent_results(
        self, team_name: str, game: str = "cs2", n: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get recent match results for a team.

        Returns list of dicts with: opponent, score, won (bool), date, event.
        """
        cache_key = f"results:{game}:{team_name.lower()}:{n}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if game == "cs2":
                results = await asyncio.to_thread(self._scrape_hltv_results, team_name, n)
            else:
                results = await asyncio.to_thread(self._scrape_liquipedia_results, team_name, game, n)
            results = results or []
            self._cache.set(cache_key, results)
            return results
        except Exception as exc:
            logger.debug("HLTVScraper: recent results failed", team=team_name, error=str(exc))
            return []

    async def get_current_patch_notes(self, game: str) -> Optional[Dict[str, Any]]:
        """
        Get latest patch/update information for a game.

        Returns dict with: version, date, url, major_changes.
        """
        cache_key = f"patch:{game}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if game == "cs2":
                patch = await asyncio.to_thread(self._scrape_cs2_patch)
            else:
                # Other games handled by Riot API / PandaScore
                patch = None

            if patch:
                self._cache.set(cache_key, patch)
            return patch
        except Exception as exc:
            logger.debug("HLTVScraper: patch notes failed", game=game, error=str(exc))
            return None

    async def get_cs2_map_pool(self) -> List[str]:
        """Get current CS2 active duty map pool."""
        return list(CS2_MAP_POOL)

    async def get_map_side_rates(self, map_name: str) -> Dict[str, float]:
        """Get default CT/T win rates for a CS2 map."""
        return CS2_DEFAULT_MAP_SIDES.get(map_name.lower(), {"ct": 0.50, "t": 0.50})

    # ── Sync scraping methods (run in asyncio.to_thread) ──────────────

    def _scrape_hltv_team_rating(self, team_name: str) -> Optional[float]:
        """Scrape HLTV team rating. Returns 0.0-2.0 scale."""
        # In production, this would scrape hltv.org/ranking/teams
        # For now, return None to indicate "no data" — PandaScore provides
        # team stats as a fallback
        logger.debug("HLTVScraper: HLTV scraping not yet wired — using PandaScore fallback")
        return None

    def _scrape_hltv_map_stats(self, team_name: str) -> Optional[Dict[str, float]]:
        """Scrape HLTV map statistics for a team."""
        logger.debug("HLTVScraper: HLTV map stats scraping not yet wired")
        return None

    def _scrape_hltv_results(self, team_name: str, n: int) -> List[Dict[str, Any]]:
        """Scrape recent HLTV match results."""
        logger.debug("HLTVScraper: HLTV results scraping not yet wired")
        return []

    def _scrape_liquipedia_results(
        self, team_name: str, game: str, n: int
    ) -> List[Dict[str, Any]]:
        """Scrape Liquipedia match results for non-CS2 games."""
        logger.debug("HLTVScraper: Liquipedia scraping not yet wired")
        return []

    def _scrape_cs2_patch(self) -> Optional[Dict[str, Any]]:
        """Scrape latest CS2 patch information from official blog."""
        logger.debug("HLTVScraper: CS2 patch scraping not yet wired")
        return None
