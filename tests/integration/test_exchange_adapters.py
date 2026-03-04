"""
Integration tests for exchange adapter implementations.

Tests the normalized data model compliance of each adapter without requiring
real API credentials (uses mock HTTP responses).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from base_engine.exchanges.models import (
    FeeSchedule, MarketSnapshot, OrderBook, OrderResult, PositionSnapshot,
)


# ── Kalshi Adapter ───────────────────────────────────────────────────────────

class TestKalshiAdapter:
    def test_fee_schedule(self):
        from base_engine.exchanges.kalshi_adapter import KalshiAdapter
        adapter = KalshiAdapter(kalshi_client=None)
        fees = adapter.fee_schedule()
        assert isinstance(fees, FeeSchedule)
        assert fees.platform == "kalshi"
        assert fees.taker_fee == 0.01

    def test_platform_name(self):
        from base_engine.exchanges.kalshi_adapter import KalshiAdapter
        adapter = KalshiAdapter(kalshi_client=None)
        assert adapter.platform_name() == "kalshi"

    def test_is_enabled_without_client(self):
        from base_engine.exchanges.kalshi_adapter import KalshiAdapter
        adapter = KalshiAdapter(kalshi_client=None)
        assert not adapter.is_enabled()

    @pytest.mark.asyncio
    async def test_get_markets_returns_empty_without_client(self):
        from base_engine.exchanges.kalshi_adapter import KalshiAdapter
        adapter = KalshiAdapter(kalshi_client=None)
        result = await adapter.get_markets()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_positions_uses_entry_price(self):
        """Regression: was using avg_entry_price which doesn't exist on PositionSnapshot."""
        from base_engine.exchanges.kalshi_adapter import KalshiAdapter
        mock_client = AsyncMock()
        mock_client.get_positions = AsyncMock(return_value=[
            {"ticker": "KXBTC-100K", "position": 5, "average_price": 6500, "pnl": 100},
        ])
        adapter = KalshiAdapter(kalshi_client=mock_client)
        positions = await adapter.get_positions()
        assert len(positions) == 1
        assert positions[0].entry_price == 65.0  # 6500 / 100
        assert positions[0].platform == "kalshi"


# ── Coinbase Adapter ─────────────────────────────────────────────────────────

class TestCoinbaseAdapter:
    def test_fee_schedule(self):
        from base_engine.exchanges.coinbase_adapter import CoinbaseAdapter
        adapter = CoinbaseAdapter()
        fees = adapter.fee_schedule()
        assert fees.platform == "coinbase"
        assert fees.taker_fee == 0.02

    def test_is_enabled_without_key(self):
        from base_engine.exchanges.coinbase_adapter import CoinbaseAdapter
        adapter = CoinbaseAdapter()
        assert not adapter.is_enabled()

    @pytest.mark.asyncio
    async def test_get_positions_returns_list(self):
        from base_engine.exchanges.coinbase_adapter import CoinbaseAdapter
        adapter = CoinbaseAdapter()
        result = await adapter.get_positions()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_orderbook_returns_none_without_client(self):
        from base_engine.exchanges.coinbase_adapter import CoinbaseAdapter
        adapter = CoinbaseAdapter()
        result = await adapter.get_orderbook("test-market")
        assert result is None


# ── ForecastEx Adapter ───────────────────────────────────────────────────────

class TestForecastExAdapter:
    def test_fee_schedule(self):
        from base_engine.exchanges.forecastex_adapter import ForecastExAdapter
        adapter = ForecastExAdapter()
        fees = adapter.fee_schedule()
        assert fees.platform == "forecastex"
        assert fees.taker_fee == 0.0
        assert fees.total_round_trip() == 0.0

    def test_is_enabled_without_connection(self):
        from base_engine.exchanges.forecastex_adapter import ForecastExAdapter
        adapter = ForecastExAdapter()
        assert not adapter.is_enabled()

    @pytest.mark.asyncio
    async def test_place_order_fails_without_connection(self):
        from base_engine.exchanges.forecastex_adapter import ForecastExAdapter
        adapter = ForecastExAdapter()
        result = await adapter.place_order("123", "BUY", 1.0, 0.5)
        assert not result.success
        assert "not connected" in result.error.lower()


# ── Chain Provider ───────────────────────────────────────────────────────────

class TestChainProvider:
    @pytest.mark.asyncio
    async def test_polygon_send_transaction_with_client(self):
        from base_engine.chain.chain_provider import PolygonProvider
        mock_client = AsyncMock()
        mock_client.send_transaction = AsyncMock(return_value="0xdeadbeef")
        provider = PolygonProvider(blockchain_client=mock_client)
        tx_hash = await provider.send_transaction("0xabc", b"\x00", 0)
        assert tx_hash == "0xdeadbeef"

    @pytest.mark.asyncio
    async def test_polygon_send_transaction_without_client_raises(self):
        from base_engine.chain.chain_provider import PolygonProvider
        provider = PolygonProvider()
        with pytest.raises(NotImplementedError, match="blockchain_client"):
            await provider.send_transaction("0xabc", b"\x00", 0)

    @pytest.mark.asyncio
    async def test_polyl2_send_transaction_raises(self):
        from base_engine.chain.chain_provider import PolyL2Provider
        provider = PolyL2Provider()
        with pytest.raises(NotImplementedError, match="not yet available"):
            await provider.send_transaction("0xabc", b"\x00", 0)


# ── Shared Models ────────────────────────────────────────────────────────────

class TestSharedModels:
    def test_market_snapshot_validity(self):
        valid = MarketSnapshot(market_id="m1", platform="test", question="Test?", yes_price=0.5)
        assert valid.is_valid

        invalid = MarketSnapshot(market_id="", platform="test", question="Test?", yes_price=0.5)
        assert not invalid.is_valid

    def test_fee_schedule_round_trip(self):
        fees = FeeSchedule(taker_fee=0.015, maker_fee=0.0, settlement_fee=0.002, platform="test")
        assert abs(fees.total_round_trip() - 0.032) < 1e-9

    def test_order_book_properties(self):
        from base_engine.exchanges.models import OrderBookLevel
        ob = OrderBook(
            market_id="m1",
            platform="test",
            bids=[OrderBookLevel(0.60, 100)],
            asks=[OrderBookLevel(0.62, 50)],
        )
        assert ob.best_bid == 0.60
        assert ob.best_ask == 0.62
        assert ob.mid_price == 0.61
        assert abs(ob.spread - 0.02) < 1e-9
