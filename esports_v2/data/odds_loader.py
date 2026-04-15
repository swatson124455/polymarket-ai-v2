"""
OddsPapi historical odds loader for EsportsBot v2.

Fetches historical Pinnacle closing odds for CS2 and LoL matches via OddsPapi.
Returns a lookup dict suitable for CLV enrichment via clv.py:enrich_with_clv().

OddsPapi API:
  - Base: https://api.oddspapi.io
  - Auth: apiKey query param
  - Historical odds available for up to 3 months
  - Rate limit: undocumented (free tier), self-throttle to be safe

Workflow:
  1. Discover sport IDs for CS2 and LoL
  2. Fetch finished fixtures within date range
  3. For each fixture, fetch Pinnacle odds (market 101 = match winner)
  4. Extract closing odds (last recorded price before match start)
  5. Build match_key -> (odds_a, odds_b) lookup

Usage::
    loader = OddsPapiLoader(api_key="papi-...")
    odds = loader.fetch_odds(game="cs2", days_back=90)
    # odds = {"team_a||team_b||2026-01-15": (1.85, 2.05), ...}
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://oddspapi.io/api"
_REQ_DELAY = 2.0  # seconds between requests
_FIXTURE_PAGE_DAYS = 10  # max date range per fixtures request


def _normalize_team(name: str) -> str:
    """Normalize team name for matching across data sources."""
    return name.strip().lower()


def make_match_key(team_a: str, team_b: str, date: str) -> str:
    """
    Build a canonical match key for odds lookup.

    Key format: "teamA_norm||teamB_norm||YYYY-MM-DD"
    Always sorted alphabetically so A vs B == B vs A.
    """
    a = _normalize_team(team_a)
    b = _normalize_team(team_b)
    day = date[:10] if date and len(date) >= 10 else date or ""
    if a > b:
        a, b = b, a
    return f"{a}||{b}||{day}"


class OddsPapiLoader:
    """Synchronous OddsPapi REST client for historical Pinnacle odds."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("OddsPapi API key required")
        self._api_key = api_key
        self._session = requests.Session()
        self._request_count = 0
        self._sport_cache: Dict[str, int] = {}  # game -> sportId

    def discover_sports(self) -> Dict[str, int]:
        """
        Fetch available sports and find CS2/LoL sport IDs.

        Returns:
            Dict mapping game name ('cs2', 'lol') to sportId.
        """
        data = self._get("/v4/sports")
        if not data or not isinstance(data, list):
            logger.error("oddspapi_sports_failed")
            return {}

        mapping = {}
        cs2_keywords = ["counter-strike", "cs2", "csgo", "cs:go"]
        lol_keywords = ["league of legends", "lol"]

        for sport in data:
            if not isinstance(sport, dict):
                continue
            name = (sport.get("sportName") or sport.get("name") or "").lower()
            slug = (sport.get("sportSlug") or sport.get("slug") or "").lower()
            sport_id = sport.get("sportId") or sport.get("id")
            if not sport_id:
                continue

            combined = f"{name} {slug}"
            if any(kw in combined for kw in cs2_keywords):
                mapping["cs2"] = int(sport_id)
                logger.info(f"oddspapi_sport_found game=cs2 id={sport_id} name={name}")
            elif any(kw in combined for kw in lol_keywords):
                mapping["lol"] = int(sport_id)
                logger.info(f"oddspapi_sport_found game=lol id={sport_id} name={name}")

        self._sport_cache = mapping
        return mapping

    def fetch_odds(
        self,
        game: str,
        days_back: int = 90,
    ) -> Dict[str, Tuple[float, float]]:
        """
        Fetch Pinnacle closing odds for finished matches.

        Args:
            game: 'cs2' or 'lol'.
            days_back: How far back (max ~90 days due to OddsPapi limit).

        Returns:
            Dict mapping match_key -> (pinnacle_odds_a, pinnacle_odds_b).
            match_key format: "team_a_norm||team_b_norm||YYYY-MM-DD"
        """
        if game not in self._sport_cache:
            self.discover_sports()
        sport_id = self._sport_cache.get(game)
        if not sport_id:
            logger.error(f"oddspapi_no_sport game={game}")
            return {}

        # Fetch fixtures in 10-day windows
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=min(days_back, 90))
        fixtures = self._fetch_fixtures(sport_id, start, now)
        logger.info(f"oddspapi_fixtures game={game} count={len(fixtures)}")

        # Fetch Pinnacle odds for each fixture
        odds_lookup: Dict[str, Tuple[float, float]] = {}
        fetched = 0
        skipped = 0

        for fx in fixtures:
            fixture_id = fx.get("fixtureId") or fx.get("id")
            p1 = fx.get("participant1Name") or fx.get("homeTeam") or ""
            p2 = fx.get("participant2Name") or fx.get("awayTeam") or ""
            start_time = fx.get("startTime") or fx.get("scheduledAt") or ""

            if not fixture_id or not p1 or not p2:
                skipped += 1
                continue

            odds = self._fetch_pinnacle_odds(str(fixture_id))
            if odds:
                key = make_match_key(p1, p2, start_time)
                odds_lookup[key] = odds
                fetched += 1
            else:
                skipped += 1

            time.sleep(_REQ_DELAY)

        logger.info(
            f"oddspapi_odds_done game={game} fetched={fetched} "
            f"skipped={skipped} total_requests={self._request_count}"
        )
        return odds_lookup

    def fetch_all_odds(self, days_back: int = 90) -> Dict[str, Tuple[float, float]]:
        """Fetch odds for both CS2 and LoL."""
        combined = {}
        for game in ("cs2", "lol"):
            odds = self.fetch_odds(game, days_back)
            combined.update(odds)
        return combined

    def save_odds(self, odds: Dict[str, Tuple[float, float]], filepath: str | Path) -> None:
        """Save odds lookup to JSON for reuse."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: list(v) for k, v in odds.items()}
        with open(filepath, "w") as f:
            json.dump(serializable, f, indent=2)
        logger.info(f"oddspapi_saved path={filepath} count={len(odds)}")

    @staticmethod
    def load_odds(filepath: str | Path) -> Dict[str, Tuple[float, float]]:
        """Load previously saved odds lookup."""
        filepath = Path(filepath)
        if not filepath.exists():
            return {}
        with open(filepath, "r") as f:
            data = json.load(f)
        return {k: tuple(v) for k, v in data.items()}

    def _fetch_fixtures(
        self, sport_id: int, start: datetime, end: datetime
    ) -> List[dict]:
        """Fetch finished fixtures in 10-day windows."""
        all_fixtures = []
        window_start = start

        while window_start < end:
            window_end = min(window_start + timedelta(days=_FIXTURE_PAGE_DAYS), end)
            params = {
                "sportId": sport_id,
                "fromDate": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "toDate": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "statusId": 2,  # finished
            }
            data = self._get("/v4/fixtures", params=params)
            if data and isinstance(data, list):
                all_fixtures.extend(data)
                logger.info(
                    f"oddspapi_fixtures_page from={window_start.date()} "
                    f"to={window_end.date()} count={len(data)}"
                )
            window_start = window_end
            time.sleep(_REQ_DELAY)

        return all_fixtures

    def _fetch_pinnacle_odds(self, fixture_id: str) -> Optional[Tuple[float, float]]:
        """
        Fetch Pinnacle match winner odds for a fixture.

        Returns (odds_a, odds_b) as decimal odds, or None if not available.
        Extracts the most recent (closing) price from historical data.
        """
        params = {
            "fixtureId": fixture_id,
            "bookmakers": "pinnacle",
        }
        data = self._get("/v4/historical-odds", params=params)
        if not data or not isinstance(data, dict):
            return None

        # Navigate: markets -> 101 (match winner) -> outcomes
        markets = data.get("markets", data)
        match_winner = markets.get("101") or markets.get(101)
        if not match_winner or not isinstance(match_winner, dict):
            # Try first available market
            for mk, mv in markets.items():
                if isinstance(mv, dict) and "outcomes" in mv:
                    match_winner = mv
                    break
            if not match_winner:
                return None

        outcomes = match_winner.get("outcomes", {})
        if not outcomes:
            return None

        # Extract home (101) and away (103) closing odds
        home_odds = self._extract_closing_price(outcomes.get("101") or outcomes.get(101))
        away_odds = self._extract_closing_price(outcomes.get("103") or outcomes.get(103))

        # Fallback: try iterating outcomes by bookmakerOutcomeId
        if home_odds is None or away_odds is None:
            for oid, odata in outcomes.items():
                price = self._extract_closing_price(odata)
                if price is None:
                    continue
                boid = self._get_bookmaker_outcome_id(odata)
                if boid == "home" and home_odds is None:
                    home_odds = price
                elif boid == "away" and away_odds is None:
                    away_odds = price

        if home_odds is None or away_odds is None:
            return None
        if home_odds <= 1.0 or away_odds <= 1.0:
            return None

        return (home_odds, away_odds)

    @staticmethod
    def _extract_closing_price(outcome_data) -> Optional[float]:
        """Extract the most recent (closing) price from an outcome's player data."""
        if not outcome_data or not isinstance(outcome_data, dict):
            return None

        players = outcome_data.get("players", {})
        if not players:
            # outcome_data might directly have price entries
            if "price" in outcome_data:
                try:
                    return float(outcome_data["price"])
                except (ValueError, TypeError):
                    return None
            return None

        # Find the most recent price entry
        latest_time = ""
        latest_price = None
        for pid, pdata in players.items():
            if not isinstance(pdata, dict):
                continue
            created = pdata.get("createdAt", "")
            price = pdata.get("price")
            if price is not None and created >= latest_time:
                try:
                    latest_price = float(price)
                    latest_time = created
                except (ValueError, TypeError):
                    continue

        return latest_price

    @staticmethod
    def _get_bookmaker_outcome_id(outcome_data) -> Optional[str]:
        """Extract bookmakerOutcomeId from outcome data."""
        if not outcome_data or not isinstance(outcome_data, dict):
            return None
        players = outcome_data.get("players", {})
        for pid, pdata in players.items():
            if isinstance(pdata, dict):
                return pdata.get("bookmakerOutcomeId")
        return outcome_data.get("bookmakerOutcomeId")

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[any]:
        """HTTP GET with retry and API key injection."""
        if params is None:
            params = {}
        params["apiKey"] = self._api_key

        for attempt in range(3):
            try:
                resp = self._session.get(
                    f"{_BASE_URL}{path}",
                    params=params,
                    timeout=15,
                )
                self._request_count += 1

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "30"))
                    logger.warning(f"oddspapi_rate_limited retry_after={retry_after}")
                    time.sleep(retry_after)
                    continue

                if resp.status_code == 404:
                    return None

                if resp.status_code == 401:
                    logger.error("oddspapi_auth_failed — check ODDSPAPI_API_KEY")
                    return None

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as e:
                logger.warning(f"oddspapi_request_error attempt={attempt+1} error={e}")
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))

        return None
