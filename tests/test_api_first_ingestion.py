"""
Comprehensive tests for API-first price ingestion approach.

Tests cover:
1. Polymarket Price History API (Strategy 1) - Happy paths and edge cases
2. Orderbook API fallback (Strategy 2)
3. Blockchain Exchange events fallback (Strategy 3)
4. FPMM contract queries fallback (Strategy 4)
5. Error scenarios and edge cases
6. Multi-outcome market handling (token IDs vs condition IDs)

"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from datetime import datetime, timezone
from base_engine.data.data_ingestion import DataIngestionService


class TestAPIFirstPriceIngestion:
    """Test the API-first price ingestion approach."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock PolymarketClient."""
        client = AsyncMock()
        return client
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock Database."""
        db = AsyncMock()
        return db
    
    @pytest.fixture
    def mock_blockchain_client(self):
        """Create a mock BlockchainClient."""
        blockchain = AsyncMock()
        return blockchain
    
    @pytest.fixture
    def mock_thegraph_client(self):
        """Create a mock TheGraphClient."""
        thegraph = AsyncMock()
        return thegraph
    
    @pytest.fixture
    def ingestion_service(self, mock_client, mock_db, mock_blockchain_client, mock_thegraph_client):
        """Create DataIngestionService with mocked dependencies."""
        service = DataIngestionService(
            client=mock_client,
            db=mock_db,
            blockchain_client=mock_blockchain_client,
            thegraph_client=mock_thegraph_client
        )
        return service
    
    @pytest.fixture
    def sample_market_with_tokens(self):
        """Sample market data with token IDs."""
        return {
            "id": "market_123",
            "conditionId": "0xa69729ae3d9838ec5754e0f74bf57dedd5ddbecd9e31b15a04f48f081168ba00",
            "tokens": [
                {
                    "tokenId": "1234567890123456789012345678901234567890",
                    "outcome": "YES"
                },
                {
                    "tokenId": "0987654321098765432109876543210987654321",
                    "outcome": "NO"
                }
            ]
        }
    
    @pytest.mark.asyncio
    async def test_strategy_1_api_success_happy_path(self, ingestion_service, sample_market_with_tokens, mock_client, mock_db):
        """Test Strategy 1: Polymarket API returns price history successfully."""
        # Force API path: no DB token IDs so we use market dicts + bulk_insert_prices
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # Mock API response
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [
                {"t": 1609459200, "p": 0.65},  # 2021-01-01 00:00:00 UTC
                {"t": 1609462800, "p": 0.67},  # 2021-01-01 01:00:00 UTC
                {"t": 1609466400, "p": 0.69},  # 2021-01-01 02:00:00 UTC
            ]
        })
        
        # Mock get_market so we get a real dict (code calls client.get_market(market_id))
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        # _extract_tokens_from_market returns (tokens, token_diagnostics)
        ingestion_service._extract_tokens_from_market = Mock(return_value=(sample_market_with_tokens["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1234567890123456789012345678901234567890")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        # Strategy 1 uses bulk_insert_prices_raw; mock must return int for diagnostics
        mock_db.bulk_insert_prices_raw = AsyncMock(return_value=6)
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Verify success (both YES and NO tokens × 3 points = 6 prices)
        assert result["success"] is True
        assert result["diagnostics"]["markets_successful"] == 1
        assert result["diagnostics"]["prices_ingested"] == 6
        
        # Verify API was called for each token (YES and NO)
        assert mock_client.get_price_history.call_count == 2
        
        # Verify prices were saved via bulk insert (Strategy 1 uses bulk_insert_prices_raw)
        assert mock_db.bulk_insert_prices_raw.called
        call_arg = mock_db.bulk_insert_prices_raw.call_args[0][0]
        assert len(call_arg) == 6
    
    @pytest.mark.asyncio
    async def test_strategy_1_api_empty_history(self, ingestion_service, sample_market_with_tokens, mock_client, mock_db):
        """Test Strategy 1: API returns empty history, should fallback to Strategy 2."""
        # Mock API returns empty history
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        # Mock orderbook fallback succeeds (need both bids and asks for midpoint)
        mock_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.65"}],
            "asks": [{"price": "0.67"}]
        })
        
        ingestion_service._extract_tokens_from_market = Mock(return_value=(sample_market_with_tokens["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1234567890123456789012345678901234567890")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should fallback to orderbook
        assert mock_client.get_orderbook.called
        assert result["diagnostics"]["prices_ingested"] == 1  # One price from orderbook
    
    @pytest.mark.asyncio
    async def test_strategy_1_api_no_tokens(self, ingestion_service, mock_client, mock_db):
        """Test Strategy 1: Market has no tokens, should skip API and try orderbook."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        market_no_tokens = {
            "id": "market_456",
            "conditionId": "0x123",
            "tokens": []
        }
        
        # Mock get_market to return market without tokens
        mock_client.get_market = AsyncMock(return_value=market_no_tokens)
        # Return (tokens, diagnostics); empty tokens
        ingestion_service._extract_tokens_from_market = Mock(return_value=([], {}))
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_456"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # API should not be called (no tokens)
        mock_client.get_price_history.assert_not_called()
        # Should mark as no events
        assert result["diagnostics"]["markets_no_events"] >= 1
    
    @pytest.mark.asyncio
    async def test_strategy_1_api_invalid_price_data(self, ingestion_service, sample_market_with_tokens, mock_client, mock_db):
        """Test Strategy 1: API returns invalid price data, should skip invalid entries."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # Mock API returns mix of valid and invalid data
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [
                {"t": 1609459200, "p": 0.65},  # Valid
                {"t": None, "p": 0.67},  # Invalid timestamp
                {"t": 1609462800, "p": None},  # Invalid price
                {"t": 1609466400, "p": 0.69},  # Valid
            ]
        })
        
        ingestion_service._extract_tokens_from_market = Mock(return_value=(sample_market_with_tokens["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1234567890123456789012345678901234567890")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        mock_db.bulk_insert_prices_raw = AsyncMock(return_value=4)
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should save 4 valid prices (2 valid per token × 2 tokens YES/NO; Strategy 1 uses bulk_insert_prices_raw)
        assert result["diagnostics"]["prices_ingested"] == 4
        assert mock_db.bulk_insert_prices_raw.called and len(mock_db.bulk_insert_prices_raw.call_args[0][0]) == 4
    
    @pytest.mark.asyncio
    async def test_strategy_2_orderbook_fallback(self, ingestion_service, sample_market_with_tokens, mock_client, mock_db):
        """Test Strategy 2: Orderbook API fallback when price history API fails."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # Strategy 1 fails
        mock_client.get_price_history = AsyncMock(side_effect=Exception("API Error"))
        
        # Strategy 2 succeeds
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        mock_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.64"}],
            "asks": [{"price": "0.66"}]
        })
        
        ingestion_service._extract_tokens_from_market = Mock(return_value=(sample_market_with_tokens["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1234567890123456789012345678901234567890")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should use orderbook
        assert mock_client.get_orderbook.called
        assert result["diagnostics"]["prices_ingested"] == 1
        
        # Verify midpoint calculation (0.64 + 0.66) / 2 = 0.65 (Strategy 2 uses save_market_price)
        assert mock_db.save_market_price.called
        call_args = mock_db.save_market_price.call_args
        assert call_args[1]["price"] == 0.65
    
    @pytest.mark.asyncio
    async def test_strategy_2_orderbook_only_bids(self, ingestion_service, sample_market_with_tokens, mock_client, mock_db):
        """Test Strategy 2: Orderbook with only bids (no asks) - implementation requires both for midpoint."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        mock_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.65"}],
            "asks": []
        })
        
        ingestion_service._extract_tokens_from_market = Mock(return_value=(sample_market_with_tokens["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1234567890123456789012345678901234567890")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Implementation only uses orderbook when both bids and asks exist; with only bids, no price saved
        assert mock_client.get_orderbook.called
        assert result["diagnostics"]["prices_ingested"] == 0
        assert result["diagnostics"]["markets_no_events"] >= 1
    
    @pytest.mark.asyncio
    async def test_strategy_3_blockchain_fallback(self, ingestion_service, sample_market_with_tokens, mock_client, mock_blockchain_client, mock_db):
        """V2 CLOB-only: Strategy 3 (blockchain) is not used; no fallback to exchange events."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        mock_client.get_orderbook = AsyncMock(return_value=None)
        mock_blockchain_client.query_exchange_order_filled_events = AsyncMock(return_value=[])
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        assert not mock_blockchain_client.query_exchange_order_filled_events.called
        assert result["diagnostics"]["prices_ingested"] == 0
    
    @pytest.mark.asyncio
    async def test_strategy_4_fpmm_fallback(self, ingestion_service, sample_market_with_tokens, mock_client, mock_blockchain_client, mock_thegraph_client, mock_db):
        """V2 CLOB-only: Strategy 4 (FPMM) is not used; no fallback to FPMM contract."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        mock_client.get_orderbook = AsyncMock(return_value=None)
        mock_blockchain_client.query_fpmm_trade_events = AsyncMock(return_value=[])
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        assert not mock_blockchain_client.query_fpmm_trade_events.called
        assert result["diagnostics"]["prices_ingested"] == 0
    
    @pytest.mark.asyncio
    async def test_fpmm_address_validation_condition_id_rejected(self, ingestion_service, sample_market_with_tokens, mock_client, mock_blockchain_client, mock_thegraph_client, mock_db):
        """Test that condition IDs (64 hex chars) are rejected as FPMM addresses."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # Strategy 1-2.5 all fail (no tokens)
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        mock_client.get_orderbook = AsyncMock(return_value=None)
        ingestion_service._extract_tokens_from_market = Mock(return_value=([], {}))
        
        # Try to use condition ID as FPMM address (should be rejected)
        condition_id = "0xa69729ae3d9838ec5754e0f74bf57dedd5ddbecd9e31b15a04f48f081168ba00"  # 64 chars
        sample_market_with_tokens["contractAddress"] = condition_id
        
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # V2 CLOB-only: FPMM is never called; all strategies fail -> markets_no_events
        mock_blockchain_client.query_fpmm_trade_events.assert_not_called()
        assert result["diagnostics"]["markets_no_events"] >= 1
    
    @pytest.mark.asyncio
    async def test_multi_outcome_market_token_ids(self, ingestion_service, mock_client, mock_db):
        """Test that multi-outcome markets use token IDs correctly (not condition IDs)."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # Multi-outcome market: all outcomes share same condition ID
        multi_outcome_market = {
            "id": "market_multi",
            "conditionId": "0xa69729ae3d9838ec5754e0f74bf57dedd5ddbecd9e31b15a04f48f081168ba00",
            "tokens": [
                {"tokenId": "1111111111111111111111111111111111111111", "outcome": "YES"},
                {"tokenId": "2222222222222222222222222222222222222222", "outcome": "NO"},
                {"tokenId": "3333333333333333333333333333333333333333", "outcome": "MAYBE"}
            ]
        }
        
        # Mock API returns prices for YES token
        mock_client.get_market = AsyncMock(return_value=multi_outcome_market)
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [
                {"t": 1609459200, "p": 0.65}
            ]
        })
        
        ingestion_service._extract_tokens_from_market = Mock(return_value=(multi_outcome_market["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1111111111111111111111111111111111111111")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_multi"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should use token ID (not condition ID) for API call
        assert mock_client.get_price_history.called
        call_args = mock_client.get_price_history.call_args
        token_id_arg = (call_args[1] or {}).get("token_id") or (call_args[0][0] if call_args[0] else None)
        assert token_id_arg == "1111111111111111111111111111111111111111"
        assert token_id_arg != multi_outcome_market["conditionId"]  # Not condition ID
        assert result["success"] is True
    
    @pytest.mark.asyncio
    async def test_all_strategies_fail(self, ingestion_service, sample_market_with_tokens, mock_client, mock_blockchain_client, mock_thegraph_client, mock_db):
        """Test when all strategies fail - should gracefully handle and report."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # All strategies fail
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        mock_client.get_orderbook = AsyncMock(return_value=None)
        mock_blockchain_client.query_exchange_order_filled_events = AsyncMock(return_value=[])
        ingestion_service._extract_tokens_from_market = Mock(return_value=([], {}))
        mock_thegraph_client.get_fpmm_address_from_polymarket_api = AsyncMock(return_value=None)
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        mock_thegraph_client.get_market_by_condition_id = AsyncMock(return_value=None)
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should complete without crashing
        assert result["success"] is True
        assert result["diagnostics"]["prices_ingested"] == 0
        assert result["diagnostics"]["markets_no_events"] >= 1
    
    @pytest.mark.asyncio
    async def test_network_error_handling(self, ingestion_service, sample_market_with_tokens, mock_client, mock_db):
        """Test that network errors are handled gracefully."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        # Network error on API call
        mock_client.get_market = AsyncMock(return_value=sample_market_with_tokens)
        mock_client.get_price_history = AsyncMock(side_effect=Exception("Network timeout"))
        mock_client.get_orderbook = AsyncMock(side_effect=Exception("Network timeout"))
        
        ingestion_service._extract_tokens_from_market = Mock(return_value=(sample_market_with_tokens["tokens"], {}))
        ingestion_service._extract_token_id = Mock(return_value="1234567890123456789012345678901234567890")
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_123"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should not crash, should fallback or report failure
        assert "success" in result
        # Should have attempted fallbacks
        assert mock_client.get_orderbook.called or result["diagnostics"]["markets_failed"] >= 1
    
    @pytest.mark.asyncio
    async def test_invalid_market_id(self, ingestion_service, mock_client):
        """Test handling of invalid market IDs."""
        mock_client.get_market = AsyncMock(return_value=None)
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["invalid_market"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should handle gracefully
        assert result["success"] is True
        assert result["diagnostics"]["markets_failed"] >= 1 or result["diagnostics"]["markets_processed"] == 0
    
    @pytest.mark.asyncio
    async def test_batch_processing_multiple_markets(self, ingestion_service, mock_client, mock_db):
        """Test processing multiple markets in batch."""
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        markets = [
            {"id": f"market_{i}", "tokens": [{"tokenId": f"{i}" * 40, "outcome": "YES"}]}
            for i in range(1, 4)
        ]
        
        # Mock successful API calls for all markets
        mock_client.get_market = AsyncMock(side_effect=markets)
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [{"t": 1609459200, "p": 0.65}]
        })
        # _extract_tokens_from_market returns (tokens, token_diagnostics)
        ingestion_service._extract_tokens_from_market = Mock(side_effect=[(m["tokens"], {}) for m in markets])
        ingestion_service._extract_token_id = Mock(side_effect=[m["tokens"][0]["tokenId"] for m in markets])
        ingestion_service._get_market_resolution_normalized = AsyncMock(return_value={"resolved": False})
        # Strategy 1 uses bulk_insert_prices_raw; return 1 per market so total prices_ingested == 3
        mock_db.bulk_insert_prices_raw = AsyncMock(return_value=1)
        
        result = await ingestion_service.ingest_historical_prices(
            market_ids=["market_1", "market_2", "market_3"],
            from_timestamp=1609459200,
            to_timestamp=1609466400,
            max_markets=10
        )
        
        # Should process all markets
        assert result["diagnostics"]["markets_processed"] == 3
        assert result["diagnostics"]["markets_successful"] == 3
        assert result["diagnostics"]["prices_ingested"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
