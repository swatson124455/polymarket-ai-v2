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

# Game keywords for the soccer/football double-gate
_ESPORTS_GAME_KEYWORDS: Dict[str, List[str]] = {
    "lol": ["league of legends", "lol ", "lck", "lec", "lpl", "lcs", "worlds", "msi"],
    "cs2": ["counter-strike", "cs2", "csgo", "blast premier", "esl ", "pgl ", "iem "],
    "dota2": ["dota", "the international", " ti ", "dpc"],
    "valorant": ["valorant", "vct", "champions tour"],
}


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
    for keywords in _ESPORTS_GAME_KEYWORDS.values():
        if any(kw in q for kw in keywords):
            return True
    return False


def _detect_game(question: str) -> str:
    """Detect which esports game a market is for from question text."""
    q = question.lower()
    for game, keywords in _ESPORTS_GAME_KEYWORDS.items():
        if any(kw in q for kw in keywords):
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
                # Direct DB query: category='esports', active, not resolved, has prices.
                # NO liquidity filter — CLOB markets have liq=0 by design.
                # Volume gate filters out untradeable/dead markets.
                rows = await session.execute(text("""
                    SELECT id, condition_id, question, slug, category,
                           liquidity, volume, yes_token_id, no_token_id,
                           yes_price, no_price, resolution_source,
                           end_date_iso, active, resolved, resolution
                    FROM markets
                    WHERE category = 'esports'
                      AND active = true
                      AND (resolved = false OR resolved IS NULL)
                      AND yes_price IS NOT NULL
                    ORDER BY volume DESC NULLS LAST
                    LIMIT 500
                """))
                all_rows = rows.fetchall()

            markets = []
            for row in all_rows:
                question = str(row[2] or "")

                # Double-gate: reject soccer/football miscategorized as esports
                if not _is_real_esports(question):
                    continue

                # Volume gate
                vol = float(row[6] or 0)
                if vol < vol_min:
                    continue

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
                    rows = await session.execute(text("""
                        SELECT id, condition_id FROM markets
                        WHERE category = 'esports'
                          AND active = true
                          AND (resolved = false OR resolved IS NULL)
                          AND condition_id IS NOT NULL
                          AND condition_id != ''
                        LIMIT 1000
                    """))
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
                logger.debug(
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
                    if stats["total"] > 0:
                        logger.info(
                            "EsportsMarketService: price refresh complete",
                            total=stats["total"],
                            refreshed=stats["refreshed"],
                            closed=stats["closed"],
                            errors=stats["errors"],
                        )
                except Exception as exc:
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
