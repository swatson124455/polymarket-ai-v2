"""
OpenDota API Client — Free Dota2 match & hero data.

No auth required. Rate limit: ~50 req/min (free tier).
Docs: https://docs.opendota.com/

Provides hero pick/ban data and team match history that PandaScore
free tier does not include. Enriches Dota2Model with hero-level features.

Usage::
    client = OpenDotaClient()
    heroes = await client.get_team_heroes(team_id=123456)
    matchups = await client.get_hero_matchups(hero_id=1)
    recent = await client.get_team_recent_matches(team_id=123456)
"""
from __future__ import annotations

import asyncio
import re
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

BASE_URL = "https://api.opendota.com/api"

# Module-level TTL cache: cache_key → (data, mono_ts)
_cache: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 1800  # 30 minutes
_CACHE_MAX = 200
_last_request: float = 0.0
_MIN_INTERVAL = 1.5  # Minimum 1.5s between requests (safe for free tier)


async def _rate_limited_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """GET request with rate limiting and caching."""
    global _last_request

    cache_key = f"{path}:{params}"
    cached = _cache.get(cache_key)
    if cached and (_time.monotonic() - cached[1]) < _CACHE_TTL:
        return cached[0]

    # Rate limit
    now = _time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = _time.monotonic()

    url = f"{BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 429:
                    logger.debug("opendota: rate limited", path=path)
                    return None
                if resp.status != 200:
                    logger.debug("opendota: non-200", path=path, status=resp.status)
                    return None
                data = await resp.json()

        # Evict oldest if cache full
        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            del _cache[oldest]
        _cache[cache_key] = (data, _time.monotonic())
        return data
    except Exception as exc:
        logger.debug("opendota: request failed", path=path, error=str(exc))
        return None


class OpenDotaClient:
    """Async client for OpenDota API — free Dota2 data."""

    async def get_team_heroes(
        self, team_id: int, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get team's most-played heroes with win rates.

        Returns list of {hero_id, games_played, wins, win_rate}.
        """
        data = await _rate_limited_get(f"/teams/{team_id}/heroes")
        if not data or not isinstance(data, list):
            return []
        # Sort by games played, take top N
        sorted_heroes = sorted(data, key=lambda h: h.get("games_played", 0), reverse=True)
        result = []
        for h in sorted_heroes[:limit]:
            played = h.get("games_played", 0)
            wins = h.get("wins", 0)
            if played > 0:
                result.append({
                    "hero_id": h.get("hero_id"),
                    "games_played": played,
                    "wins": wins,
                    "win_rate": round(wins / played, 4),
                })
        return result

    async def get_hero_matchups(self, hero_id: int) -> Dict[int, float]:
        """Get hero vs hero matchup win rates.

        Returns {opponent_hero_id: win_rate_advantage}.
        Positive = hero_id has advantage over opponent.
        """
        data = await _rate_limited_get(f"/heroes/{hero_id}/matchups")
        if not data or not isinstance(data, list):
            return {}
        matchups: Dict[int, float] = {}
        for m in data:
            opp_id = m.get("hero_id")
            played = m.get("games_played", 0)
            wins = m.get("wins", 0)
            if played >= 10 and opp_id is not None:
                matchups[opp_id] = round((wins / played) - 0.5, 4)
        return matchups

    async def get_team_recent_matches(
        self, team_id: int, limit: int = 25
    ) -> List[Dict[str, Any]]:
        """Get team's recent match results.

        Returns list of {match_id, radiant_win, radiant, duration, start_time}.
        """
        data = await _rate_limited_get(f"/teams/{team_id}/matches")
        if not data or not isinstance(data, list):
            return []
        results = []
        for m in data[:limit]:
            results.append({
                "match_id": m.get("match_id"),
                "radiant_win": m.get("radiant_win"),
                "radiant": m.get("radiant"),
                "duration": m.get("duration"),
                "start_time": m.get("start_time"),
                "league_name": m.get("league_name", ""),
            })
        return results

    async def get_team_form(self, team_id: int, last_n: int = 10) -> Dict[str, Any]:
        """Compute recent form metrics for a team.

        Returns {win_rate, avg_duration, matches_played, form_string}.
        """
        matches = await self.get_team_recent_matches(team_id, limit=last_n)
        if not matches:
            return {"win_rate": 0.5, "avg_duration": 0, "matches_played": 0, "form_string": ""}

        wins = 0
        total_duration = 0
        form_chars = []

        for m in matches:
            is_radiant = m.get("radiant")
            radiant_win = m.get("radiant_win")
            if radiant_win is None:
                continue
            won = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
            if won:
                wins += 1
                form_chars.append("W")
            else:
                form_chars.append("L")
            total_duration += m.get("duration", 0)

        n = len(matches)
        return {
            "win_rate": round(wins / n, 4) if n > 0 else 0.5,
            "avg_duration": round(total_duration / n) if n > 0 else 0,
            "matches_played": n,
            "form_string": "".join(form_chars[:5]),  # Last 5: "WWLWL"
        }

    async def search_team(self, team_name: str) -> Optional[int]:
        """Search for a team by name/tag, return OpenDota team_id.

        Fetches the /teams endpoint (all pro teams, cached 30 min) and
        searches by exact name, exact tag, then substring match.

        Returns team_id or None if not found.
        """
        data = await _rate_limited_get("/teams")
        if not data or not isinstance(data, list):
            return None

        name_lower = team_name.lower().strip()
        if not name_lower:
            return None

        # Pass 1: exact name match (case-insensitive)
        for team in data:
            if str(team.get("name", "")).lower().strip() == name_lower:
                return team.get("team_id")

        # Pass 2: exact tag match (e.g. "OG", "EG", "LGD")
        for team in data:
            if str(team.get("tag", "")).lower().strip() == name_lower:
                return team.get("team_id")

        # Pass 3: word-boundary match — avoids "og" matching "rogue"
        # Prefer longest containing name (most specific match)
        pattern = re.compile(r'\b' + re.escape(name_lower) + r'\b')
        candidates = []
        for team in data:
            tname = str(team.get("name", "")).lower()
            if pattern.search(tname) or re.search(r'\b' + re.escape(tname) + r'\b', name_lower):
                candidates.append((len(tname), team.get("team_id")))
        if candidates:
            candidates.sort(reverse=True)  # longest name first → most specific
            return candidates[0][1]

        return None

    async def get_team_enrichment(
        self, team_name: str
    ) -> Optional[Dict[str, float]]:
        """Get enrichment features for a Dota2 team by name.

        Returns {form_wr, form_matches, hero_pool_depth} or None.
        Combines recent form + hero pool analysis into simple numeric signals.
        """
        team_id = await self.search_team(team_name)
        if team_id is None:
            return None

        form = await self.get_team_form(team_id)
        if not form or form["matches_played"] == 0:
            return None

        heroes = await self.get_team_heroes(team_id, limit=30)
        # Hero pool depth: heroes with ≥5 games and >45% WR
        pool_depth = sum(
            1 for h in heroes
            if h.get("games_played", 0) >= 5 and h.get("win_rate", 0) > 0.45
        )

        return {
            "form_wr": form["win_rate"],
            "form_matches": float(form["matches_played"]),
            "hero_pool_depth": float(pool_depth),
        }

    async def get_hero_stats(self) -> Dict[int, Dict]:
        """Get global hero statistics — pick rate, win rate, ban rate.

        Returns {hero_id: {localized_name, pick_rate, win_rate, ban_rate}}.
        Useful for meta-awareness features.
        """
        data = await _rate_limited_get("/heroStats")
        if not data or not isinstance(data, list):
            return {}
        result: Dict[int, Dict] = {}
        for h in data:
            hero_id = h.get("id")
            if hero_id is None:
                continue
            total_picks = sum(h.get(f"{rank}_pick", 0) for rank in
                             ("1", "2", "3", "4", "5", "6", "7", "8"))
            total_wins = sum(h.get(f"{rank}_win", 0) for rank in
                            ("1", "2", "3", "4", "5", "6", "7", "8"))
            result[hero_id] = {
                "localized_name": h.get("localized_name", ""),
                "pick_count": total_picks,
                "win_rate": round(total_wins / total_picks, 4) if total_picks > 0 else 0.5,
            }
        return result
