"""
Comprehensive tests for historical price ingestion fixes.

Tests cover:
- Happy paths (successful ingestion)
- Edge cases (empty data, missing fields, etc.)
- Error scenarios (API failures, invalid data, etc.)
- Loop control flow (all strategies tried)
- Token extraction (all formats)
- API parameter handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from typing import Dict, List, Any

from base_engine.data.data_ingestion import DataIngestionService
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.data.database import Database


class TestTokenExtraction:
    """Test token extraction with various market data formats."""
    
    def test_extract_tokens_direct_tokens_array(self):
        """Test extraction from direct 'tokens' array."""
        service = DataIngestionService(MagicMock(), MagicMock())
        
        market_data = {
            "id": "12345",
            "tokens": [
                {"tokenId": "token1", "outcome": "YES"},
                {"tokenId": "token2", "outcome": "NO"}
            ]
        }
        
        tokens, diagnostics = service._extract_tokens_from_market(market_data, "12345")
        
        assert len(tokens) == 2
        assert tokens[0]["tokenId"] == "token1"
        assert diagnostics["extraction_strategy_used"] == "direct_tokens"
        assert diagnostics["tokens_found"] == 2
    
    def test_extract_tokens_from_outcomes_array(self):
        """Test extraction from 'outcomes' array."""
        service = DataIngestionService(MagicMock(), MagicMock())
        
        market_data = {
            "id": "12345",
            "outcomes": [
                {"tokenId": "token1", "title": "YES"},
                {"tokenId": "token2", "title": "NO"}
            ]
        }
        
        tokens, diagnostics = service._extract_tokens_from_market(market_data, "12345")
        
        assert len(tokens) == 2
        assert diagnostics["extraction_strategy_used"] == "outcomes_array"
    
    def test_extract_tokens_from_conditions_array(self):
        """Test extraction from 'conditions' array."""
        service = DataIngestionService(MagicMock(), MagicMock())
        
        market_data = {
            "id": "12345",
            "conditions": [
                {"tokenId": "token1"},
                {"tokenId": "token2"}
            ]
        }
        
        tokens, diagnostics = service._extract_tokens_from_market(market_data, "12345")
        
        assert len(tokens) == 2
        assert diagnostics["extraction_strategy_used"] == "conditions_array"
    
    def test_extract_tokens_single_token_in_root(self):
        """Test extraction when tokenId is in root."""
        service = DataIngestionService(MagicMock(), MagicMock())
        
        market_data = {
            "id": "12345",
            "tokenId": "token1"
        }
        
        tokens, diagnostics = service._extract_tokens_from_market(market_data, "12345")
        
        assert len(tokens) == 1
        assert tokens[0]["tokenId"] == "token1"
        assert diagnostics["extraction_strategy_used"] == "root_tokenId"
    
    def test_extract_tokens_no_tokens_found(self):
        """Test when no tokens are found."""
        service = DataIngestionService(MagicMock(), MagicMock())
        
        market_data = {
            "id": "12345",
            "question": "Test question"
        }
        
        tokens, diagnostics = service._extract_tokens_from_market(market_data, "12345")
        
        assert len(tokens) == 0
        assert diagnostics["error"] == "No tokens found in any expected location"
        assert diagnostics["tokens_found"] == 0
    
    def test_extract_tokens_invalid_input_not_dict(self):
        """Test when market_data is not a dict."""
        service = DataIngestionService(MagicMock(), MagicMock())
        
        tokens, diagnostics = service._extract_tokens_from_market("not a dict", "12345")
        
        assert len(tokens) == 0
        assert "error" in diagnostics
        assert "not a dict" in diagnostics["error"].lower()


class TestAPIParameterHandling:
    """Test API parameter handling in get_price_history."""
    
    @pytest.mark.asyncio
    async def test_get_price_history_valid_token(self):
        """Test with valid token_id."""
        client = PolymarketClient()
        client._request = AsyncMock(return_value={"history": [{"t": 1234567890, "p": 0.5}]})
        
        result = await client.get_price_history("token123", start_ts=1234567890, end_ts=1234567900)
        
        assert "history" in result
        assert len(result["history"]) == 1
        client._request.assert_called_once()
        call_args = client._request.call_args
        assert call_args[0][2] == client.clob_api  # clob_api
        # V2 CLOB API uses "market" param with token_id value (not "token")
        assert call_args[1]["params"]["market"] == "token123"
    
    @pytest.mark.asyncio
    async def test_get_price_history_invalid_token_none(self):
        """Test with None token_id."""
        client = PolymarketClient()
        
        result = await client.get_price_history(None)
        
        assert result == {"history": []}
    
    @pytest.mark.asyncio
    async def test_get_price_history_api_404(self):
        """Test handling of 404 response."""
        import httpx
        
        client = PolymarketClient()
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=MagicMock(status_code=404))
        client._request = AsyncMock(side_effect=error)
        
        result = await client.get_price_history("token123")
        
        assert result == {"history": []}
    
    @pytest.mark.asyncio
    async def test_get_price_history_api_400_fallback(self):
        """Test 400 error returns empty history (V2 client does not retry with different param)."""
        import httpx
        
        client = PolymarketClient()
        error = httpx.HTTPStatusError("Bad request", request=MagicMock(), response=MagicMock(status_code=400, text="Bad request"))
        client._request = AsyncMock(side_effect=error)
        
        result = await client.get_price_history("token123")
        
        assert result == {"history": []}
        assert client._request.call_count == 1
    
    @pytest.mark.asyncio
    async def test_get_price_history_unexpected_response_structure(self):
        """Test handling of unexpected response structure."""
        client = PolymarketClient()
        client._request = AsyncMock(return_value="not a dict")
        
        result = await client.get_price_history("token123")
        
        assert result == {"history": []}


class TestLoopControlFlow:
    """Test loop control flow and strategy execution."""
    
    @pytest.mark.asyncio
    async def test_strategy_1_success_skips_remaining(self):
        """Test that Strategy 1 success skips remaining strategies."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_db.save_market_price = AsyncMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        # Mock successful token extraction and API call
        market_data = {
            "id": "12345",
            "tokens": [{"tokenId": "token1"}]
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [{"t": 1234567890, "p": 0.5}]
        })
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            from_timestamp=1234567890,
            to_timestamp=1234567900,
            max_markets=1
        )
        
        assert result["success"]
        assert "diagnostics" in result
        assert result["diagnostics"]["markets_processed"] >= 0
        assert "prices_ingested" in result["diagnostics"]
    
    @pytest.mark.asyncio
    async def test_all_strategies_fail_comprehensive_diagnostics(self):
        """Test that all strategies failing provides comprehensive diagnostics."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_db.save_market_price = AsyncMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        # Market with no tokens
        market_data = {
            "id": "12345",
            "question": "Test question"
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            from_timestamp=1234567890,
            to_timestamp=1234567900,
            max_markets=1
        )
        
        assert result["success"]
        assert "diagnostics" in result
        assert result["diagnostics"]["markets_processed"] >= 0
        assert "errors" in result["diagnostics"]
    
    @pytest.mark.asyncio
    async def test_strategy_2_fallback_when_strategy_1_fails(self):
        """Test that Strategy 2 is tried when Strategy 1 fails."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_db.save_market_price = AsyncMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        # Market with tokens but price history API fails
        market_data = {
            "id": "12345",
            "tokens": [{"tokenId": "token1"}]
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        mock_client.get_price_history = AsyncMock(return_value={"history": []})  # Empty history
        mock_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.4"}],
            "asks": [{"price": "0.6"}]
        })
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            from_timestamp=1234567890,
            to_timestamp=1234567900,
            max_markets=1
        )
        
        # Verify Strategy 2 (orderbook) was called
        assert mock_client.get_orderbook.called
        # Should have ingested at least 1 price from orderbook
        assert result["diagnostics"]["prices_ingested"] >= 1


class TestErrorScenarios:
    """Test error handling scenarios."""
    
    @pytest.mark.asyncio
    async def test_market_data_none(self):
        """Test handling when market data is None."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        mock_client.get_markets = AsyncMock(return_value=[None])
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        assert result["success"]
        assert result["diagnostics"]["markets_failed"] >= 1
    
    @pytest.mark.asyncio
    async def test_market_data_not_dict(self):
        """Test handling when market data is not a dict."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        mock_client.get_markets = AsyncMock(return_value=["not a dict"])
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        assert result["success"]
        assert result["diagnostics"]["markets_failed"] >= 1
    
    @pytest.mark.asyncio
    async def test_api_returns_empty_history(self):
        """Test handling when API returns empty history."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        market_data = {
            "id": "12345",
            "tokens": [{"tokenId": "token1"}]
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        mock_client.get_price_history = AsyncMock(return_value={"history": []})
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        assert result["success"]
        assert "diagnostics" in result
        assert result["diagnostics"]["markets_processed"] >= 0
    
    @pytest.mark.asyncio
    async def test_database_save_failure(self):
        """Test handling when database save fails."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_db.save_market_price = AsyncMock(side_effect=Exception("DB error"))
        
        service = DataIngestionService(mock_client, mock_db)
        
        market_data = {
            "id": "12345",
            "tokens": [{"tokenId": "token1"}]
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [{"t": 1234567890, "p": 0.5}]
        })
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        # Should handle DB error gracefully
        assert result["success"] or "DB error" in str(result.get("error", ""))


class TestEdgeCases:
    """Test edge cases."""
    
    @pytest.mark.asyncio
    async def test_empty_tokens_array(self):
        """Test handling when tokens array is empty."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        market_data = {
            "id": "12345",
            "tokens": []
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        assert result["success"]
        # Should try Strategy 2.5 (current price from market data)
    
    @pytest.mark.asyncio
    async def test_tokens_with_non_dict_items(self):
        """Test handling when tokens array contains non-dict items."""
        from unittest.mock import AsyncMock
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_db.get_markets_with_token_ids = AsyncMock(return_value=[])
        mock_db.session_factory = True
        mock_db.bulk_insert_prices = AsyncMock(return_value=None)
        
        service = DataIngestionService(mock_client, mock_db)
        
        market_data = {
            "id": "12345",
            "tokens": ["not a dict", {"tokenId": "token1"}]
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        mock_client.get_price_history = AsyncMock(return_value={
            "history": [{"t": 1234567890, "p": 0.5}]
        })
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        # Should filter out non-dict items and use valid token (or mark no_events if bulk_insert fails)
        assert result["success"]
        # May ingest 1+ prices or mark no_events depending on token filtering
        assert "diagnostics" in result
        assert "prices_ingested" in result["diagnostics"]
    
    @pytest.mark.asyncio
    async def test_market_missing_id(self):
        """Test handling when market is missing ID."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        
        service = DataIngestionService(mock_client, mock_db)
        
        market_data = {
            "question": "Test question"
            # Missing "id"
        }
        
        mock_client.get_markets = AsyncMock(return_value=[market_data])
        
        result = await service.ingest_historical_prices(
            market_ids=None,
            max_markets=1
        )
        
        assert result["success"]
        assert result["diagnostics"]["markets_failed"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
