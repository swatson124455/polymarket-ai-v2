"""
Esports Market Scanner — find Polymarket esports markets by event/team.

Mirrors sports/markets/sports_market_scanner.py pattern.

Scans Polymarket for esports markets using:
  - Category filter: "esports" tag
  - Keyword match: team names, tournament names, game title
  - Market type classification: match_winner, map_winner, tournament_winner, total_maps

Results cached with 120s TTL.

Usage::
    scanner = EsportsMarketScanner(db=db)
    markets = await scanner.find_markets_for_match("12345", "lol")
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

_CACHE_TTL = 120.0
_CACHE: Dict[str, tuple] = {}   # key → (timestamp, results)
_CACHE_LOCK = asyncio.Lock()
_CACHE_MAX = 200

# Game-specific keywords for market matching
_GAME_KEYWORDS: Dict[str, List[str]] = {
    "lol": ["league of legends", "lol", "lck", "lec", "lpl", "lcs", "worlds", "msi", "rift"],
    "cs2": ["counter-strike", "cs2", "csgo", "cs:go", "blast", "esl", "pgl", "iem", "faceit"],
    "dota2": ["dota", "dota 2", "the international", "ti", "dpc"],
    "valorant": ["valorant", "vct", "champions tour", "vcl", "vrl"],
}

# Market type patterns
_MARKET_TYPE_PATTERNS = {
    "match_winner": ["win", "beat", "defeat", "winner", "vs", "versus"],
    "map_winner": ["map", "game 1", "game 2", "game 3", "game 4", "game 5"],
    "tournament_winner": ["tournament", "championship", "champion", "split", "season"],
    "total_maps": ["total maps", "over", "under", "maps played"],
    "first_blood": ["first blood", "first kill"],
    "props": ["mvp", "kills", "assists", "deaths", "kda"],
}


class EsportsMarketScanner:
    """
    Scans Polymarket for esports markets.

    Usage::
        scanner = EsportsMarketScanner(db=db)
        markets = await scanner.find_markets_for_match("12345", "lol")
    """

    def __init__(self, db=None, polymarket_client=None) -> None:
        self._db = db
        self._poly = polymarket_client

    async def find_markets_for_match(
        self,
        match_id: str,
        game: str,
        team_names: Optional[List[str]] = None,
        db=None,
    ) -> List[Dict[str, Any]]:
        """
        Find Polymarket markets related to a specific match.

        Args:
            match_id: PandaScore match ID.
            game: Game title (lol, cs2, dota2, valorant).
            team_names: Optional team names for keyword matching.
            db: Database session (optional).

        Returns:
            List of market dicts with: market_id, token_id, price, question, market_type.
        """
        cache_key = f"match:{match_id}:{game}"
        cached = await self._get_cache(cache_key)
        if cached is not None:
            return cached

        results = []

        # Strategy 1: keyword search across active esports markets
        keywords = list(_GAME_KEYWORDS.get(game, []))
        if team_names:
            keywords.extend(team_names)

        if self._poly:
            try:
                all_markets = await asyncio.wait_for(
                    self._poly.get_markets(active=True, limit=500),
                    timeout=10.0,
                )
                for market in (all_markets or []):
                    question = str(market.get("question", "")).lower()
                    category = str(market.get("category", "")).lower()

                    # Must be esports-related
                    if category != "esports" and not any(kw in question for kw in keywords):
                        continue

                    # Check for team name matches
                    if team_names:
                        if not any(t.lower() in question for t in team_names):
                            continue

                    # Classify market type
                    market_type = self._classify_market_type(question)

                    # Extract price and token info
                    tokens = market.get("tokens", [])
                    if not tokens:
                        continue

                    token = tokens[0]
                    price_raw = token.get("outcomePrice") or token.get("price")
                    try:
                        price = float(price_raw) if price_raw else None
                    except (ValueError, TypeError):
                        price = None

                    results.append({
                        "market_id": str(market.get("id", "")),
                        "token_id": str(token.get("tokenId") or token.get("token_id", "")),
                        "price": price,
                        "question": market.get("question", ""),
                        "market_type": market_type,
                        "match_id": match_id,
                        "game": game,
                    })
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsMarketScanner: Polymarket scan failed", error=str(exc))

        await self._set_cache(cache_key, results)
        return results

    async def find_all_esports_markets(
        self, game: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Find ALL active esports markets on Polymarket, optionally filtered by game.

        Returns list of market dicts.
        """
        cache_key = f"all_esports:{game or 'all'}"
        cached = await self._get_cache(cache_key)
        if cached is not None:
            return cached

        results = []
        keywords = []
        if game and game in _GAME_KEYWORDS:
            keywords = _GAME_KEYWORDS[game]
        else:
            for kws in _GAME_KEYWORDS.values():
                keywords.extend(kws)
            keywords.append("esports")

        if self._poly:
            try:
                all_markets = await asyncio.wait_for(
                    self._poly.get_markets(active=True, limit=500),
                    timeout=15.0,
                )
                for market in (all_markets or []):
                    question = str(market.get("question", "")).lower()
                    category = str(market.get("category", "")).lower()

                    if category == "esports" or any(kw in question for kw in keywords):
                        tokens = market.get("tokens", [])
                        if not tokens:
                            continue
                        token = tokens[0]
                        price_raw = token.get("outcomePrice") or token.get("price")
                        try:
                            price = float(price_raw) if price_raw else None
                        except (ValueError, TypeError):
                            price = None

                        results.append({
                            "market_id": str(market.get("id", "")),
                            "token_id": str(token.get("tokenId") or token.get("token_id", "")),
                            "price": price,
                            "question": market.get("question", ""),
                            "market_type": self._classify_market_type(question),
                            "game": self._detect_game(question),
                        })
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsMarketScanner: full scan failed", error=str(exc))

        await self._set_cache(cache_key, results)
        return results

    @staticmethod
    def _classify_market_type(question: str) -> str:
        """Classify market type from question text."""
        q = question.lower()
        for mtype, patterns in _MARKET_TYPE_PATTERNS.items():
            if any(p in q for p in patterns):
                return mtype
        return "match_winner"

    @staticmethod
    def _detect_game(question: str) -> str:
        """Detect which game a market is for from the question text."""
        q = question.lower()
        for game, keywords in _GAME_KEYWORDS.items():
            if any(kw in q for kw in keywords):
                return game
        return "unknown"

    async def _get_cache(self, key: str) -> Optional[List]:
        async with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > _CACHE_TTL:
                del _CACHE[key]
                return None
            return value

    async def _set_cache(self, key: str, value: List) -> None:
        async with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), value)
            # Evict oldest if over limit
            while len(_CACHE) > _CACHE_MAX:
                oldest_key = next(iter(_CACHE))
                del _CACHE[oldest_key]
