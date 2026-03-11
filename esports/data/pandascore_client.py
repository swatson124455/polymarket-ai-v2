"""
PandaScore API Client — async HTTP client for live + historical esports data.

Covers 8 game titles: LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, Rocket League.
PandaScore REST API: https://developers.pandascore.co/

Rate limit: 1000 req/hour (free tier). Bounded response cache with 30s TTL.
All HTTP via httpx.AsyncClient (persistent connection, exponential backoff).

Usage::
    client = PandaScoreClient(api_key="...")
    await client.init()
    matches = await client.get_live_matches()
    await client.close()
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

_BASE_URL = "https://api.pandascore.co"
_CACHE_TTL = 30.0
_CACHE_MAX = 500
_BASE_BACKOFF = 1.0
_MAX_BACKOFF = 30.0

# PandaScore game slug mapping
GAME_SLUGS = {
    "lol": "lol",
    "cs2": "csgo",         # PandaScore still uses "csgo" slug for CS2
    "dota2": "dota2",
    "valorant": "valorant",
    "cod": "codmw",
    "r6": "r6siege",
    "sc2": "starcraft-2",
    "rl": "rl",
}


@dataclass
class EsportsMatch:
    """Normalised match data from PandaScore."""
    match_id: int
    game: str                    # lol / cs2 / dota2 / valorant / cod / r6 / sc2 / rl
    tournament: str = ""
    team_a: str = ""
    team_b: str = ""
    team_a_id: int = 0
    team_b_id: int = 0
    score_a: int = 0
    score_b: int = 0
    best_of: int = 1            # BO1 / BO3 / BO5
    status: str = "not_started"  # not_started / running / finished / canceled
    scheduled_at: str = ""
    stream_url: str = ""
    league: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class _BoundedCache:
    """Simple TTL cache with max-size eviction (FIFO)."""

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


class PandaScoreClient:
    """
    Async PandaScore REST API client.

    Usage::
        client = PandaScoreClient(api_key="your-key")
        await client.init()
        live = await client.get_live_matches()
        details = await client.get_match_details(match_id=12345)
        await client.close()
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("PANDASCORE_API_KEY is required — esports bot cannot start without it")
        self._api_key = api_key
        self._client = None  # httpx.AsyncClient — created in init()
        self._cache = _BoundedCache()
        self._consecutive_failures = 0
        # Rate-limit tracking: 1000 req/hr free tier, baseline ~360/hr.
        # Counts successful requests in a rolling 3600s window.
        self._req_count: int = 0
        self._req_window_start: float = time.monotonic()

    async def init(self) -> None:
        """Create persistent HTTP client."""
        import httpx
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=3.0),
        )
        logger.info("PandaScoreClient: initialised", base_url=_BASE_URL)

    async def close(self) -> None:
        """Close persistent HTTP client."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ── Public API methods ──────────────────────────────────────────────

    async def get_live_matches(self, game: Optional[str] = None) -> List[EsportsMatch]:
        """
        Get all currently live matches, optionally filtered by game.

        Args:
            game: One of 'lol', 'cs2', 'dota2', 'valorant', or None for all.
        """
        if game and game in GAME_SLUGS:
            path = f"/{GAME_SLUGS[game]}/matches/running"
        else:
            path = "/matches/running"

        cache_key = f"live:{game or 'all'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(path, params={"per_page": 50})
        matches = [self._parse_match(m, game) for m in (data or []) if isinstance(m, dict)]
        matches = [m for m in matches if m is not None]
        self._cache.set(cache_key, matches)
        return matches

    async def get_upcoming_matches(self, game: str, hours_ahead: int = 24) -> List[EsportsMatch]:
        """Get upcoming matches for a game within the next N hours."""
        if game not in GAME_SLUGS:
            return []

        cache_key = f"upcoming:{game}:{hours_ahead}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        end = now + _dt.timedelta(hours=hours_ahead)

        path = f"/{GAME_SLUGS[game]}/matches/upcoming"
        params = {
            "per_page": 50,
            "range[begin_at]": f"{now.isoformat()},{end.isoformat()}",
        }
        data = await self._get(path, params=params)
        matches = [self._parse_match(m, game) for m in (data or []) if isinstance(m, dict)]
        matches = [m for m in matches if m is not None]
        self._cache.set(cache_key, matches)
        return matches

    async def get_match_details(self, match_id: int) -> Optional[Dict[str, Any]]:
        """Get full match details including games/maps played."""
        cache_key = f"match:{match_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(f"/matches/{match_id}")
        if data and isinstance(data, dict):
            self._cache.set(cache_key, data)
        return data

    async def get_team_stats(self, team_id: int, game: str) -> Optional[Dict[str, Any]]:
        """Get team statistics for a specific game."""
        if game not in GAME_SLUGS:
            return None

        cache_key = f"team:{team_id}:{game}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(f"/{GAME_SLUGS[game]}/teams/{team_id}/stats")
        if data and isinstance(data, dict):
            self._cache.set(cache_key, data)
        return data

    async def get_match_games(self, match_id: int) -> List[Dict[str, Any]]:
        """Get individual games/maps within a match (for BO3/BO5)."""
        cache_key = f"games:{match_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(f"/matches/{match_id}/games")
        result = data if isinstance(data, list) else []
        self._cache.set(cache_key, result)
        return result

    async def get_past_matches(
        self, game: str, days_back: int = 90, per_page: int = 100
    ) -> List[EsportsMatch]:
        """
        Get past (finished) matches for a game within the last N days.

        Paginates automatically. Rate-limited: 1 request per 4 seconds to
        stay under 1K req/hour on free tier.

        Args:
            game: One of 'lol', 'cs2', 'dota2', 'valorant'.
            days_back: How many days of history to fetch.
            per_page: Results per page (max 100 on PandaScore).

        Returns:
            List of EsportsMatch objects for finished matches.
        """
        if game not in GAME_SLUGS:
            return []

        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        since = (now - _dt.timedelta(days=days_back)).isoformat()
        until = now.isoformat()
        slug = GAME_SLUGS[game]
        all_matches: List[EsportsMatch] = []
        page = 1
        max_pages = 20  # Safety cap: 20 * 100 = 2000 matches max

        while page <= max_pages:
            params = {
                "per_page": min(per_page, 100),
                "page": page,
                "sort": "-scheduled_at",
                "range[scheduled_at]": f"{since},{until}",
                "filter[status]": "finished",
            }
            data = await self._get(f"/{slug}/matches/past", params=params)

            if not data or not isinstance(data, list) or len(data) == 0:
                break

            for m in data:
                if isinstance(m, dict):
                    parsed = self._parse_match(m, game)
                    if parsed is not None:
                        all_matches.append(parsed)

            if len(data) < per_page:
                break  # Last page

            page += 1
            # Rate limit: 1 req/4s = 900 req/hr (under 1K limit)
            await asyncio.sleep(4.0)

        logger.info(
            "PandaScoreClient: fetched past matches",
            game=game,
            days_back=days_back,
            total=len(all_matches),
            pages=page,
        )
        return all_matches

    async def get_match_games_detail(self, match_id: int) -> List[Dict[str, Any]]:
        """
        Get detailed game/map data for a match, including timelines.

        For LoL: includes timeline frames (gold, objectives, kills per team).
        For CS2: includes round-by-round data (economy, score, bomb events).

        Unlike get_match_games(), this does NOT cache (training data is one-shot).
        """
        data = await self._get(f"/matches/{match_id}/games")
        if not data or not isinstance(data, list):
            return []
        return data

    async def get_tournaments(self, game: str) -> List[Dict[str, Any]]:
        """Get currently running tournaments for a game."""
        if game not in GAME_SLUGS:
            return []

        cache_key = f"tournaments:{game}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(f"/{GAME_SLUGS[game]}/tournaments/running", params={"per_page": 20})
        result = data if isinstance(data, list) else []
        self._cache.set(cache_key, result)
        return result

    # ── Internal helpers ────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        """HTTP GET with retry + exponential backoff."""
        if not self._client:
            logger.warning("PandaScoreClient: not initialised — call init() first")
            return None

        for attempt in range(3):
            try:
                resp = await self._client.get(path, params=params)

                if resp.status_code == 429:
                    # Rate limited — back off
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(
                        "pandascore_rate_limited",
                        retry_after=retry_after,
                        requests_this_hour=self._req_count,
                        budget=1000,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code == 404:
                    return None

                resp.raise_for_status()
                self._consecutive_failures = 0
                # Rate-limit counter: reset window each hour, log at milestones.
                now = time.monotonic()
                if now - self._req_window_start >= 3600.0:
                    self._req_count = 0
                    self._req_window_start = now
                self._req_count += 1
                if self._req_count in (500, 750, 900, 950, 990):
                    logger.warning(
                        "pandascore_rate_limit_budget",
                        requests_this_hour=self._req_count,
                        budget=1000,
                        pct_used=round(self._req_count / 10, 1),
                    )
                elif self._req_count % 100 == 0:
                    logger.info(
                        "pandascore_request_count",
                        requests_this_hour=self._req_count,
                        budget=1000,
                    )
                return resp.json()

            except Exception as exc:
                self._consecutive_failures += 1
                backoff = min(
                    _BASE_BACKOFF * (2 ** attempt),
                    _MAX_BACKOFF,
                )
                logger.debug(
                    "PandaScoreClient: request failed",
                    path=path,
                    attempt=attempt + 1,
                    error=str(exc),
                    backoff_s=backoff,
                )
                if attempt < 2:
                    # Add ±10% jitter to avoid thundering herd after mass failures
                    jitter = random.uniform(0.0, backoff * 0.1)
                    await asyncio.sleep(backoff + jitter)

        return None

    def _parse_match(self, raw: Dict[str, Any], hint_game: Optional[str] = None) -> Optional[EsportsMatch]:
        """Parse a PandaScore match JSON into normalised EsportsMatch."""
        try:
            match_id = int(raw.get("id", 0))
            if not match_id:
                return None

            # Determine game from videogame slug
            vg = raw.get("videogame", {})
            vg_slug = str(vg.get("slug", "")).lower() if isinstance(vg, dict) else ""
            game = hint_game or ""
            if not game:
                for g, slug in GAME_SLUGS.items():
                    if slug == vg_slug or g == vg_slug:
                        game = g
                        break
            if not game:
                game = vg_slug or "unknown"

            # Teams
            opponents = raw.get("opponents", [])
            team_a_data = opponents[0].get("opponent", {}) if len(opponents) > 0 else {}
            team_b_data = opponents[1].get("opponent", {}) if len(opponents) > 1 else {}

            # Score (series level — maps won)
            results = raw.get("results", [])
            score_a = int(results[0].get("score", 0)) if len(results) > 0 else 0
            score_b = int(results[1].get("score", 0)) if len(results) > 1 else 0

            # Status
            status_raw = str(raw.get("status", "not_started")).lower()
            if status_raw == "running":
                status = "running"
            elif status_raw == "finished":
                status = "finished"
            elif status_raw == "canceled":
                status = "canceled"
            else:
                status = "not_started"

            # Tournament / league
            league = raw.get("league", {})
            tournament = raw.get("tournament", {})

            return EsportsMatch(
                match_id=match_id,
                game=game,
                tournament=str(tournament.get("name", "")) if isinstance(tournament, dict) else "",
                team_a=str(team_a_data.get("name", "")),
                team_b=str(team_b_data.get("name", "")),
                team_a_id=int(team_a_data.get("id", 0)),
                team_b_id=int(team_b_data.get("id", 0)),
                score_a=score_a,
                score_b=score_b,
                best_of=int(raw.get("number_of_games", 1) or 1),
                status=status,
                scheduled_at=str(raw.get("scheduled_at", "")),
                stream_url=str(raw.get("official_stream_url", "")),
                league=str(league.get("name", "")) if isinstance(league, dict) else "",
                raw=raw,
            )
        except Exception as exc:
            logger.debug("PandaScoreClient: parse error", error=str(exc))
            return None
