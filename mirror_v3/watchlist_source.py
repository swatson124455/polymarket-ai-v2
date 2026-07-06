"""v3 watched-wallet source — the `is_watched` predicate the collector needs.

The signal collector (mirror_v3/signal_collector.py) only records trades by
WATCHED wallets. Old MB gets that set from EliteWatchlist, which is welded to the
dead whale-copy scoring stack (efficiency ranking, decay, quotas, confidence).
The v3 silo must NOT import that (mirror_v3/__init__.py isolation contract), and
it does not need it: the watched SET is simply the top-N monthly-leaderboard
addresses — the scoring only fed old MB's (now dead) sizing/confidence, never
membership (verified: elite_watchlist.refresh_watchlist adds every leaderboard
address to _watchlist_addresses; scoring is layered on top for sizing only).

So this module re-implements ONLY the leaderboard fetch (a clean paginated Data
API GET, copied from elite_watchlist._fetch_monthly_leaderboard) and exposes an
O(1) `is_watched()` predicate over a cached, periodically-refreshed address set.
No scoring, no ranking, no DB — a wallet is watched iff it is a current top-N
monthly PnL leader.

Fail-safe: a failed refresh keeps the previous set (never empties the watchlist
mid-run). The `fetcher` is injectable so tests exercise the predicate/refresh
logic without hitting the network.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, List, Optional

from structlog import get_logger

logger = get_logger()

# Copied verbatim from elite_watchlist.py (the API contract, not the scoring).
_LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
_LEADERBOARD_PAGE_SIZE = 50      # API max per request
_LEADERBOARD_MAX_OFFSET = 1000   # API caps offset at 1000 -> max 1050 traders

# Old MB refreshes the watchlist every 6h (elite_watchlist.needs_refresh).
_DEFAULT_REFRESH_INTERVAL_S = 6 * 3600
_DEFAULT_SIZE = 1000


class LeaderboardWatchlist:
    """Cached set of top-N monthly-leaderboard addresses + an is_watched predicate.

    Args:
        size: how many leaders to watch (default 1000; API hard-caps ~1050).
        fetcher: coroutine returning a list of raw address strings. Defaults to
            the live Data API fetch. Injected in tests to avoid the network.
        refresh_interval_s: TTL before needs_refresh() returns True (default 6h).
    """

    def __init__(
        self,
        *,
        size: int = _DEFAULT_SIZE,
        fetcher: Optional[Callable[[], Awaitable[List[str]]]] = None,
        refresh_interval_s: int = _DEFAULT_REFRESH_INTERVAL_S,
    ) -> None:
        self._size = int(size)
        self._fetcher = fetcher or self._default_fetch
        self._refresh_interval_s = int(refresh_interval_s)
        self._addresses: set[str] = set()
        self._last_refresh_mono: Optional[float] = None

    # ── predicate + introspection (collector-facing) ─────────────────────────

    def is_watched(self, addr_lower: str) -> bool:
        """O(1) membership. Caller lowercases (matches collector/old-MB behavior)."""
        return addr_lower in self._addresses

    def size(self) -> int:
        return len(self._addresses)

    def needs_refresh(self) -> bool:
        if self._last_refresh_mono is None:
            return True
        return (time.monotonic() - self._last_refresh_mono) >= self._refresh_interval_s

    # ── refresh ──────────────────────────────────────────────────────────────

    async def refresh(self) -> int:
        """Rebuild the watched set from the leaderboard. Fail-safe: on error or an
        empty fetch, the previous set is KEPT (never emptied mid-run). Returns the
        current watched-set size."""
        try:
            raw = await self._fetcher()
        except Exception as e:
            logger.warning("v3_watchlist_refresh_failed", error=str(e), kept=len(self._addresses))
            return len(self._addresses)
        cleaned = {a.lower() for a in raw if isinstance(a, str) and a}
        if not cleaned:
            logger.warning("v3_watchlist_refresh_empty", kept=len(self._addresses))
            return len(self._addresses)
        self._addresses = cleaned
        self._last_refresh_mono = time.monotonic()
        logger.info("v3_watchlist_refreshed", size=len(self._addresses))
        return len(self._addresses)

    async def _default_fetch(self) -> List[str]:
        """Live paginated monthly-leaderboard fetch (Data API, no auth).
        GET /v1/leaderboard?timePeriod=MONTH&orderBy=PNL&category=OVERALL&limit=50&offset=N
        """
        import aiohttp

        out: List[str] = []
        seen: set[str] = set()
        offset = 0
        effective_limit = min(self._size, _LEADERBOARD_MAX_OFFSET + _LEADERBOARD_PAGE_SIZE)
        async with aiohttp.ClientSession() as session:
            while len(out) < effective_limit and offset <= _LEADERBOARD_MAX_OFFSET:
                params = {
                    "timePeriod": "MONTH",
                    "orderBy": "PNL",
                    "category": "OVERALL",
                    "limit": _LEADERBOARD_PAGE_SIZE,
                    "offset": offset,
                }
                async with session.get(
                    _LEADERBOARD_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("v3_leaderboard_fetch_failed", status=resp.status, offset=offset)
                        break
                    data = await resp.json()
                if not data or not isinstance(data, list):
                    break
                for u in data:
                    if not isinstance(u, dict):
                        continue
                    addr = u.get("proxyWallet") or u.get("address")
                    if not addr or addr in seen:
                        continue
                    seen.add(addr)
                    out.append(addr)
                    if len(out) >= effective_limit:
                        break
                if len(data) < _LEADERBOARD_PAGE_SIZE:
                    break  # last page
                offset += _LEADERBOARD_PAGE_SIZE
                await asyncio.sleep(0.15)  # rate-limit courtesy (matches old MB)
        return out
