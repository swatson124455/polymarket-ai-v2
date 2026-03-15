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

    Rate counter is CLASS-LEVEL: all instances (EsportsBot, EsportsLiveBot)
    share one request counter per hour, preventing aggregate overrun of the
    1000 req/hr free-tier quota.

    Usage::
        client = PandaScoreClient(api_key="your-key")
        await client.init()
        live = await client.get_live_matches()
        details = await client.get_match_details(match_id=12345)
        await client.close()
    """

    # Shared rate-limit counter across ALL PandaScoreClient instances.
    # EsportsBot + EsportsLiveBot each create their own PandaScoreClient,
    # so class-level state ensures both share one 1000 req/hr budget.
    _shared_req_count: int = 0
    _shared_req_window_start: float = 0.0  # set to time.monotonic() on first use
    _shared_lock: Optional[asyncio.Lock] = None  # lazy-init in asyncio context

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("PANDASCORE_API_KEY is required — esports bot cannot start without it")
        self._api_key = api_key
        self._client = None  # httpx.AsyncClient — created in init()
        self._cache = _BoundedCache()
        self._consecutive_failures = 0

    @classmethod
    def get_remaining_budget(cls) -> int:
        """Return approximate remaining requests in the current hour window."""
        return max(0, 950 - cls._shared_req_count)

    @classmethod
    def _get_shared_lock(cls) -> asyncio.Lock:
        """Lazy-init class-level lock (must be called inside asyncio event loop)."""
        if cls._shared_lock is None:
            cls._shared_lock = asyncio.Lock()
        return cls._shared_lock

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
            http2=True,
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

    async def search_team_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Search PandaScore for a team by name. Returns best match or None.

        Uses GET /teams?search[name]=X&per_page=5. Costs 1 API request.
        """
        if not name or len(name) < 2:
            return None
        data = await self._get("/teams", params={"search[name]": name, "per_page": 5})
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        # Prefer exact name match, else first result
        name_lower = name.lower()
        for team in data:
            if isinstance(team, dict) and str(team.get("name", "")).lower() == name_lower:
                return team
        return data[0] if isinstance(data[0], dict) else None

    async def get_team_roster(self, team_id: int) -> Optional[List[str]]:
        """Get sorted player slugs for a team. Costs 1 API request (cached)."""
        cache_key = f"roster:{team_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(f"/teams/{team_id}")
        if not data or not isinstance(data, dict):
            return None
        players = data.get("players", [])
        if not players or not isinstance(players, list):
            return None
        slugs = sorted(p.get("slug", "") for p in players if isinstance(p, dict))
        self._cache.set(cache_key, slugs)
        return slugs

    async def get_team_matches(
        self, team_id: int, game: str, per_page: int = 20
    ) -> List[EsportsMatch]:
        """Get recent finished matches for a specific team. Costs 1 API request."""
        if game not in GAME_SLUGS:
            return []
        slug = GAME_SLUGS[game]
        data = await self._get(
            f"/{slug}/matches/past",
            params={
                "filter[opponent_id]": team_id,
                "per_page": per_page,
                "sort": "-scheduled_at",
                "filter[status]": "finished",
            },
        )
        if not data or not isinstance(data, list):
            return []
        matches = [self._parse_match(m, game) for m in data if isinstance(m, dict)]
        return [m for m in matches if m is not None]

    # ── Internal helpers ────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        """HTTP GET with retry + exponential backoff."""
        if not self._client:
            logger.warning("PandaScoreClient: not initialised — call init() first")
            return None

        # Hard circuit breaker: refuse requests above 950/hr to avoid 429s
        _HARD_LIMIT = 950
        async with self._get_shared_lock():
            now = time.monotonic()
            if PandaScoreClient._shared_req_window_start == 0.0:
                PandaScoreClient._shared_req_window_start = now
            if now - PandaScoreClient._shared_req_window_start >= 3600.0:
                PandaScoreClient._shared_req_count = 0
                PandaScoreClient._shared_req_window_start = now
            if PandaScoreClient._shared_req_count >= _HARD_LIMIT:
                _remaining = 3600.0 - (now - PandaScoreClient._shared_req_window_start)
                logger.error(
                    "pandascore_circuit_breaker_open",
                    requests_this_hour=PandaScoreClient._shared_req_count,
                    hard_limit=_HARD_LIMIT,
                    window_resets_in_s=round(_remaining, 0),
                )
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
                        requests_this_hour=PandaScoreClient._shared_req_count,
                        budget=1000,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code == 404:
                    return None

                resp.raise_for_status()
                self._consecutive_failures = 0
                # Shared rate-limit counter (class-level, all 3 esports bot instances).
                # Reset window each hour; log at milestones.
                async with self._get_shared_lock():
                    now = time.monotonic()
                    if PandaScoreClient._shared_req_window_start == 0.0:
                        PandaScoreClient._shared_req_window_start = now
                    if now - PandaScoreClient._shared_req_window_start >= 3600.0:
                        PandaScoreClient._shared_req_count = 0
                        PandaScoreClient._shared_req_window_start = now
                    PandaScoreClient._shared_req_count += 1
                    _cur = PandaScoreClient._shared_req_count
                if _cur in (500, 750, 900, 950, 990):
                    logger.warning(
                        "pandascore_rate_limit_budget",
                        requests_this_hour=_cur,
                        budget=1000,
                        pct_used=round(_cur / 10, 1),
                    )
                elif _cur % 100 == 0:
                    logger.info(
                        "pandascore_request_count",
                        requests_this_hour=_cur,
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

    @staticmethod
    def extract_draft(game_data: Dict[str, Any], team_a_id: int, team_b_id: int) -> Optional[Dict[str, Any]]:
        """Extract picks/bans from a PandaScore game-level dict.

        PandaScore game objects contain a 'teams' array with picks/bans per team.
        Also checks for 'players' array which contains per-player champion/agent.

        Returns:
            Dict with team_a_picks, team_a_bans, team_b_picks, team_b_bans
            (each a list of champion/agent/hero name strings), or None.
        """
        if not isinstance(game_data, dict):
            return None

        draft: Dict[str, list] = {
            "team_a_picks": [], "team_a_bans": [],
            "team_b_picks": [], "team_b_bans": [],
        }

        # Method 1: teams array with picks/bans (LoL, Dota2, R6)
        teams = game_data.get("teams", [])
        if isinstance(teams, list) and len(teams) >= 2:
            for team_obj in teams:
                if not isinstance(team_obj, dict):
                    continue
                tid = team_obj.get("team", {}).get("id") if isinstance(team_obj.get("team"), dict) else team_obj.get("team_id")
                prefix = "team_a" if tid == team_a_id else "team_b" if tid == team_b_id else None
                if not prefix:
                    continue
                # picks
                picks = team_obj.get("picks", [])
                if isinstance(picks, list):
                    for p in picks:
                        name = None
                        if isinstance(p, dict):
                            ch = p.get("champion", p.get("hero", p.get("agent", {})))
                            if isinstance(ch, dict):
                                name = ch.get("name", ch.get("localized_name", ""))
                            elif isinstance(ch, str):
                                name = ch
                            elif isinstance(p.get("name"), str):
                                name = p["name"]
                        if name:
                            draft[f"{prefix}_picks"].append(name)
                # bans
                bans = team_obj.get("bans", [])
                if isinstance(bans, list):
                    for b in bans:
                        name = None
                        if isinstance(b, dict):
                            ch = b.get("champion", b.get("hero", b.get("agent", {})))
                            if isinstance(ch, dict):
                                name = ch.get("name", ch.get("localized_name", ""))
                            elif isinstance(ch, str):
                                name = ch
                            elif isinstance(b.get("name"), str):
                                name = b["name"]
                        if name:
                            draft[f"{prefix}_bans"].append(name)

        # Method 2: players array (Valorant agents, LoL champions per player)
        if not draft["team_a_picks"] and not draft["team_b_picks"]:
            players = game_data.get("players", [])
            if isinstance(players, list):
                for pl in players:
                    if not isinstance(pl, dict):
                        continue
                    tid = pl.get("team", {}).get("id") if isinstance(pl.get("team"), dict) else pl.get("team_id")
                    prefix = "team_a" if tid == team_a_id else "team_b" if tid == team_b_id else None
                    if not prefix:
                        continue
                    # champion / agent / hero
                    for key in ("champion", "agent", "hero"):
                        ch = pl.get(key)
                        if isinstance(ch, dict):
                            name = ch.get("name", ch.get("localized_name", ""))
                            if name:
                                draft[f"{prefix}_picks"].append(name)
                                break
                        elif isinstance(ch, str) and ch:
                            draft[f"{prefix}_picks"].append(ch)
                            break

        # Only return if we got at least some picks
        total_picks = len(draft["team_a_picks"]) + len(draft["team_b_picks"])
        if total_picks == 0:
            return None

        return draft

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
