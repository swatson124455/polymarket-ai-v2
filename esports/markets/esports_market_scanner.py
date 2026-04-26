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
    def _team_present(
        team_name: str, question_lc: str, alias_map: Dict[str, List[str]],
    ) -> Tuple[bool, float]:
        """S195: is one specific team mentioned in the question?

        Three-stage check for ONE team:
          1. Direct substring on the lowercased team name.
          2. Substring on any of the team's known aliases.
          3. rapidfuzz token_set_ratio fallback over the canonical name only.

        Returns (matched, score) where score reflects match quality (100 for
        exact substring, the rapidfuzz value for fuzzy hits, 0 otherwise).
        """
        team_lc = (team_name or "").strip().lower()
        if not team_lc:
            return False, 0.0
        # Stage 1: direct substring
        if team_lc in question_lc:
            return True, 100.0
        # Stage 2: alias substring
        for variant in alias_map.get(team_lc, []):
            v = (variant or "").strip().lower()
            if v and v != team_lc and v in question_lc:
                return True, 100.0
        # Stage 3: fuzzy fallback (canonical name only — alias variants
        # are designed for exact substring; fuzzing them is double work)
        if _RAPIDFUZZ_AVAILABLE:
            score = float(_rapidfuzz_fuzz.token_set_ratio(team_lc, question_lc))
            if score >= _FUZZY_THRESHOLD:
                return True, score
            return False, score
        return False, 0.0

    @classmethod
    def _both_teams_present(
        cls,
        team_names: List[str],
        question_lc: str,
        alias_map: Dict[str, List[str]],
    ) -> Tuple[bool, float, float]:
        """S195 deeper fix: require BOTH teams in the question, not either.

        Pre-fix the matcher returned markets where a single team appeared,
        so famous teams like T1 picked up season-long playoff markets and
        unrelated other-opponent matches. The actual `T1 vs BNK FEARX`
        market existed but was buried in the result set — and the caller
        picks the first result, so the wrong market won.

        Returns (both_present, score_a, score_b). Score = max of the two
        teams' individual scores when both pass; otherwise = the better
        single-team score for unmatched-tracker triage.
        """
        if not team_names or len(team_names) < 2:
            return False, 0.0, 0.0
        a_ok, a_score = cls._team_present(team_names[0], question_lc, alias_map)
        b_ok, b_score = cls._team_present(team_names[1], question_lc, alias_map)
        return (a_ok and b_ok), a_score, b_score

    @staticmethod
    def _specificity_score(question_lc: str) -> float:
        """S195 deeper fix: rank match-specific questions above season /
        handicap / playoff markets so the caller's first-result pick is
        the right one.

        Heuristic — higher score = more likely the question is about ONE
        specific match, not a season aggregate or sub-market.

        Base 100. Bonuses/penalties:
          +30  question contains 'vs' (typical match-question shape)
          -50  contains season/playoff/championship/tournament/split keyword
          -20  contains map/game/handicap/total-maps sub-market keyword
                  (still valid but more granular than full-match)
        """
        score = 100.0
        if " vs " in question_lc or " vs. " in question_lc:
            score += 30.0
        season_terms = ("season", "playoff", "championship", "tournament",
                        "regular season", "split")
        for term in season_terms:
            if term in question_lc:
                score -= 50.0
                break
        sub_terms = ("- map ", "- game ", "map winner", "game winner",
                     "handicap", "total maps", " over ", " under ",
                     "first blood", "first kill")
        for term in sub_terms:
            if term in question_lc:
                score -= 20.0
                break
        return score

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

        # S195: pre-load alias map for both teams so the per-market loop
        # below doesn't pay a DB round-trip per market.
        alias_map: Dict[str, List[str]] = {}
        if team_names:
            alias_map = await self._get_alias_map(game)

        # S195: track the closest near-miss for the unmatched-tracker.
        # Recorded only if zero markets matched — gives a human reviewing
        # the table fast triage data ("AaB Esport" predicted, closest
        # question said "Aalborg Esports", score 78 → add an alias).
        _best_near_miss_score = 0.0
        _best_near_miss_question: Optional[str] = None
        _candidate_count = 0

        # S195 deeper fix: collect (specificity_score, market_dict) pairs
        # then sort by specificity descending before returning. The first
        # result wins downstream in _find_polymarket_for_match — we want
        # match-specific questions ranked above season/handicap variants.
        scored_results: List[Tuple[float, Dict[str, Any]]] = []

        for market in (all_markets or []):
            question = str(market.get("question", "")).lower()
            category = str(market.get("category", "")).lower()

            # Must be esports-related
            if category != "esports" and not any(kw in question for kw in keywords):
                continue

            _candidate_count += 1

            # S195 deeper fix: require BOTH teams in the question, not either.
            # Was `any(team in question)` — accepted markets mentioning only
            # one team (typically the famous one), which surfaced season-long
            # markets and games against unrelated opponents.
            if team_names and len(team_names) >= 2:
                both_ok, score_a, score_b = self._both_teams_present(
                    team_names, question, alias_map,
                )
                if not both_ok:
                    near = max(score_a, score_b)
                    if near > _best_near_miss_score:
                        _best_near_miss_score = near
                        _best_near_miss_question = market.get("question")
                    continue
            elif team_names:
                # Only 1 team given — fall back to single-team gate
                # (preserves callers that only pass one name).
                ok, sc = self._team_present(team_names[0], question, alias_map)
                if not ok:
                    if sc > _best_near_miss_score:
                        _best_near_miss_score = sc
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

            market_dict = {
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
            }
            spec = self._specificity_score(question)
            scored_results.append((spec, market_dict))

        # Sort by specificity descending — match-specific questions win.
        scored_results.sort(key=lambda x: -x[0])
        results = [m for (_, m) in scored_results]

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
