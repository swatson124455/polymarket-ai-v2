"""
Sports data client for real-time game state ingestion.

Integrates free sports APIs (API-Football, TheSportsDB) for scores,
game clock, player stats, and injuries. Short TTL cache for live games.

Supports: NFL, NBA, NHL, FIFA World Cup.
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
import httpx
from structlog import get_logger

logger = get_logger()

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# Cache TTL: 30s for live games, 5min for schedules
LIVE_CACHE_TTL = 30
SCHEDULE_CACHE_TTL = 300


class SportsClient:
    """
    Unified sports data client with multi-API fallback.

    Primary: TheSportsDB (free, no key required for basic endpoints).
    Secondary: API-Football (free tier: 100 req/day, requires key).
    """

    def __init__(self, api_football_key: Optional[str] = None):
        self._api_football_key = api_football_key
        self._http = httpx.AsyncClient(timeout=15.0)
        self._cache: Dict[str, Dict] = {}  # key -> {"data": any, "ts": float}

    def _cache_get(self, key: str, ttl: float) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if time.monotonic() - entry["ts"] < ttl:
                return entry["data"]
        return None

    def _cache_set(self, key: str, data: Any) -> None:
        self._cache[key] = {"data": data, "ts": time.monotonic()}

    # ── Live games ──────────────────────────────────────────────────────

    async def get_live_games(self) -> List[Dict]:
        """Get currently live games across all supported sports."""
        cached = self._cache_get("live_games", LIVE_CACHE_TTL)
        if cached is not None:
            return cached

        games: List[Dict] = []

        # TheSportsDB livescore endpoints
        for sport in ["soccer", "basketball", "icehockey"]:
            try:
                r = await self._http.get(f"{THESPORTSDB_BASE}/livescore.php", params={"s": sport})
                if r.status_code == 200:
                    events = r.json().get("events") or []
                    for ev in events:
                        if not isinstance(ev, dict):
                            continue
                        games.append(self._parse_thesportsdb_event(ev, sport))
            except Exception as e:
                logger.debug("TheSportsDB livescore (%s) failed: %s", sport, e)

        # API-Football for soccer (if key available)
        if self._api_football_key:
            try:
                r = await self._http.get(
                    f"{API_FOOTBALL_BASE}/fixtures",
                    params={"live": "all"},
                    headers={"x-apisports-key": self._api_football_key},
                )
                if r.status_code == 200:
                    for fix in r.json().get("response") or []:
                        games.append(self._parse_api_football_fixture(fix))
            except Exception as e:
                logger.debug("API-Football live fixtures failed: %s", e)

        self._cache_set("live_games", games)
        return games

    async def get_upcoming_games(self, sport: str = "soccer", days: int = 7) -> List[Dict]:
        """Get upcoming scheduled games."""
        cache_key = f"upcoming_{sport}_{days}"
        cached = self._cache_get(cache_key, SCHEDULE_CACHE_TTL)
        if cached is not None:
            return cached

        games: List[Dict] = []
        try:
            r = await self._http.get(
                f"{THESPORTSDB_BASE}/eventsround.php",
                params={"id": "4328" if sport == "soccer" else "4387", "r": "1", "s": "2025-2026"},
            )
            if r.status_code == 200:
                for ev in r.json().get("events") or []:
                    games.append(self._parse_thesportsdb_event(ev, sport))
        except Exception as e:
            logger.debug("TheSportsDB upcoming failed: %s", e)

        self._cache_set(cache_key, games)
        return games

    async def get_team_stats(self, team_name: str) -> Optional[Dict]:
        """Get team stats for prediction models."""
        try:
            r = await self._http.get(f"{THESPORTSDB_BASE}/searchteams.php", params={"t": team_name})
            if r.status_code == 200:
                teams = r.json().get("teams") or []
                if teams:
                    return self._parse_team(teams[0])
        except Exception as e:
            logger.debug("TheSportsDB team search failed: %s", e)
        return None

    # ── Parsers ─────────────────────────────────────────────────────────

    def _parse_thesportsdb_event(self, ev: Dict, sport: str) -> Dict:
        try:
            score_home = int(ev.get("intHomeScore") or 0)
            score_away = int(ev.get("intAwayScore") or 0)
        except (ValueError, TypeError):
            score_home, score_away = 0, 0

        return {
            "event_id": ev.get("idEvent", ""),
            "sport": sport,
            "home_team": ev.get("strHomeTeam", ""),
            "away_team": ev.get("strAwayTeam", ""),
            "score_home": score_home,
            "score_away": score_away,
            "status": ev.get("strStatus", ""),
            "elapsed_pct": self._estimate_elapsed_pct(ev, sport),
            "league": ev.get("strLeague", ""),
            "date": ev.get("dateEvent", ""),
            "market_id": None,  # Must be mapped externally
        }

    def _parse_api_football_fixture(self, fix: Dict) -> Dict:
        fixture = fix.get("fixture", {})
        teams = fix.get("teams", {})
        goals = fix.get("goals", {})
        status = fixture.get("status", {})

        elapsed = status.get("elapsed") or 0
        total_minutes = 90 if status.get("short") != "ET" else 120
        elapsed_pct = min(100, (elapsed / total_minutes) * 100)

        return {
            "event_id": str(fixture.get("id", "")),
            "sport": "soccer",
            "home_team": teams.get("home", {}).get("name", ""),
            "away_team": teams.get("away", {}).get("name", ""),
            "score_home": goals.get("home", 0) or 0,
            "score_away": goals.get("away", 0) or 0,
            "status": status.get("long", ""),
            "elapsed_pct": elapsed_pct,
            "league": fix.get("league", {}).get("name", ""),
            "date": fixture.get("date", ""),
            "market_id": None,
        }

    def _parse_team(self, team: Dict) -> Dict:
        return {
            "name": team.get("strTeam", ""),
            "country": team.get("strCountry", ""),
            "league": team.get("strLeague", ""),
            "stadium": team.get("strStadium", ""),
            "formed_year": team.get("intFormedYear", ""),
        }

    def _estimate_elapsed_pct(self, ev: Dict, sport: str) -> float:
        """Estimate game completion percentage from TheSportsDB status."""
        status = (ev.get("strStatus") or "").lower()
        progress = ev.get("strProgress") or ev.get("intSpectators") or ""
        if "ft" in status or "finished" in status:
            return 100.0
        if "ht" in status or "half" in status:
            return 50.0
        if "1st" in status:
            return 25.0
        if "2nd" in status:
            return 75.0
        # Try to parse minute from progress field
        try:
            mins = int(str(progress).replace("'", "").strip())
            total = 90 if sport == "soccer" else 48 if sport == "basketball" else 60
            return min(100, (mins / total) * 100)
        except (ValueError, TypeError):
            pass
        return 0.0

    async def close(self) -> None:
        await self._http.aclose()
