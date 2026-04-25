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
from typing import Any, Dict, List, Optional, Tuple

from structlog import get_logger

logger = get_logger()

_CACHE_TTL = 120.0
_CACHE: Dict[str, tuple] = {}   # key → (timestamp, results)
_CACHE_LOCK = asyncio.Lock()
_CACHE_MAX = 200

# S195: alias-cache TTL — separate from result cache. Aliases change slowly
# (org rebrands, new tournaments) so a 30-min refresh is plenty and saves
# one DB round-trip per scan cycle.
_ALIAS_TTL = 1800.0

# S195: rapidfuzz threshold for the fuzzy fallback. token_set_ratio handles
# word-order differences and partial overlaps. 80 is conservative — empirically
# above this score is "same team in different wording", below is noise.
_FUZZY_THRESHOLD = 80.0

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz  # type: ignore
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:  # pragma: no cover — rapidfuzz is in requirements
    _rapidfuzz_fuzz = None
    _RAPIDFUZZ_AVAILABLE = False

# Game-specific keywords for market matching
_GAME_KEYWORDS: Dict[str, List[str]] = {
    "lol": ["league of legends", "lol", "lck", "lec", "lpl", "lcs", "worlds", "msi", "rift"],
    "cs2": ["counter-strike", "cs2", "csgo", "cs:go", "blast", "esl ", "pgl ", "iem ", "faceit"],
    "dota2": ["dota", "dota 2", "the international", "ti", "dpc"],
    "valorant": ["valorant", "vct", "champions tour", "vcl", "vrl"],
    "cod": ["call of duty", "cod ", "call of duty league", "cdl"],
    "r6": ["rainbow six", "r6 ", "six invitational", "r6 siege"],
    "sc2": ["starcraft", "sc2", "brood war", "gsl", "asl"],
    "rl": ["rocket league", "rlcs"],
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

    def __init__(self, db=None, polymarket_client=None, market_service=None) -> None:
        self._db = db
        self._poly = polymarket_client
        self._market_service = market_service  # EsportsMarketService (Commit 4)
        # S195: alias cache — { game: ({canon_lc: [alias_lc, ...]}, expires_at) }
        # Keyed by game so a None/all-game refresh is a separate slot.
        self._alias_cache: Dict[Optional[str], Tuple[Dict[str, List[str]], float]] = {}

    async def _get_alias_map(self, game: Optional[str]) -> Dict[str, List[str]]:
        """S195: load + cache the alias map for this game.

        Returns {canonical_name_lc: [alias_lc, alias_lc, ...]} with the
        canonical name itself included in each list. Empty dict if DB
        unavailable or table empty — callers must tolerate that and fall
        through to fuzzy / lowercase substring matching.
        """
        now = time.monotonic()
        cached = self._alias_cache.get(game)
        if cached is not None:
            amap, expires_at = cached
            if now < expires_at:
                return amap
        amap: Dict[str, List[str]] = {}
        if self._db is not None and hasattr(self._db, "load_esports_team_aliases"):
            try:
                amap = await self._db.load_esports_team_aliases(game=game)
            except Exception as e:
                logger.debug("alias_map_load_failed", game=game, error=str(e))
                amap = {}
        self._alias_cache[game] = (amap, now + _ALIAS_TTL)
        return amap

    @staticmethod
    def _expand_team_aliases(
        team_names: List[str], alias_map: Dict[str, List[str]],
    ) -> List[str]:
        """S195: expand a list of PandaScore team names into the full set
        of (lowercased) variants to match against market questions.

        Always includes the original names. If a name has alias entries,
        all variants are added. Result deduped, preserving order.
        """
        seen = set()
        expanded: List[str] = []
        for t in team_names:
            t_lc = (t or "").strip().lower()
            if not t_lc:
                continue
            if t_lc not in seen:
                seen.add(t_lc)
                expanded.append(t_lc)
            for variant in alias_map.get(t_lc, []):
                v_lc = (variant or "").strip().lower()
                if v_lc and v_lc not in seen:
                    seen.add(v_lc)
                    expanded.append(v_lc)
        return expanded

    @staticmethod
    def _team_match_score(
        team_names: List[str], question_lc: str, expanded: List[str],
    ) -> Tuple[bool, float]:
        """S195: decide whether this market's question mentions either team.

        Two-stage:
          1. Substring check across all expanded aliases (fast). If any alias
             appears in the question, return (True, 100.0).
          2. rapidfuzz fallback over the original (non-expanded) team names.
             Catches typos and word-order variations the alias table missed.
             Threshold _FUZZY_THRESHOLD; below that we treat as no match.

        Returns (matched, score) where score is the best fuzzy ratio seen
        (used by the unmatched-prediction tracker for triage).
        """
        # Stage 1: alias substring (cheap, exact)
        for a in expanded:
            if a and a in question_lc:
                return True, 100.0

        # Stage 2: fuzzy fallback over canonical names only. Don't fuzz
        # every alias — alias variants are designed for exact substring,
        # fuzzing them would just push the threshold work into rapidfuzz.
        if _RAPIDFUZZ_AVAILABLE and team_names:
            best = 0.0
            for t in team_names:
                t_lc = (t or "").strip().lower()
                if not t_lc:
                    continue
                # token_set_ratio handles word-order + partial overlap, e.g.
                # "AaB Esport" vs "Aalborg Esport AaB" scores ~85+.
                score = float(_rapidfuzz_fuzz.token_set_ratio(t_lc, question_lc))
                if score > best:
                    best = score
            return (best >= _FUZZY_THRESHOLD), best

        return False, 0.0

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

        # Strategy 1: EsportsMarketService (DB-backed, bypasses broken Gamma API)
        all_markets = []
        if self._market_service:
            try:
                all_markets = await asyncio.wait_for(
                    self._market_service.get_tradeable_esports_markets(game=game),
                    timeout=10.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsMarketScanner: market service failed", error=str(exc))

        # Strategy 2 (fallback): Polymarket API — kept for backward compatibility
        if not all_markets and self._poly:
            try:
                all_markets = await asyncio.wait_for(
                    self._poly.get_markets(active=True, limit=500),
                    timeout=10.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsMarketScanner: Polymarket scan failed", error=str(exc))

        # Strategy 1: keyword search across active esports markets
        keywords = list(_GAME_KEYWORDS.get(game, []))
        if team_names:
            keywords.extend(team_names)

        # S195: pre-load alias expansion for both teams so the per-market
        # loop below doesn't pay a DB round-trip per market.
        expanded_aliases: List[str] = []
        if team_names:
            alias_map = await self._get_alias_map(game)
            expanded_aliases = self._expand_team_aliases(team_names, alias_map)

        # S195: track the closest near-miss for the unmatched-tracker.
        # Recorded only if zero markets matched — gives a human reviewing
        # the table fast triage data ("AaB Esport" predicted, closest
        # question said "Aalborg Esports", score 78 → add an alias).
        _best_near_miss_score = 0.0
        _best_near_miss_question: Optional[str] = None
        _candidate_count = 0

        for market in (all_markets or []):
            question = str(market.get("question", "")).lower()
            category = str(market.get("category", "")).lower()

            # Must be esports-related
            if category != "esports" and not any(kw in question for kw in keywords):
                continue

            _candidate_count += 1

            # S195: alias-aware team name matching with fuzzy fallback.
            if team_names:
                matched, score = self._team_match_score(
                    team_names, question, expanded_aliases,
                )
                if not matched:
                    if score > _best_near_miss_score:
                        _best_near_miss_score = score
                        _best_near_miss_question = market.get("question")
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
                # Passthrough paired-token keys from upstream market_service. The
                # Polymarket API fallback path lacks these; passthrough emits None
                # and preserves that path's pre-existing behavior.
                "yes_token_id": market.get("yes_token_id"),
                "no_token_id": market.get("no_token_id"),
                "yes_price": market.get("yes_price"),
                "no_price": market.get("no_price"),
                "id": market.get("id"),
                "condition_id": market.get("condition_id"),
            })

        # S195: log any shadow prediction whose match scored zero markets.
        # Idempotent — DB does ON CONFLICT DO NOTHING on (match_id, team_a, team_b).
        if not results and team_names and len(team_names) >= 2 and self._db is not None:
            if hasattr(self._db, "log_unmatched_prediction"):
                try:
                    await self._db.log_unmatched_prediction(
                        match_id=match_id,
                        team_a=team_names[0],
                        team_b=team_names[1],
                        game=game,
                        candidate_markets_count=_candidate_count,
                        closest_question=_best_near_miss_question,
                        closest_score=(_best_near_miss_score
                                       if _best_near_miss_score > 0.0 else None),
                    )
                except Exception as e:
                    logger.debug("log_unmatched_prediction call failed: %s", e)

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

        # Primary: EsportsMarketService (DB-backed, bypasses broken Gamma API)
        all_markets = []
        if self._market_service:
            try:
                all_markets = await asyncio.wait_for(
                    self._market_service.get_tradeable_esports_markets(game=game),
                    timeout=15.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsMarketScanner: market service failed", error=str(exc))

        # Fallback: Polymarket API (kept for backward compatibility)
        if not all_markets and self._poly:
            try:
                all_markets = await asyncio.wait_for(
                    self._poly.get_markets(active=True, limit=500),
                    timeout=15.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsMarketScanner: full scan failed", error=str(exc))

        keywords = []
        if game and game in _GAME_KEYWORDS:
            keywords = _GAME_KEYWORDS[game]
        else:
            for kws in _GAME_KEYWORDS.values():
                keywords.extend(kws)
            keywords.append("esports")

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
                    # Passthrough paired-token keys; see find_markets_for_match.
                    "yes_token_id": market.get("yes_token_id"),
                    "no_token_id": market.get("no_token_id"),
                    "yes_price": market.get("yes_price"),
                    "no_price": market.get("no_price"),
                    "id": market.get("id"),
                    "condition_id": market.get("condition_id"),
                })

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
