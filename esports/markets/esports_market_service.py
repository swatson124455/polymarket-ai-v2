"""
Esports Market Service — dedicated market discovery + CLOB price refresh.

WHY THIS EXISTS:
The Polymarket Gamma API returns ZERO esports markets in standard pagination.
The base engine's `get_markets(limit=200)` fetches 200 random markets from 26,888
active markets — none of which are esports. All 1,593 esports markets entered the
DB via CLOB API resolution backfill with hardcoded `liquidity=0, volume=0`, so
liquidity-based filters permanently hide them.

This service:
  1. Queries DB directly for `category='esports'` — bypasses broken Gamma API
  2. Has NO liquidity filter (CLOB markets have no AMM liquidity by design)
  3. Uses volume as a proxy for tradability
  4. Double-gates soccer/football that Polymarket miscategorizes as "esports"
  5. Runs background CLOB price refresh every 5 min to keep prices current
  6. Returns market dicts compatible with analyze_opportunity()

Usage::
    service = EsportsMarketService(db=db)
    await service.start_background_refresh()
    markets = await service.get_tradeable_esports_markets(game="cs2")
    await service.close()
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

# Default minimum volume for tradability ($100 USD)
_DEFAULT_MIN_VOLUME = float(os.environ.get("ESPORTS_MIN_VOLUME_USD", "100"))

# Cache TTL for tradeable markets query (seconds)
_CACHE_TTL = 120.0

# Background refresh interval (seconds) — 5 minutes
_REFRESH_INTERVAL = float(os.environ.get("ESPORTS_PRICE_REFRESH_INTERVAL", "300"))

# S182 #2: ESPORTS_MARKETS_REFRESH_V2_ENABLED gates the fixes landed this session:
# (a) ORDER BY updated_at ASC NULLS FIRST on the refresh query for deterministic rotation
# (b) logger.warning(exc_info=True) on the refresh-loop exception handler so silent
#     crashes become visible (pre-S182 was logger.debug — invisible for 18h+ before detection)
# (c) EsportsMarketService_cycle_complete heartbeat outside the `stats["total"] > 0`
#     guard so zero-row cycles emit a log too (pre-S182 only non-zero cycles logged)
# Default TRUE (opt-out). Rollback path: set ESPORTS_MARKETS_REFRESH_V2_ENABLED=false
# in VPS .env and restart esports service. No code revert needed.
_MARKETS_REFRESH_V2_ENABLED = os.environ.get("ESPORTS_MARKETS_REFRESH_V2_ENABLED", "true").lower() in ("true", "1", "yes")

# Game keywords for the soccer/football double-gate
_ESPORTS_GAME_KEYWORDS: Dict[str, List[str]] = {
    "lol": ["league of legends", "lol:", "lol ", " lol "],
    "cs2": ["counter-strike", "cs2", "csgo", "blast premier"],
    "dota2": ["dota 2", "dota2", "dota:"],
    "valorant": ["valorant", "vct ", "champions tour"],
    # S216 Item 5-v2: removed bare "cod " substring — matched "COD Meknès"
    # (Moroccan soccer club Wydad/FathUnionSport/RS Berkane opponents). The
    # \bcdl\b boundary regex in _BOUNDARY_KEYWORDS still catches CDL Major
    # markets, and "call of duty" substring still catches branded markets.
    # Direct-data audit 2026-05-14: 0 active markets matched cod-only path
    # (excluding "call of duty"/cdl), so recall cost = 0 at fix time.
    "cod": ["call of duty", "call of duty league"],
    "r6": ["rainbow six", "r6 ", "six invitational", "r6 siege"],
    "sc2": ["starcraft", "sc2", "brood war"],
    "rl": ["rocket league", "rlcs"],
}

# Short esports acronyms that need word-boundary matching to avoid
# false positives inside common words (e.g. "lec" in "election",
# "lcs" in "councils", "msi" in various strings).
# Checked via regex \b...\b in _kw_match().
import re as _re
_BOUNDARY_KEYWORDS: Dict[str, List[_re.Pattern]] = {
    "lol": [_re.compile(r"\blck\b"), _re.compile(r"\blec\b"), _re.compile(r"\blpl\b"),
            _re.compile(r"\blcs\b"), _re.compile(r"\bmsi\b")],
    "cs2": [_re.compile(r"\besl\b"), _re.compile(r"\bpgl\b"), _re.compile(r"\biem\b")],
    "dota2": [_re.compile(r"\bdpc\b"), _re.compile(r"\bthe international\s+\d"), _re.compile(r"\bti\b")],
    "cod": [_re.compile(r"\bcdl\b")],
    "sc2": [_re.compile(r"\bgsl\b"), _re.compile(r"\basl\b")],
}


def _game_matches(q: str, game: str) -> bool:
    """Check if lowercased question *q* matches a specific esports game."""
    if any(kw in q for kw in _ESPORTS_GAME_KEYWORDS.get(game, [])):
        return True
    for pat in _BOUNDARY_KEYWORDS.get(game, []):
        if pat.search(q):
            return True
    return False


def _is_real_esports(question: str) -> bool:
    """Double-gate: reject soccer/football that Polymarket miscategorizes as esports.

    Returns True only if the question matches a known esports game's keywords.
    This catches Bundesliga, La Liga, Premier League etc. that slip through
    the Polymarket category tagging.
    """
    q = question.lower()
    # Explicit "esports" keyword is sufficient
    if "esports" in q:
        return True
    return any(_game_matches(q, g) for g in _ESPORTS_GAME_KEYWORDS)


def _detect_game(question: str) -> str:
    """Detect which esports game a market is for from question text."""
    q = question.lower()
    for game in _ESPORTS_GAME_KEYWORDS:
        if _game_matches(q, game):
            return game
    return "unknown"


class EsportsMarketService:
    """Dedicated esports market discovery + CLOB price refresh.

    Replaces the broken path through base_engine.get_markets() + Gamma API.
    """

    def __init__(self, db=None, polymarket_client=None) -> None:
        self._db = db
        self._poly = polymarket_client
        self._httpx_client = None  # Lazy init for CLOB API calls
        self._refresh_task: Optional[asyncio.Task] = None

        # Cache for get_tradeable_esports_markets()
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_ts: float = 0.0

        # Stats
        self._last_refresh_stats: Dict[str, int] = {}

    async def get_tradeable_esports_markets(
        self,
        game: Optional[str] = None,
        min_volume: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Get active, priced esports markets from the DB.

        Args:
            game: Optional game filter ("lol", "cs2", "dota2", "valorant").
            min_volume: Minimum volume in USD. Defaults to ESPORTS_MIN_VOLUME_USD env.

        Returns:
            List of market dicts compatible with analyze_opportunity().
        """
        # Check cache
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < _CACHE_TTL:
            if game:
                return [m for m in self._cache if m.get("_game") == game]
            return list(self._cache)

        if not self._db or not getattr(self._db, "session_factory", None):
            return []

        vol_min = min_volume if min_volume is not None else _DEFAULT_MIN_VOLUME

        try:
            from sqlalchemy import text

            async with self._db.get_session() as session:
                # Query ALL active unresolved markets with prices — category
                # is unreliable (Polymarket miscategorizes esports as politics,
                # crypto, weather, etc). The _is_real_esports() keyword filter
                # below is the real gate. No volume filter — CLOB markets
                # start with volume=0.
                rows = await session.execute(text("""
                    SELECT id, condition_id, question, slug, category,
                           liquidity, volume, yes_token_id, no_token_id,
                           yes_price, no_price, resolution_source,
                           end_date_iso, active, resolved, resolution
                    FROM markets
                    WHERE active = true
                      AND (resolved = false OR resolved IS NULL)
                      -- S216 Thread D root fix: exclude markets whose scheduled
                      -- end is in the past. Polymarket's resolution backfill
                      -- runs on a delay, so a freshly-closed market can still
                      -- have active=true + resolved=false for minutes before
                      -- the backfill marks it resolved + backdates resolved_at
                      -- to the actual close time. Without this filter, the bot
                      -- predicts on closed markets and the prediction_time ends
                      -- up > the (later-written) resolved_at — the 385-row
                      -- temporal-ordering violation backlog cleanup script keeps
                      -- finding.
                      AND (end_date_iso IS NULL OR end_date_iso > NOW())
                      AND yes_price BETWEEN 0.03 AND 0.97
                      AND (
                        question ILIKE '%esports%'
                        OR question ILIKE '%league of legends%'
                        OR question ILIKE '%counter-strike%'
                        OR question ILIKE '%cs2%'
                        OR question ILIKE '%csgo%'
                        OR question ILIKE '%blast premier%'
                        OR question ILIKE '%dota%'
                        OR question ILIKE '%the international%'
                        OR question ILIKE '%valorant%'
                        OR question ILIKE '%champions tour%'
                        OR question ILIKE '%call of duty%'
                        OR question ILIKE '%rainbow six%'
                        OR question ILIKE '%six invitational%'
                        OR question ILIKE '%starcraft%'
                        OR question ILIKE '%sc2%'
                        OR question ILIKE '%brood war%'
                        OR question ILIKE '%rocket league%'
                        OR question ILIKE '%rlcs%'
                        OR question ~* '\\y(lol|lck|lec|lpl|lcs|msi|esl|pgl|iem|dpc|cdl|gsl|asl|vct|r6|ti)\\y'
                      )
                    ORDER BY volume DESC NULLS LAST
                    LIMIT 5000
                """))
                all_rows = rows.fetchall()

            markets = []
            for row in all_rows:
                question = str(row[2] or "")

                # Keyword gate: only pass markets matching esports game keywords
                if not _is_real_esports(question):
                    continue

                vol = float(row[6] or 0)

                yes_price = float(row[9]) if row[9] is not None else None
                no_price = float(row[10]) if row[10] is not None else None

                # Need at least YES price for edge computation
                if yes_price is None:
                    continue

                # Build market dict compatible with analyze_opportunity()
                tokens = []
                if row[7]:  # yes_token_id
                    tokens.append({
                        "tokenId": row[7],
                        "outcomePrice": yes_price,
                    })
                if row[8]:  # no_token_id
                    tokens.append({
                        "tokenId": row[8],
                        "outcomePrice": no_price if no_price is not None else (1.0 - yes_price),
                    })
                if not tokens:
                    continue

                markets.append({
                    "id": row[0],
                    "condition_id": row[1],
                    "question": question,
                    "slug": row[3],
                    "category": row[4],
                    "liquidity": float(row[5] or 0),
                    "volume": vol,
                    "yes_token_id": row[7],
                    "no_token_id": row[8],
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "resolution_source": row[11],
                    "end_date_iso": row[12].isoformat() if row[12] else None,
                    "active": bool(row[13]),
                    "resolved": bool(row[14]),
                    "resolution": row[15],
                    "tokens": tokens,
                    "_game": _detect_game(question),  # Internal: for game filtering
                })

            self._cache = markets
            self._cache_ts = now

            if game:
                return [m for m in markets if m.get("_game") == game]
            return list(markets)

        except Exception as exc:
            logger.warning("EsportsMarketService: DB query failed", error=str(exc))
            return []

    async def refresh_market_prices(
        self,
        market_ids: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Refresh prices for esports markets from CLOB API.

        For each market, calls CLOB API to get current token prices and
        closed/active status. Updates DB with fresh data.

        Args:
            market_ids: Optional list of specific market IDs. If None, refreshes
                        all active esports markets.

        Returns:
            Dict with: refreshed, closed, errors counts.
        """
        stats = {"refreshed": 0, "closed": 0, "errors": 0, "total": 0}

        if not self._db or not getattr(self._db, "session_factory", None):
            return stats

        # Get list of markets to refresh
        if market_ids is None:
            try:
                from sqlalchemy import text
                async with self._db.get_session() as session:
                    # S182 #2: add `ORDER BY updated_at ASC NULLS FIRST` for
                    # deterministic rotation — pre-S182 the query had no ORDER BY,
                    # so same "first 1000" rows returned each cycle (verified by
                    # Phase 0.2-b Investigation #2: md5 identical across 30s runs
                    # when service was idle, with partial churn only from an
                    # unknown upstream writer on `markets`). With NULLS FIRST,
                    # rows never-refreshed get first priority; oldest stale next.
                    # Env flag ESPORTS_MARKETS_REFRESH_V2_ENABLED (default true)
                    # toggles the ORDER BY. Pre-existing behavior (no ordering)
                    # restored via flag-off rollback without code revert.
                    # S216 Thread C: broaden refresh universe from category='esports'
                    # to the matcher's keyword filter. Polymarket miscategorizes ~half
                    # of LoL/CS2 markets as 'sports' or 'crypto', and the matcher at
                    # get_tradeable_esports_markets already uses the keyword filter
                    # (not category) — so prior to this fix, the matcher SAW those
                    # markets but they got stale prices because refresh ignored them.
                    # KEEP IN SYNC with get_tradeable_esports_markets keyword filter
                    # (~line 178-198 of this file).
                    _KW_FILTER = """(
                        question ILIKE '%esports%'
                        OR question ILIKE '%league of legends%'
                        OR question ILIKE '%counter-strike%'
                        OR question ILIKE '%cs2%'
                        OR question ILIKE '%csgo%'
                        OR question ILIKE '%blast premier%'
                        OR question ILIKE '%dota%'
                        OR question ILIKE '%the international%'
                        OR question ILIKE '%valorant%'
                        OR question ILIKE '%champions tour%'
                        OR question ILIKE '%call of duty%'
                        OR question ILIKE '%rainbow six%'
                        OR question ILIKE '%six invitational%'
                        OR question ILIKE '%starcraft%'
                        OR question ILIKE '%sc2%'
                        OR question ILIKE '%brood war%'
                        OR question ILIKE '%rocket league%'
                        OR question ILIKE '%rlcs%'
                        OR question ~* '\\y(lol|lck|lec|lpl|lcs|msi|esl|pgl|iem|dpc|cdl|gsl|asl|vct|r6|ti)\\y'
                    )"""
                    if _MARKETS_REFRESH_V2_ENABLED:
                        _query_sql = f"""
                            SELECT id, condition_id FROM markets
                            WHERE {_KW_FILTER}
                              AND active = true
                              AND (resolved = false OR resolved IS NULL)
                              AND condition_id IS NOT NULL
                              AND condition_id != ''
                            ORDER BY updated_at ASC NULLS FIRST
                            LIMIT 1000
                        """
                    else:
                        _query_sql = f"""
                            SELECT id, condition_id FROM markets
                            WHERE {_KW_FILTER}
                              AND active = true
                              AND (resolved = false OR resolved IS NULL)
                              AND condition_id IS NOT NULL
                              AND condition_id != ''
                            LIMIT 1000
                        """
                    rows = await session.execute(text(_query_sql))
                    id_pairs = [(str(r[0]), str(r[1])) for r in rows.fetchall() if r[1]]
            except Exception as exc:
                logger.warning("EsportsMarketService: refresh query failed", error=str(exc))
                return stats
        else:
            # For specified market IDs, we need to look up condition_ids
            try:
                from sqlalchemy import text
                async with self._db.get_session() as session:
                    rows = await session.execute(text("""
                        SELECT id, condition_id FROM markets
                        WHERE id = ANY(:ids)
                          AND condition_id IS NOT NULL
                          AND condition_id != ''
                    """), {"ids": market_ids})
                    id_pairs = [(str(r[0]), str(r[1])) for r in rows.fetchall() if r[1]]
            except Exception as exc:
                logger.warning("EsportsMarketService: refresh lookup failed", error=str(exc))
                return stats

        stats["total"] = len(id_pairs)
        if not id_pairs:
            return stats

        # Lazy init httpx client
        if self._httpx_client is None:
            import httpx
            self._httpx_client = httpx.AsyncClient(timeout=15.0)

        from sqlalchemy import text

        for market_id, condition_id in id_pairs:
            try:
                url = f"https://clob.polymarket.com/markets/{condition_id}"
                r = await self._httpx_client.get(url)
                if r.status_code != 200:
                    stats["errors"] += 1
                    continue

                clob = r.json()
                tokens = clob.get("tokens") or []
                closed = clob.get("closed", False)

                # Extract prices from tokens
                yes_price = no_price = None
                for t in tokens:
                    outcome = (t.get("outcome") or "").upper().strip()
                    price = t.get("price")
                    if price is not None:
                        try:
                            price = float(price)
                        except (ValueError, TypeError):
                            price = None
                    if outcome == "YES" and price is not None:
                        yes_price = price
                    elif outcome == "NO" and price is not None:
                        no_price = price
                # Fallback: positional tokens
                if yes_price is None and len(tokens) >= 1:
                    _p = tokens[0].get("price")
                    if _p is not None:
                        try:
                            yes_price = float(_p)
                        except (ValueError, TypeError):
                            pass
                if no_price is None and len(tokens) >= 2:
                    _p = tokens[1].get("price")
                    if _p is not None:
                        try:
                            no_price = float(_p)
                        except (ValueError, TypeError):
                            pass

                # Extract volume if available
                vol = None
                for vk in ("volume", "volumeNum", "volume_num"):
                    _v = clob.get(vk)
                    if _v is not None:
                        try:
                            vol = float(_v)
                            break
                        except (ValueError, TypeError):
                            pass

                # Update DB
                async with self._db.get_session() as session:
                    update_parts = ["updated_at = NOW()"]
                    params: Dict[str, Any] = {"mid": market_id}

                    if yes_price is not None:
                        update_parts.append("yes_price = :yp")
                        params["yp"] = yes_price
                    if no_price is not None:
                        update_parts.append("no_price = :np")
                        params["np"] = no_price
                    if closed:
                        update_parts.append("active = false")
                        stats["closed"] += 1
                    if vol is not None and vol > 0:
                        update_parts.append("volume = GREATEST(COALESCE(volume, 0), :vol)")
                        params["vol"] = vol

                    await session.execute(
                        text(f"UPDATE markets SET {', '.join(update_parts)} WHERE id = :mid"),
                        params,
                    )
                    await session.commit()

                stats["refreshed"] += 1

            except Exception as exc:
                stats["errors"] += 1
                # S216: was logger.debug → invisible at default INFO level.
                # The cycle-end heartbeat surfaces aggregate `errors=N` but
                # without per-error context you can't tell whether the
                # failures are (a) a single transient CLOB hiccup or (b) a
                # sustained outage affecting a specific market subset.
                # WARNING with truncated market_id + error string gives
                # the operator triage data; structlog will suppress dupes
                # when the same error string repeats across markets.
                logger.warning(
                    "EsportsMarketService: CLOB refresh failed",
                    market_id=market_id[:20] if market_id else "",
                    error=str(exc),
                )

            # Rate limit: 100ms between CLOB API calls (10 req/s max)
            await asyncio.sleep(0.1)

        self._last_refresh_stats = stats

        # Invalidate cache after refresh so next query gets fresh data
        self._cache = None

        return stats

    def start_background_refresh(self) -> asyncio.Task:
        """Launch async background task that refreshes CLOB prices every 5 min.

        Returns the asyncio Task so the caller can track/cancel it.
        """
        async def _refresh_loop():
            while True:
                try:
                    stats = await self.refresh_market_prices()
                    # S182 #2: emit heartbeat OUTSIDE the stats["total"] > 0 guard.
                    # Pre-S182 the log was suppressed on zero-row cycles, which is
                    # exactly when the refresh is broken (either the in-scope query
                    # returns 0 or silently crashes) — so the log never fired for 18h
                    # and nobody knew the service was idle. Now every cycle emits
                    # EsportsMarketService_cycle_complete regardless of total.
                    if _MARKETS_REFRESH_V2_ENABLED:
                        logger.info(
                            "EsportsMarketService_cycle_complete",
                            total=stats.get("total", 0),
                            refreshed=stats.get("refreshed", 0),
                            closed=stats.get("closed", 0),
                            errors=stats.get("errors", 0),
                        )
                    elif stats["total"] > 0:
                        # Legacy behavior: only log non-zero cycles
                        logger.info(
                            "EsportsMarketService: price refresh complete",
                            total=stats["total"],
                            refreshed=stats["refreshed"],
                            closed=stats["closed"],
                            errors=stats["errors"],
                        )
                except Exception as exc:
                    # S182 #2: logger.debug → logger.warning with exc_info=True.
                    # Pre-S182 the silent-exception log was DEBUG (invisible in
                    # default logging config), which masked an 18h+ outage where
                    # the refresh loop was crashing on every iteration. Warning
                    # + traceback makes future crashes visible immediately.
                    if _MARKETS_REFRESH_V2_ENABLED:
                        logger.warning(
                            "EsportsMarketService: refresh loop error",
                            error=str(exc),
                            exc_info=True,
                        )
                    else:
                        logger.debug("EsportsMarketService: refresh loop error", error=str(exc))

                await asyncio.sleep(_REFRESH_INTERVAL)

        self._refresh_task = asyncio.create_task(_refresh_loop())
        logger.info(
            "EsportsMarketService: background refresh started",
            interval_seconds=_REFRESH_INTERVAL,
        )
        return self._refresh_task

    async def close(self) -> None:
        """Cancel background task and close HTTP client."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None
        logger.info("EsportsMarketService: closed")

    @property
    def last_refresh_stats(self) -> Dict[str, int]:
        """Return stats from the most recent refresh cycle."""
        return dict(self._last_refresh_stats)
