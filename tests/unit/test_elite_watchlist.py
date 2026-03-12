"""
Unit tests for EliteWatchlist — real-time WebSocket copy trading.

Tests:
- Watchlist refresh from monthly leaderboard
- Efficiency scoring (profit/volume)
- on_trade_event filtering (non-watchlist, dedup, price bounds)
- on_trade_event matching and copy execution
- Daily refresh check
- Stats tracking
"""
import asyncio
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.elite_watchlist import EliteWatchlist


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_top_users = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_mirror_bot():
    bot = MagicMock()
    bot._execute_mirror_trade = AsyncMock(return_value=True)
    bot._can_open_position = MagicMock(return_value=True)
    bot._get_token_side = AsyncMock(return_value="YES")
    bot._track_open_position = MagicMock()
    bot._persist_trader_to_position = AsyncMock()
    bot.mirrored_trades = OrderedDict()
    bot.base_engine = MagicMock()
    return bot


@pytest.fixture
def watchlist(mock_client, mock_mirror_bot):
    return EliteWatchlist(client=mock_client, db=None, mirror_bot=mock_mirror_bot)


def _make_leaderboard_data():
    """Simulate monthly leaderboard API response."""
    return [
        {"address": "0xAAA", "pnl": 50000, "vol": 200000, "rank": 1, "userName": "TopTrader"},
        {"address": "0xBBB", "pnl": 30000, "vol": 300000, "rank": 2, "userName": "VolumeKing"},
        {"address": "0xCCC", "pnl": 10000, "vol": 20000, "rank": 3, "userName": "Efficient"},
    ]


@pytest.mark.asyncio
async def test_refresh_watchlist_adds_all_traders(watchlist):
    """All leaderboard traders should be added (no win rate gate)."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        count = await watchlist.refresh_watchlist()
    assert count == 3
    assert "0xaaa" in watchlist._watchlist_addresses
    assert "0xbbb" in watchlist._watchlist_addresses
    assert "0xccc" in watchlist._watchlist_addresses


@pytest.mark.asyncio
async def test_efficiency_scoring(watchlist):
    """Efficiency = pnl/vol. Higher efficiency traders get higher confidence."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    # 0xAAA: 50000/200000 = 0.25
    # 0xBBB: 30000/300000 = 0.10
    # 0xCCC: 10000/20000  = 0.50
    assert abs(watchlist._watchlist_data["0xaaa"]["efficiency"] - 0.25) < 0.01
    assert abs(watchlist._watchlist_data["0xbbb"]["efficiency"] - 0.10) < 0.01
    assert abs(watchlist._watchlist_data["0xccc"]["efficiency"] - 0.50) < 0.01


@pytest.mark.asyncio
async def test_on_trade_event_ignores_non_watchlist(watchlist):
    """Events from non-watchlist traders should be ignored."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xNOBODY"},
        "transaction_hash": "0xTX1",
    }
    await watchlist.on_trade_event(event)
    assert watchlist._events_matched == 0


@pytest.mark.asyncio
async def test_on_trade_event_copies_watchlist_trader(watchlist, mock_mirror_bot):
    """Events from watchlist traders should trigger copy."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xAAA"},
        "transaction_hash": "0xTX1",
    }
    await watchlist.on_trade_event(event)
    assert watchlist._events_matched == 1
    assert watchlist._copies_attempted == 1
    assert watchlist._copies_executed == 1
    mock_mirror_bot._execute_mirror_trade.assert_called_once()
    call_kwargs = mock_mirror_bot._execute_mirror_trade.call_args
    assert call_kwargs.kwargs["side"] == "YES"
    assert call_kwargs.kwargs["price"] == 0.65
    # Efficient trader (0.25 efficiency) should get higher confidence
    assert call_kwargs.kwargs["confidence"] > 0.55


@pytest.mark.asyncio
async def test_on_trade_event_dedup_by_tx_hash(watchlist, mock_mirror_bot):
    """Same transaction_hash should not trigger duplicate copy."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xAAA"},
        "transaction_hash": "0xTX1",
    }
    await watchlist.on_trade_event(event)
    await watchlist.on_trade_event(event)  # duplicate
    assert watchlist._copies_attempted == 1


@pytest.mark.asyncio
async def test_on_trade_event_rejects_extreme_prices(watchlist):
    """Prices at 0.01 or below, 0.99 or above should be rejected."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    for bad_price in ["0.001", "0.01", "0.99", "1.0"]:
        event = {
            "event_type": "last_trade_price",
            "market": "0xMARKET1",
            "asset_id": "TOKEN1",
            "price": bad_price,
            "size": "100",
            "side": "BUY",
            "outcome": "Yes",
            "user": {"address": "0xAAA"},
            "transaction_hash": f"0xTX_{bad_price}",
        }
        await watchlist.on_trade_event(event)
    assert watchlist._copies_attempted == 0


@pytest.mark.asyncio
async def test_on_trade_event_respects_position_cap(watchlist, mock_mirror_bot):
    """When _can_open_position returns False, should not attempt copy."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    mock_mirror_bot._can_open_position.return_value = False
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xAAA"},
        "transaction_hash": "0xTX1",
    }
    await watchlist.on_trade_event(event)
    assert watchlist._copies_attempted == 0


@pytest.mark.asyncio
async def test_on_trade_event_no_user_field(watchlist):
    """Events without user field should be silently ignored."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
    }
    await watchlist.on_trade_event(event)
    assert watchlist._events_matched == 0


@pytest.mark.asyncio
async def test_on_trade_event_sell_skips_position_check(watchlist, mock_mirror_bot):
    """SELL events should skip _can_open_position (they close positions)."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    mock_mirror_bot._can_open_position.return_value = False
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "SELL",
        "outcome": "Yes",
        "user": {"address": "0xAAA"},
        "transaction_hash": "0xTX_SELL",
    }
    await watchlist.on_trade_event(event)
    assert watchlist._copies_attempted == 1


@pytest.mark.asyncio
async def test_get_stats(watchlist):
    """Stats should reflect current state."""
    stats = watchlist.get_stats()
    assert stats["watchlist_size"] == 0
    assert stats["events_received"] == 0
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    stats = watchlist.get_stats()
    assert stats["watchlist_size"] == 3
    assert stats["last_refresh_date"] is not None


@pytest.mark.asyncio
async def test_needs_refresh_on_new_day(watchlist):
    """needs_refresh should return True when date changes."""
    assert watchlist.needs_refresh() is True  # Never refreshed
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    assert watchlist.needs_refresh() is False  # Just refreshed today
    watchlist._last_refresh_date = "2020-01-01"  # Fake old date
    assert watchlist.needs_refresh() is True


@pytest.mark.asyncio
async def test_refresh_fallback_to_get_top_users(watchlist, mock_client):
    """When leaderboard fails, should fall back to get_top_users."""
    mock_client.get_top_users.return_value = [
        {"address": "0xFALLBACK", "totalProfit": 5000, "totalVolume": 50000},
    ]
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = []  # Leaderboard returns nothing
        count = await watchlist.refresh_watchlist()
    assert count == 1
    assert "0xfallback" in watchlist._watchlist_addresses


@pytest.mark.asyncio
async def test_address_case_insensitive(watchlist, mock_mirror_bot):
    """Watchlist lookup should be case-insensitive."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xaaa"},
        "transaction_hash": "0xTX_CASE",
    }
    await watchlist.on_trade_event(event)
    assert watchlist._copies_attempted == 1


@pytest.mark.asyncio
async def test_confidence_scales_with_efficiency(watchlist, mock_mirror_bot):
    """High efficiency traders should get higher confidence (larger Kelly sizing)."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()

    # 0xCCC has highest efficiency (0.50) → should get highest confidence
    event_efficient = {
        "event_type": "last_trade_price",
        "market": "0xMARKET1",
        "asset_id": "TOKEN1",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xCCC"},
        "transaction_hash": "0xTX_EFF",
    }
    await watchlist.on_trade_event(event_efficient)
    eff_conf = mock_mirror_bot._execute_mirror_trade.call_args.kwargs["confidence"]

    mock_mirror_bot._execute_mirror_trade.reset_mock()

    # 0xBBB has lowest efficiency (0.10) → should get lower confidence
    event_grinder = {
        "event_type": "last_trade_price",
        "market": "0xMARKET2",
        "asset_id": "TOKEN2",
        "price": "0.65",
        "size": "100",
        "side": "BUY",
        "outcome": "Yes",
        "user": {"address": "0xBBB"},
        "transaction_hash": "0xTX_GRIND",
    }
    await watchlist.on_trade_event(event_grinder)
    grinder_conf = mock_mirror_bot._execute_mirror_trade.call_args.kwargs["confidence"]

    # Efficient trader should have higher confidence
    assert eff_conf > grinder_conf
    # Both should be >= 0.55 base and <= 0.70 cap
    assert 0.55 <= grinder_conf <= 0.70
    assert 0.55 <= eff_conf <= 0.70


# ── RTDS on_rtds_trade tests ─────────────────────────────────────


def _make_rtds_event(addr="0xAAA", asset="TOKEN1", condition_id="0xMARKET1",
                     price=0.65, size=100, side="BUY", outcome="Yes"):
    """Simulate an RTDS activity/trades event."""
    return {
        "asset": asset,
        "conditionId": condition_id,
        "eventSlug": "some-event",
        "outcome": outcome,
        "outcomeIndex": 0,
        "price": price,
        "proxyWallet": addr,
        "pseudonym": "trader1",
        "side": side,
        "size": size,
        "slug": "some-market",
    }


@pytest.mark.asyncio
async def test_rtds_ignores_non_watchlist(watchlist):
    """RTDS events from non-watchlist traders should be fast-rejected."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = _make_rtds_event(addr="0xNOBODY")
    await watchlist.on_rtds_trade(event)
    assert watchlist._events_matched == 0


@pytest.mark.asyncio
async def test_rtds_copies_watchlist_trader(watchlist, mock_mirror_bot):
    """RTDS events from watchlist traders should trigger copy via on_trade_event."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = _make_rtds_event(addr="0xAAA")
    await watchlist.on_rtds_trade(event)
    assert watchlist._events_matched == 1
    assert watchlist._copies_executed == 1
    mock_mirror_bot._execute_mirror_trade.assert_called_once()
    call_kwargs = mock_mirror_bot._execute_mirror_trade.call_args.kwargs
    assert call_kwargs["side"] == "YES"
    assert call_kwargs["price"] == 0.65
    assert call_kwargs["market_id"] == "0xMARKET1"
    assert call_kwargs["token_id"] == "TOKEN1"


@pytest.mark.asyncio
async def test_rtds_dedup_same_trade(watchlist, mock_mirror_bot):
    """Identical RTDS events should be deduped (same addr+asset+price+size+side)."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = _make_rtds_event(addr="0xAAA")
    await watchlist.on_rtds_trade(event)
    await watchlist.on_rtds_trade(event)  # duplicate
    assert watchlist._copies_attempted == 1


@pytest.mark.asyncio
async def test_rtds_different_trades_not_deduped(watchlist, mock_mirror_bot):
    """RTDS events with different prices should NOT be deduped."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event1 = _make_rtds_event(addr="0xAAA", price=0.65)
    event2 = _make_rtds_event(addr="0xAAA", price=0.70)
    await watchlist.on_rtds_trade(event1)
    await watchlist.on_rtds_trade(event2)
    assert watchlist._copies_attempted == 2


@pytest.mark.asyncio
async def test_rtds_field_mapping(watchlist, mock_mirror_bot):
    """RTDS fields should be correctly mapped to internal format."""
    with patch.object(watchlist, "_fetch_monthly_leaderboard", new_callable=AsyncMock) as mock_lb:
        mock_lb.return_value = _make_leaderboard_data()
        await watchlist.refresh_watchlist()
    event = _make_rtds_event(addr="0xBBB", asset="TOKEN99", condition_id="0xMKT99",
                             price=0.42, size=250, side="BUY", outcome="No")
    await watchlist.on_rtds_trade(event)
    call_kwargs = mock_mirror_bot._execute_mirror_trade.call_args.kwargs
    assert call_kwargs["market_id"] == "0xMKT99"
    assert call_kwargs["token_id"] == "TOKEN99"
    assert call_kwargs["side"] == "NO"
    assert call_kwargs["price"] == 0.42
