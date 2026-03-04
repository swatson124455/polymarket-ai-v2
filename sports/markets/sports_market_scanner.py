"""
Sports Market Scanner — find Polymarket + Kalshi markets for a given game/event.

Phase 2: Full implementation scanning both platforms.

Polymarket:
  - Filters existing markets by category="sports" + keyword match.
  - Keywords: team names, player names extracted from game metadata.
  - NFL: also scans for "free agency", "draft", "combine" keywords.

Kalshi:
  - Uses KalshiSportsClient.get_sports_markets() filtered to sport.

Results are cached with a 120s TTL to avoid hammering APIs.
Writes discovered markets to sports_market_map table.

Usage::
    scanner = SportsMarketScanner(db=db, kalshi_client=kalshi_client)
    markets = await scanner.find_markets_for_game(game_id="123", sport="nba")
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional
from structlog import get_logger

from sports.markets.kalshi_client import SportsMarketCandidate

logger = get_logger()

_CACHE_TTL = 120.0   # seconds
_CACHE: Dict[str, tuple] = {}   # key → (timestamp, List[SportsMarketCandidate])
_CACHE_LOCK = asyncio.Lock()

# NFL offseason search terms
_NFL_OFFSEASON_TERMS = [
    "free agency", "nfl draft", "combine", "free agent",
    "signs with", "agrees to terms", "traded to", "nfl trade",
]

# Sport → typical market keywords
_SPORT_KEYWORDS: Dict[str, List[str]] = {
    "nba": ["nba", "basketball", "championship", "finals", "playoffs"],
    "nfl": ["nfl", "football", "super bowl", "championship", "playoffs"],
    "mlb": ["mlb", "baseball", "world series", "playoffs"],
    "nhl": ["nhl", "hockey", "stanley cup", "playoffs"],
    "soccer": ["soccer", "football", "premier league", "champions league", "epl", "la liga"],
    "tennis": ["tennis", "wimbledon", "us open", "french open", "australian open", "atp", "wta"],
}


class SportsMarketScanner:
    """
    Scans Polymarket + Kalshi for markets related to a game or sport event.

    Usage::
        scanner = SportsMarketScanner(db=db)
        markets = await scanner.find_markets_for_game("game_id", "nba")
    """

    def __init__(
        self,
        db=None,
        kalshi_client=None,
        polymarket_client=None,
    ) -> None:
        self._db = db
        self._kalshi = kalshi_client
        self._poly = polymarket_client

    async def find_markets_for_game(
        self,
        game_id: str,
        sport: str,
        player_name: Optional[str] = None,
        team_names: Optional[List[str]] = None,
        db=None,
    ) -> List[SportsMarketCandidate]:
        """
        Find Polymarket + Kalshi markets for a game/event.

        Args:
            game_id:     Game ID from sports_games table (or "free_agent_move" for offseason).
            sport:       Sport code: nba / nfl / mlb / nhl / soccer / tennis.
            player_name: Player name to include in keyword search (optional).
            team_names:  Team names to include in keyword search (optional).
            db:          Override DB instance (optional).

        Returns:
            List of SportsMarketCandidate objects, deduplicated by market_id.
        """
        db = db or self._db
        # I38: Normalize player_name — "LeBron" vs "lebron" must hit same cache entry
        _pname_norm = (player_name or "").lower().strip()
        cache_key = f"{sport}_{game_id}_{_pname_norm}_{','.join(team_names or [])}"

        # Check cache
        async with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached:
                ts, markets = cached
                if time.monotonic() - ts < _CACHE_TTL:
                    return markets

        # Gather candidates from both platforms concurrently
        poly_task = asyncio.create_task(
            self._scan_polymarket(sport, game_id, player_name, team_names, db)
        )
        kalshi_task = asyncio.create_task(
            self._scan_kalshi(sport, game_id, player_name, team_names)
        )

        results = await asyncio.gather(poly_task, kalshi_task, return_exceptions=True)

        candidates: List[SportsMarketCandidate] = []
        seen_ids = set()

        for result in results:
            if isinstance(result, Exception):
                logger.debug("SportsMarketScanner: scan error", error=str(result))
                continue
            for c in result:
                if c.market_id not in seen_ids:
                    candidates.append(c)
                    seen_ids.add(c.market_id)

        # Persist to sports_market_map
        if db and candidates:
            await self._save_to_db(candidates, game_id, sport, db)

        # Cache result
        async with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), candidates)
            # Bound cache size — evict oldest entries when > 200 keys
            if len(_CACHE) > 200:
                # Find and remove oldest entries
                sorted_keys = sorted(_CACHE.keys(), key=lambda k: _CACHE[k][0])
                for k in sorted_keys[:len(_CACHE) - 200]:
                    del _CACHE[k]

        logger.info(
            "SportsMarketScanner: found markets",
            game_id=game_id,
            sport=sport,
            count=len(candidates),
        )
        return candidates

    async def invalidate_cache(self, sport: Optional[str] = None) -> None:
        """Clear the market scanner cache for a sport or entirely."""
        async with _CACHE_LOCK:
            if sport:
                keys_to_remove = [k for k in _CACHE if k.startswith(sport)]
                for k in keys_to_remove:
                    del _CACHE[k]
            else:
                _CACHE.clear()

    # ─── Polymarket scan ──────────────────────────────────────────────────────

    async def _scan_polymarket(
        self,
        sport: str,
        game_id: str,
        player_name: Optional[str],
        team_names: Optional[List[str]],
        db,
    ) -> List[SportsMarketCandidate]:
        """Scan Polymarket for sports markets matching the given keywords."""
        try:
            # Build keyword list
            keywords = list(_SPORT_KEYWORDS.get(sport, []))
            if player_name:
                keywords.append(player_name.lower())
                # Also add last name only
                parts = player_name.split()
                if len(parts) > 1:
                    keywords.append(parts[-1].lower())
            if team_names:
                keywords.extend(t.lower() for t in team_names)
            if sport == "nfl":
                keywords.extend(_NFL_OFFSEASON_TERMS)

            # Get tradeable markets from DB (already cached by base_engine)
            if db is None:
                return []

            from sqlalchemy import text
            candidates = []
            async with db.get_session() as session:
                # Search markets by category + question text keyword match
                for keyword in keywords[:10]:  # limit DB queries
                    result = await session.execute(
                        text(
                            "SELECT m.id, m.condition_id, m.question, "
                            "  m.yes_price, m.no_price, m.category "
                            "FROM markets m "
                            "WHERE (LOWER(m.category) LIKE '%sport%' "
                            "   OR LOWER(m.question) LIKE :kw) "
                            "  AND COALESCE(m.yes_price, 0.5) BETWEEN 0.05 AND 0.95 "
                            "  AND m.is_resolved = false "
                            "LIMIT 20"
                        ),
                        {"kw": f"%{keyword}%"},
                    )
                    rows = result.fetchall()
                    seen = {c.market_id for c in candidates}
                    for row in rows:
                        market_id = str(row[0])
                        if market_id in seen:
                            continue
                        yes_price = float(row[3]) if row[3] is not None else 0.50
                        candidates.append(SportsMarketCandidate(
                            platform="polymarket",
                            market_id=market_id,
                            market_type="moneyline",
                            sport=sport,
                            yes_token_id=str(row[1]) if row[1] else None,
                            no_token_id=None,
                            current_price=yes_price,
                            title=str(row[2] or ""),
                            price_fetched_at=time.monotonic(),  # I39: track when price was fetched
                        ))
                        seen.add(market_id)

            return candidates
        except Exception as exc:
            logger.debug("SportsMarketScanner: Polymarket scan error", error=str(exc))
            return []

    # ─── Kalshi scan ─────────────────────────────────────────────────────────

    async def _scan_kalshi(
        self,
        sport: str,
        game_id: str,
        player_name: Optional[str],
        team_names: Optional[List[str]],
    ) -> List[SportsMarketCandidate]:
        """Scan Kalshi sports markets."""
        if self._kalshi is None:
            return []
        try:
            markets = await asyncio.wait_for(
                self._kalshi.get_sports_markets(sport=sport),
                timeout=10.0,
            )
            # Apply additional keyword filtering if player/team names provided
            if player_name or team_names:
                keywords = []
                if player_name:
                    keywords.append(player_name.lower())
                if team_names:
                    keywords.extend(t.lower() for t in team_names)
                markets = [
                    m for m in markets
                    if any(kw in m.title.lower() for kw in keywords)
                ]
            return markets
        except asyncio.TimeoutError:
            logger.warning("SportsMarketScanner: Kalshi scan timed out")
            return []
        except Exception as exc:
            logger.debug("SportsMarketScanner: Kalshi scan error", error=str(exc))
            return []

    # ─── DB persistence ───────────────────────────────────────────────────────

    async def _save_to_db(
        self,
        candidates: List[SportsMarketCandidate],
        game_id: str,
        sport: str,
        db,
    ) -> None:
        """Upsert market candidates into sports_market_map."""
        try:
            from sqlalchemy import text
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            async with db.get_session() as session:
                for c in candidates:
                    await session.execute(
                        text(
                            "INSERT INTO sports_market_map "
                            "  (game_id, platform, market_id, market_type, sport, mapped_at) "
                            "VALUES (:game_id, :platform, :market_id, :market_type, :sport, :mapped_at) "
                            "ON CONFLICT (platform, market_id) DO NOTHING"
                        ),
                        {
                            "game_id": int(game_id) if str(game_id).isdigit() else None,
                            "platform": c.platform,
                            "market_id": c.market_id,
                            "market_type": c.market_type or "moneyline",
                            "sport": sport,
                            "mapped_at": now,
                        },
                    )
                await session.commit()
        except Exception as exc:
            logger.debug("SportsMarketScanner: DB save error", error=str(exc))


# ─── Module-level convenience function ────────────────────────────────────────

_default_scanner: Optional[SportsMarketScanner] = None


async def find_markets_for_game(
    game_id: str,
    sport: str,
    db=None,
    kalshi_client=None,
    polymarket_client=None,
    player_name: Optional[str] = None,
    team_names: Optional[List[str]] = None,
) -> List[SportsMarketCandidate]:
    """
    Module-level convenience wrapper.
    Creates or reuses a SportsMarketScanner singleton.
    """
    global _default_scanner
    if _default_scanner is None:
        _default_scanner = SportsMarketScanner(
            db=db, kalshi_client=kalshi_client, polymarket_client=polymarket_client
        )
    elif db and _default_scanner._db is None:
        _default_scanner._db = db

    return await _default_scanner.find_markets_for_game(
        game_id=game_id,
        sport=sport,
        player_name=player_name,
        team_names=team_names,
        db=db,
    )
