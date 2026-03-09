"""
OddsPapi API Client — Esports odds from 350+ bookmakers (Pinnacle focus).

API key required (free tier: 250 requests/month): https://oddspapi.io
Base URL: https://api.oddspapi.io/v4

Provides Pinnacle closing lines for CLV (Closing Line Value) benchmarking.
CLV against Pinnacle is the gold standard for edge validation in esports betting.

Usage::
    client = OddsPapiClient(api_key="YOUR_KEY")
    fixtures = await client.get_fixtures(sport_id=17, days_back=3)
    closing = await client.get_pinnacle_closing_line(fixture_id="id123")
    # closing = {"closing_prob_home": 0.55, "closing_prob_away": 0.45, ...}
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

BASE_URL = "https://api.oddspapi.io/v4"

# Game title → OddsPapi sport ID
SPORT_IDS = {
    "dota2": 16,
    "cs2": 17,
    "lol": 18,
    "cod": 56,
    "rl": 59,
    "valorant": 61,
}

# Module-level TTL cache: cache_key → (data, mono_ts)
_cache: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 3600  # 1 hour — CLV data doesn't change after settlement
_CACHE_MAX = 200
_last_request: float = 0.0
_MIN_INTERVAL = 5.5  # 5.5s between requests — historical-odds has 5s cooldown


async def _rate_limited_get(
    path: str, params: Dict, cache_key: Optional[str] = None,
) -> Optional[Any]:
    """GET request with rate limiting and aggressive caching."""
    global _last_request

    if cache_key is None:
        cache_key = f"{path}:{sorted(params.items())}"
    cached = _cache.get(cache_key)
    if cached and (_time.monotonic() - cached[1]) < _CACHE_TTL:
        return cached[0]

    # Rate limit (conservative — 250 req/month budget)
    now = _time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = _time.monotonic()

    url = f"{BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    logger.debug("oddspapi: rate limited", path=path)
                    return None
                if resp.status == 402:
                    logger.warning("oddspapi: quota exhausted (250/month)")
                    return None
                if resp.status != 200:
                    logger.debug("oddspapi: non-200", path=path, status=resp.status)
                    return None
                data = await resp.json()

        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            del _cache[oldest]
        _cache[cache_key] = (data, _time.monotonic())
        return data
    except Exception as exc:
        logger.debug("oddspapi: request failed", path=path, error=str(exc))
        return None


class OddsPapiClient:
    """Async client for OddsPapi — esports odds from Pinnacle and 350+ books."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def get_fixtures(
        self, game: str, days_back: int = 3,
    ) -> List[Dict[str, Any]]:
        """Get recent fixtures (matches) for a game.

        Returns list of {fixture_id, home, away, start_time, status}.
        Limited to settled matches for CLV computation.
        """
        sport_id = SPORT_IDS.get(game)
        if sport_id is None:
            return []

        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=days_back)

        data = await _rate_limited_get(
            "/fixtures",
            params={
                "sportId": sport_id,
                "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "hasOdds": "true",
                "apiKey": self._api_key,
            },
            cache_key=f"fixtures:{game}:{days_back}",
        )
        if not data or not isinstance(data, list):
            return []

        results = []
        for f in data:
            fixture_id = f.get("id") or f.get("fixtureId")
            if not fixture_id:
                continue
            participants = f.get("participants", [])
            home = participants[0].get("name", "") if len(participants) > 0 else ""
            away = participants[1].get("name", "") if len(participants) > 1 else ""
            results.append({
                "fixture_id": str(fixture_id),
                "home": home,
                "away": away,
                "start_time": f.get("startTime", ""),
                "status": f.get("status", ""),
            })
        return results

    async def get_pinnacle_closing_line(
        self, fixture_id: str,
    ) -> Optional[Dict[str, float]]:
        """Get Pinnacle's closing line for a fixture (Match Winner market).

        Returns {closing_prob_home, closing_prob_away, closing_odds_home,
                 closing_odds_away} or None.

        The closing line is the last price update before match start — the
        sharpest benchmark available for CLV computation.
        """
        data = await _rate_limited_get(
            "/historical-odds",
            params={
                "fixtureId": fixture_id,
                "bookmakers": "pinnacle",
                "apiKey": self._api_key,
            },
            cache_key=f"hist:{fixture_id}",
        )
        if not data or not isinstance(data, dict):
            return None

        # Navigate: bookmakers → pinnacle → markets → 171 (Match Winner) → outcomes
        bookmakers = data.get("bookmakers", {})
        pinnacle = bookmakers.get("pinnacle", {})
        markets = pinnacle.get("markets", {})
        match_winner = markets.get("171", {})  # Market 171 = Match Winner
        outcomes = match_winner.get("outcomes", {})

        # Outcome "1" = home, "2" = away
        home_prices = self._extract_prices(outcomes.get("1", {}))
        away_prices = self._extract_prices(outcomes.get("2", {}))

        if not home_prices or not away_prices:
            return None

        # Last price in the array is the closing line
        closing_home = home_prices[-1]
        closing_away = away_prices[-1]

        if closing_home <= 1.0 or closing_away <= 1.0:
            return None

        # Convert decimal odds to implied probability
        prob_home = 1.0 / closing_home
        prob_away = 1.0 / closing_away

        # Normalize to remove vig (overround)
        total = prob_home + prob_away
        if total > 0:
            prob_home /= total
            prob_away /= total

        return {
            "closing_prob_home": round(prob_home, 4),
            "closing_prob_away": round(prob_away, 4),
            "closing_odds_home": round(closing_home, 3),
            "closing_odds_away": round(closing_away, 3),
        }

    @staticmethod
    def _extract_prices(outcome_data: Dict) -> List[float]:
        """Extract price history from an outcome's nested players/entries."""
        prices = []
        players = outcome_data.get("players", {})
        for player_key, entries in players.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                price = entry.get("price")
                if price is not None and isinstance(price, (int, float)):
                    prices.append(float(price))
        return prices

    async def compute_clv(
        self, fixture_id: str, our_prob: float, side: str = "home",
    ) -> Optional[Dict[str, float]]:
        """Compute CLV (Closing Line Value) for one of our predictions.

        Args:
            fixture_id: OddsPapi fixture ID.
            our_prob: Our predicted probability for the side we bet on.
            side: "home" or "away" — which side we bet on.

        Returns:
            {clv, our_prob, closing_prob, closing_odds} or None.
            Positive clv = we beat the closing line = genuine edge.
        """
        closing = await self.get_pinnacle_closing_line(fixture_id)
        if closing is None:
            return None

        if side == "home":
            closing_prob = closing["closing_prob_home"]
            closing_odds = closing["closing_odds_home"]
        else:
            closing_prob = closing["closing_prob_away"]
            closing_odds = closing["closing_odds_away"]

        clv = our_prob - closing_prob

        return {
            "clv": round(clv, 4),
            "our_prob": round(our_prob, 4),
            "closing_prob": round(closing_prob, 4),
            "closing_odds": round(closing_odds, 3),
        }
