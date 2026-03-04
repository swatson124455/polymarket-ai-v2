"""
Player Registry — resolve raw player name strings to database player IDs.

Phase 2: Full fuzzy-match implementation using difflib.SequenceMatcher.
  - Loads all players for the given sport from sports_players table.
  - Fuzzy-matches raw name against name + name_variants (ratio > 0.85).
  - Caches per-sport player list for 60 seconds in memory.
  - Handles NFL aliases: "Pat Mahomes" / "Patrick Mahomes" / "PM15".

Usage::
    player_id = await resolve_player("LeBron James", "nba", db=db)
    # Returns int player_id or None if not found
"""
from __future__ import annotations

import asyncio
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
from structlog import get_logger

logger = get_logger()

# In-memory cache: sport → (timestamp, list_of_player_dicts)
_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_CACHE_TTL = 60.0  # seconds
_CACHE_LOCK = asyncio.Lock()

_FUZZY_THRESHOLD = 0.75  # I37: lowered from 0.85 — "LBJ" / "King James" were missing

# I37: Top-50 star nickname dict → canonical name for pre-lookup before fuzzy match
# Keys: common aliases / abbreviations. Values: canonical full name.
_STAR_NICKNAMES: Dict[str, str] = {
    # NBA
    "lbj": "LeBron James", "king james": "LeBron James", "lebron": "LeBron James",
    "ad": "Anthony Davis", "the brow": "Anthony Davis",
    "kd": "Kevin Durant", "slim reaper": "Kevin Durant",
    "sc30": "Stephen Curry", "chef curry": "Stephen Curry", "steph": "Stephen Curry",
    "pg13": "Paul George",
    "kawhi": "Kawhi Leonard", "the claw": "Kawhi Leonard",
    "ja": "Ja Morant",
    "giannis": "Giannis Antetokounmpo", "greek freak": "Giannis Antetokounmpo",
    "jokic": "Nikola Jokic", "the joker": "Nikola Jokic",
    "embiid": "Joel Embiid", "the process": "Joel Embiid",
    "tatum": "Jayson Tatum",
    "dame": "Damian Lillard", "dame dolla": "Damian Lillard",
    "shai": "Shai Gilgeous-Alexander", "sga": "Shai Gilgeous-Alexander",
    # NFL
    "pm15": "Patrick Mahomes", "mahomes": "Patrick Mahomes",
    "pat mahomes": "Patrick Mahomes",
    "jalen hurts": "Jalen Hurts",
    "josh allen": "Josh Allen",
    "tyreek": "Tyreek Hill", "cheetah": "Tyreek Hill",
    "davante": "Davante Adams",
    "cmc": "Christian McCaffrey",
    "lamar": "Lamar Jackson",
    "tua": "Tua Tagovailoa",
    "jj": "Justin Jefferson",
    # MLB
    "ohtani": "Shohei Ohtani", "sho-time": "Shohei Ohtani",
    "trout": "Mike Trout",
    "judge": "Aaron Judge",
    "mookie": "Mookie Betts",
    # NHL
    "mcdavid": "Connor McDavid",
    "ovechkin": "Alex Ovechkin", "ovi": "Alex Ovechkin",
    "draisaitl": "Leon Draisaitl",
    # Tennis
    "djokovic": "Novak Djokovic", "nole": "Novak Djokovic",
    "alcaraz": "Carlos Alcaraz",
    "sinner": "Jannik Sinner",
    "swiatek": "Iga Swiatek",
}


async def resolve_player(
    raw_name: str,
    sport: str,
    db=None,
) -> Optional[int]:
    """
    Resolve a raw player name string to a sports_players.id.

    Args:
        raw_name: Raw name from tweet/RSS (e.g. "LeBron", "Pat Mahomes").
        sport:    Sport code: nba / nfl / mlb / nhl / soccer / tennis.
        db:       Database instance (injected by caller). If None, returns None.

    Returns:
        Player ID (int) or None if not found.
    """
    if not raw_name or not sport:
        return None
    if db is None:
        return None

    # I37: Resolve nickname to canonical name first (e.g. "LBJ" → "LeBron James")
    _canon = _STAR_NICKNAMES.get(raw_name.strip().lower())
    effective_name = _canon if _canon else raw_name

    players = await _get_players_for_sport(sport, db)
    return _fuzzy_match(effective_name, players)


async def invalidate_cache(sport: Optional[str] = None) -> None:
    """
    Invalidate the in-memory player cache.

    Args:
        sport: If given, invalidate only that sport. If None, clear all.
    """
    async with _CACHE_LOCK:
        if sport:
            _CACHE.pop(sport, None)
        else:
            _CACHE.clear()


# ─── Internals ────────────────────────────────────────────────────────────────

async def _get_players_for_sport(sport: str, db) -> List[Dict]:
    """
    Load all players for a sport from DB, with 60s in-memory cache.

    Returns list of dicts: {id, name, name_variants (list of str)}.
    """
    async with _CACHE_LOCK:
        cached = _CACHE.get(sport)
        if cached:
            ts, players = cached
            if time.monotonic() - ts < _CACHE_TTL:
                return players

    # I57: Fetch from DB outside the lock to avoid blocking other sports.
    # Double-check on re-acquire: a concurrent coroutine may have populated cache already.
    try:
        players = await _fetch_players_from_db(sport, db)
    except Exception as exc:
        logger.warning(
            "PlayerRegistry: DB fetch failed", sport=sport, error=str(exc)
        )
        # Return stale cache if available
        stale = _CACHE.get(sport)
        return stale[1] if stale else []

    async with _CACHE_LOCK:
        # Double-check: another coroutine may have populated while we fetched
        cached = _CACHE.get(sport)
        if cached:
            ts, existing = cached
            if time.monotonic() - ts < _CACHE_TTL:
                return existing  # Use the fresher entry from the concurrent coroutine
        _CACHE[sport] = (time.monotonic(), players)

    logger.debug("PlayerRegistry: loaded players", sport=sport, count=len(players))
    return players


async def _fetch_players_from_db(sport: str, db) -> List[Dict]:
    """Load sports_players rows for a given sport from the database."""
    from sqlalchemy import text

    players: List[Dict] = []
    async with db.get_session() as session:
        result = await session.execute(
            text(
                "SELECT id, name, name_variants "
                "FROM sports_players "
                "WHERE sport = :sport AND status = 'active' "
                "ORDER BY id"
            ),
            {"sport": sport},
        )
        rows = result.fetchall()
        for row in rows:
            variants = row[2] or []
            if isinstance(variants, str):
                import json
                try:
                    variants = json.loads(variants)
                except Exception:
                    variants = []
            players.append({
                "id": int(row[0]),
                "name": str(row[1]),
                "variants": [str(v) for v in variants if v],
            })
    return players


def _fuzzy_match(raw_name: str, players: List[Dict]) -> Optional[int]:
    """
    Fuzzy-match raw_name against player name + all name_variants.

    Uses SequenceMatcher ratio > _FUZZY_THRESHOLD (0.85).
    Returns player ID of best match or None.
    """
    raw_lower = raw_name.strip().lower()
    if not raw_lower:
        return None

    best_ratio = 0.0
    best_id: Optional[int] = None

    for player in players:
        # Build full list of names to try
        names_to_try = [player["name"]] + player.get("variants", [])

        for name in names_to_try:
            name_lower = name.strip().lower()
            if not name_lower:
                continue

            # Exact match shortcut
            if raw_lower == name_lower:
                return player["id"]

            # Check if raw_name is a subset (e.g. "LeBron" → "LeBron James")
            if raw_lower in name_lower or name_lower in raw_lower:
                ratio = 0.90  # partial match — treat as high confidence
            else:
                ratio = SequenceMatcher(None, raw_lower, name_lower).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_id = player["id"]

    if best_ratio >= _FUZZY_THRESHOLD:
        logger.debug(
            "PlayerRegistry: matched player",
            raw_name=raw_name,
            player_id=best_id,
            ratio=round(best_ratio, 3),
        )
        return best_id

    logger.debug(
        "PlayerRegistry: no match",
        raw_name=raw_name,
        best_ratio=round(best_ratio, 3),
        threshold=_FUZZY_THRESHOLD,
    )
    return None
