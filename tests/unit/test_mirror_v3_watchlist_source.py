"""v3 watched-wallet source tests — leaderboard-backed is_watched predicate.

The collector needs a watched-wallet set that does NOT drag in the dead scoring
stack. This verifies the injected-fetcher refresh, the O(1) predicate, the
fail-safe (a bad/empty refresh keeps the prior set), and the TTL.
"""
from __future__ import annotations

import pytest

from mirror_v3.watchlist_source import LeaderboardWatchlist

A = "0xAAA000000000000000000000000000000000AAAA"
B = "0xBBB000000000000000000000000000000000BBBB"


async def _fetch_ab():
    return [A, B]


@pytest.mark.asyncio
async def test_refresh_populates_and_lowercases():
    w = LeaderboardWatchlist(fetcher=_fetch_ab)
    n = await w.refresh()
    assert n == 2
    assert w.is_watched(A.lower())
    assert w.is_watched(B.lower())
    # predicate is exact on the lowercased form
    assert not w.is_watched(A)  # caller must lowercase (matches collector)


@pytest.mark.asyncio
async def test_unwatched_is_false():
    w = LeaderboardWatchlist(fetcher=_fetch_ab)
    await w.refresh()
    assert not w.is_watched("0xdead000000000000000000000000000000000000")


@pytest.mark.asyncio
async def test_failed_fetch_keeps_previous_set():
    w = LeaderboardWatchlist(fetcher=_fetch_ab)
    await w.refresh()
    assert w.size() == 2

    async def _boom():
        raise RuntimeError("leaderboard down")

    w._fetcher = _boom
    n = await w.refresh()  # must NOT raise, must NOT empty
    assert n == 2
    assert w.is_watched(A.lower())


@pytest.mark.asyncio
async def test_empty_fetch_keeps_previous_set():
    w = LeaderboardWatchlist(fetcher=_fetch_ab)
    await w.refresh()

    async def _empty():
        return []

    w._fetcher = _empty
    n = await w.refresh()
    assert n == 2  # kept, not emptied


@pytest.mark.asyncio
async def test_refresh_replaces_set():
    w = LeaderboardWatchlist(fetcher=_fetch_ab)
    await w.refresh()

    async def _only_b():
        return [B]

    w._fetcher = _only_b
    await w.refresh()
    assert w.is_watched(B.lower())
    assert not w.is_watched(A.lower())  # A dropped off the leaderboard


@pytest.mark.asyncio
async def test_needs_refresh_ttl():
    w = LeaderboardWatchlist(fetcher=_fetch_ab, refresh_interval_s=10_000)
    assert w.needs_refresh() is True  # never refreshed yet
    await w.refresh()
    assert w.needs_refresh() is False  # just refreshed, long TTL


@pytest.mark.asyncio
async def test_refresh_skips_malformed_addresses():
    async def _mixed():
        return [A, "", None, 123, B]  # only A, B are valid strings

    w = LeaderboardWatchlist(fetcher=_mixed)
    n = await w.refresh()
    assert n == 2
    assert w.is_watched(A.lower()) and w.is_watched(B.lower())
