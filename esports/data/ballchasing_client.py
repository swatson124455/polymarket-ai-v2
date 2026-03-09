"""
Ballchasing API Client — Rocket League replay analytics (147M+ replays).

API key required (free): log in at ballchasing.com, generate key on Upload tab.
Base URL: https://ballchasing.com/api

Provides per-player and per-team stats from parsed replays: goals, saves,
boost usage, shooting %, positioning. These feed Rocket League prediction
features that PandaScore free tier cannot provide.

Rate limits (free tier): 2 req/s, 500 list/hr, 1000 detail/hr.

Usage::
    client = BallchasingClient(api_key="YOUR_KEY")
    replays = await client.search_replays(player_name="Squishy")
    stats = await client.get_team_aggregate_stats("NRG", days_back=30)
    # stats = {"goals_per_game": 1.5, "save_rate": 0.42, ...}
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

BASE_URL = "https://ballchasing.com/api"

# Module-level TTL cache: cache_key → (data, mono_ts)
_cache: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 1800  # 30 minutes
_CACHE_MAX = 200
_last_request: float = 0.0
_MIN_INTERVAL = 0.6  # Slightly over 0.5s → safe for 2 req/s free tier


async def _rate_limited_get(
    path: str, api_key: str, params: Optional[Dict] = None,
    cache_key: Optional[str] = None,
) -> Optional[Any]:
    """GET request with rate limiting, auth header, and caching."""
    global _last_request

    if cache_key is None:
        cache_key = f"{path}:{sorted((params or {}).items())}"
    cached = _cache.get(cache_key)
    if cached and (_time.monotonic() - cached[1]) < _CACHE_TTL:
        return cached[0]

    now = _time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = _time.monotonic()

    url = f"{BASE_URL}{path}"
    headers = {"Authorization": api_key}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    logger.debug("ballchasing: rate limited", path=path)
                    return None
                if resp.status != 200:
                    logger.debug("ballchasing: non-200", path=path, status=resp.status)
                    return None
                data = await resp.json()

        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            del _cache[oldest]
        _cache[cache_key] = (data, _time.monotonic())
        return data
    except Exception as exc:
        logger.debug("ballchasing: request failed", path=path, error=str(exc))
        return None


class BallchasingClient:
    """Async client for Ballchasing API — Rocket League replay stats."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search_replays(
        self,
        player_name: Optional[str] = None,
        pro: bool = True,
        count: int = 50,
        days_back: int = 30,
    ) -> List[Dict[str, Any]]:
        """Search for recent replays, optionally filtered by player.

        Args:
            player_name: Filter by player name (None = all pro replays).
            pro: Only include replays with pro-tagged players.
            count: Max results per page (1-200).
            days_back: Only include replays from the last N days.

        Returns list of {id, date, blue_team, orange_team, duration, map}.
        """
        from datetime import datetime, timedelta, timezone

        params: Dict[str, Any] = {
            "count": min(count, 200),
            "sort-by": "replay-date",
            "sort-dir": "desc",
            "playlist": "private",  # Pro matches are private/tournament
        }
        if player_name:
            params["player-name"] = player_name
        if pro:
            params["pro"] = "true"
        if days_back > 0:
            after = datetime.now(timezone.utc) - timedelta(days=days_back)
            params["replay-date-after"] = after.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        data = await _rate_limited_get(
            "/replays", self._api_key, params=params,
            cache_key=f"replays:{player_name}:{days_back}",
        )
        if not data or not isinstance(data, dict):
            return []

        replays = data.get("list", [])
        results = []
        for r in replays:
            blue = r.get("blue", {})
            orange = r.get("orange", {})
            results.append({
                "id": r.get("id", ""),
                "date": r.get("date", ""),
                "blue_team": blue.get("name", "Blue"),
                "orange_team": orange.get("name", "Orange"),
                "blue_goals": blue.get("goals", 0),
                "orange_goals": orange.get("goals", 0),
                "duration": r.get("duration", 0),
                "map": r.get("map_code", ""),
            })
        return results

    async def get_replay_stats(self, replay_id: str) -> Optional[Dict[str, Any]]:
        """Get full per-player stats for a single replay.

        Returns {blue: {players: [...]}, orange: {players: [...]}} with
        core, boost, movement, positioning stats per player.
        """
        data = await _rate_limited_get(
            f"/replays/{replay_id}", self._api_key,
            cache_key=f"replay:{replay_id}",
        )
        if not data or not isinstance(data, dict):
            return None

        return {
            "id": data.get("id", ""),
            "date": data.get("date", ""),
            "duration": data.get("duration", 0),
            "blue": self._extract_team_stats(data.get("blue", {})),
            "orange": self._extract_team_stats(data.get("orange", {})),
        }

    @staticmethod
    def _extract_team_stats(team_data: Dict) -> Dict[str, Any]:
        """Extract team-level aggregate stats from replay team data."""
        players = team_data.get("players", [])
        team_stats = {
            "name": team_data.get("name", ""),
            "goals": team_data.get("stats", {}).get("core", {}).get("goals", 0),
            "shots": team_data.get("stats", {}).get("core", {}).get("shots", 0),
            "saves": team_data.get("stats", {}).get("core", {}).get("saves", 0),
            "assists": team_data.get("stats", {}).get("core", {}).get("assists", 0),
            "player_count": len(players),
        }
        # Aggregate per-player boost stats
        total_bpm = 0.0
        total_stolen = 0.0
        for p in players:
            boost = p.get("stats", {}).get("boost", {})
            total_bpm += float(boost.get("bpm", 0))
            total_stolen += float(boost.get("amount_stolen", 0))
        n = max(len(players), 1)
        team_stats["avg_bpm"] = round(total_bpm / n, 1)
        team_stats["avg_boost_stolen"] = round(total_stolen / n, 1)
        return team_stats

    async def get_team_aggregate_stats(
        self, team_name: str, days_back: int = 30, max_replays: int = 20,
    ) -> Optional[Dict[str, float]]:
        """Aggregate team stats across recent replays.

        Searches for replays by team name, fetches details, and computes
        per-game averages for prediction features.

        Returns {goals_per_game, shots_per_game, saves_per_game,
                 shooting_pct, avg_bpm, games_found} or None.
        """
        replays = await self.search_replays(
            player_name=team_name, pro=True, count=max_replays, days_back=days_back,
        )
        if not replays:
            return None

        total_goals = 0
        total_shots = 0
        total_saves = 0
        total_bpm = 0.0
        games = 0

        # Sample up to max_replays detailed stats
        for replay in replays[:max_replays]:
            stats = await self.get_replay_stats(replay["id"])
            if stats is None:
                continue

            # Find which side this team was on
            team_side = None
            name_lower = team_name.lower()
            for side in ("blue", "orange"):
                side_name = stats[side].get("name", "").lower()
                if name_lower in side_name or side_name in name_lower:
                    team_side = side
                    break

            if team_side is None:
                continue

            team = stats[team_side]
            total_goals += team.get("goals", 0)
            total_shots += team.get("shots", 0)
            total_saves += team.get("saves", 0)
            total_bpm += team.get("avg_bpm", 0)
            games += 1

        if games == 0:
            return None

        return {
            "goals_per_game": round(total_goals / games, 2),
            "shots_per_game": round(total_shots / games, 2),
            "saves_per_game": round(total_saves / games, 2),
            "shooting_pct": round(total_goals / max(total_shots, 1) * 100, 1),
            "avg_bpm": round(total_bpm / games, 1),
            "games_found": float(games),
        }
