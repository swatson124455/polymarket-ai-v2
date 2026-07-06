"""End-to-end integration of the v3 collection path — REAL objects, faked edges.

Unit tests cover each module alone. This drives the actual seams between them,
exactly as run.py wires them:

    RTDSWebSocket._dispatch (real envelope unwrap)
        -> V3SignalCollector.on_rtds_trade (real filters)
            -> LeaderboardWatchlist.is_watched (real predicate)
            -> db.insert_mirror_rejected_signal (captured)

Only the network socket and the DB are faked; every filter/transform/branch that
runs in production runs here. This is the strongest offline proof that the three
modules actually connect.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from base_engine.data.rtds_websocket import RTDSWebSocket
from mirror_v3.signal_collector import V3_SOURCE_TAG, V3SignalCollector
from mirror_v3.watchlist_source import LeaderboardWatchlist

WHALE = "0x1234567890abcdef1234567890abcdef12345678"
STRANGER = "0xffff000000000000000000000000000000000000"


async def _wire(watched_addrs):
    """Build the real chain the way run.py does; return (socket, db)."""
    watchlist = LeaderboardWatchlist(fetcher=lambda: _return(watched_addrs))
    await watchlist.refresh()
    db = AsyncMock()
    db.insert_mirror_rejected_signal = AsyncMock(return_value=None)
    collector = V3SignalCollector(
        db, is_watched=watchlist.is_watched, is_restored=lambda: True,
    )
    socket = RTDSWebSocket(handler=collector.on_rtds_trade)
    return socket, db, collector


async def _return(v):
    return v


def _rtds_envelope(**over):
    """A realistic RTDS activity/trades envelope (payload nested, as the real feed sends)."""
    trade = {
        "asset": "tok-42",
        "conditionId": "0xcond",
        "outcome": "Yes",
        "price": 0.60,
        "proxyWallet": WHALE,
        "side": "BUY",
        "size": 500,               # 500 * 0.60 = $300
        "transactionHash": "0xabc",
    }
    trade.update(over)
    return {"topic": "activity", "type": "trades", "payload": trade}


@pytest.mark.asyncio
async def test_watched_whale_trade_flows_all_the_way_to_db():
    socket, db, collector = await _wire([WHALE])

    # Drive the REAL dispatch path the socket uses on every recv.
    await socket._dispatch(_rtds_envelope())

    db.insert_mirror_rejected_signal.assert_awaited_once()
    kw = db.insert_mirror_rejected_signal.await_args.kwargs
    assert kw["trader_address"] == WHALE          # full address preserved
    assert kw["market_id"] == "0xcond"
    assert kw["side"] == "YES"
    assert kw["whale_trade_usd"] == pytest.approx(300.0)
    assert kw["metadata"]["source"] == V3_SOURCE_TAG
    assert collector.get_stats()["logged"] == 1
    assert socket._events_dispatched == 1         # the socket really routed it


@pytest.mark.asyncio
async def test_unwatched_wallet_flows_through_but_writes_nothing():
    socket, db, collector = await _wire([WHALE])           # WHALE watched, STRANGER not
    await socket._dispatch(_rtds_envelope(proxyWallet=STRANGER, transactionHash="0xz"))
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert collector.get_stats()["watched_matched"] == 0


@pytest.mark.asyncio
async def test_payload_as_list_dispatches_each(  # RTDS may batch trades in a list
):
    socket, db, collector = await _wire([WHALE])
    batch = {"payload": [
        _rtds_envelope(transactionHash="0x1")["payload"],
        _rtds_envelope(transactionHash="0x2", outcome="No", price=0.40, size=500)["payload"],
    ]}
    await socket._dispatch(batch)
    assert db.insert_mirror_rejected_signal.await_count == 2
    sides = sorted(c.kwargs["side"] for c in db.insert_mirror_rejected_signal.await_args_list)
    assert sides == ["NO", "YES"]


@pytest.mark.asyncio
async def test_watchlist_refresh_drops_wallet_then_stops_writing():
    """After a leaderboard refresh drops the whale, the same feed stops logging it —
    proving the live predicate (not a frozen snapshot) gates the collector."""
    watchlist = LeaderboardWatchlist(fetcher=lambda: _return([WHALE]))
    await watchlist.refresh()
    db = AsyncMock(); db.insert_mirror_rejected_signal = AsyncMock(return_value=None)
    collector = V3SignalCollector(db, is_watched=watchlist.is_watched, is_restored=lambda: True)
    socket = RTDSWebSocket(handler=collector.on_rtds_trade)

    await socket._dispatch(_rtds_envelope(transactionHash="0x1"))
    assert db.insert_mirror_rejected_signal.await_count == 1

    # Whale falls off the leaderboard on the next refresh.
    watchlist._fetcher = lambda: _return([STRANGER])
    await watchlist.refresh()

    await socket._dispatch(_rtds_envelope(transactionHash="0x2"))
    assert db.insert_mirror_rejected_signal.await_count == 1  # unchanged — no new write
