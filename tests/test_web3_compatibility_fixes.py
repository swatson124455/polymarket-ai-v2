"""
Comprehensive tests for Web3.py compatibility fixes and TheGraph query fixes.

Tests cover:
1. Web3.py v6+ is_connected() replacement
2. TheGraph query format fallbacks
3. Event loop handling
4. Error scenarios and edge cases
"""
import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from base_engine.data.blockchain_client import BlockchainClient
from base_engine.execution.contract_manager import ContractManager

# Provide a dummy API key so TheGraphClient() doesn't raise ValueError during tests
_THEGRAPH_ENV = {"THE_GRAPH_API_KEY": "test-dummy-key-for-unit-tests"}


def _make_thegraph_client():
    """Create a TheGraphClient with a dummy API key for testing."""
    with patch.dict(os.environ, _THEGRAPH_ENV):
        from base_engine.data.thegraph_client import TheGraphClient
        return TheGraphClient()


class TestBlockchainClientWeb3Compatibility:
    """Test Web3.py v6+ compatibility fixes for BlockchainClient."""
    
    @pytest.mark.asyncio
    async def test_ensure_client_uses_get_block_instead_of_is_connected(self):
        """Test that ensure_client() uses get_block('latest') instead of is_connected()."""
        client = BlockchainClient(rpc_url="https://polygon-rpc.com")
        
        # Mock AsyncWeb3
        mock_w3 = AsyncMock()
        mock_w3.eth.get_block = AsyncMock(return_value={"number": 100, "timestamp": 1000})
        mock_w3.middleware_onion = MagicMock()
        
        with patch('base_engine.data.blockchain_client.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            
            # Should not raise AttributeError about is_connected
            await client.ensure_client()
            
            # Verify get_block was called (not is_connected)
            mock_w3.eth.get_block.assert_called_once_with('latest')
            assert client.w3 is not None
    
    @pytest.mark.asyncio
    async def test_ensure_client_handles_connection_failure(self):
        """Test that ensure_client() properly handles connection failures."""
        client = BlockchainClient(rpc_url="https://invalid-rpc.com")
        
        # Mock AsyncWeb3 to raise connection error
        mock_w3 = AsyncMock()
        mock_w3.eth.get_block = AsyncMock(side_effect=Exception("Connection failed"))
        mock_w3.middleware_onion = MagicMock()
        
        with patch('base_engine.data.blockchain_client.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            
            # Should raise ConnectionError with proper message
            with pytest.raises(ConnectionError) as exc_info:
                await client.ensure_client()
            
            assert "Failed to connect" in str(exc_info.value)
            assert client.w3 is None
    
    @pytest.mark.asyncio
    async def test_ensure_client_handles_web3_v6_plus(self):
        """Test that ensure_client() works with Web3.py v6+ (no is_connected method)."""
        client = BlockchainClient()
        
        # Simulate Web3.py v6+ (no is_connected method)
        mock_w3 = AsyncMock()
        # Remove is_connected if it exists (simulating v6+)
        if hasattr(mock_w3.eth, 'is_connected'):
            delattr(mock_w3.eth, 'is_connected')
        mock_w3.eth.get_block = AsyncMock(return_value={"number": 100, "timestamp": 1000})
        mock_w3.middleware_onion = MagicMock()
        
        with patch('base_engine.data.blockchain_client.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            
            # Should work without is_connected
            await client.ensure_client()
            assert client.w3 is not None


class TestContractManagerWeb3Compatibility:
    """Test Web3.py v6+ compatibility fixes for ContractManager."""
    
    @pytest.mark.asyncio
    async def test_ensure_client_uses_get_block_instead_of_is_connected(self):
        """Test that ContractManager.ensure_client() uses get_block('latest')."""
        manager = ContractManager(rpc_url="https://polygon-rpc.com")
        
        mock_w3 = AsyncMock()
        mock_w3.eth.get_block = AsyncMock(return_value={"number": 100, "timestamp": 1000})
        
        with patch('base_engine.execution.contract_manager.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            
            await manager.ensure_client()
            
            # Verify get_block was called (not is_connected)
            mock_w3.eth.get_block.assert_called_once_with('latest')
            assert manager.w3 is not None
    
    @pytest.mark.asyncio
    async def test_ensure_client_handles_connection_failure(self):
        """Test ContractManager connection failure handling."""
        manager = ContractManager(rpc_url="https://invalid-rpc.com")
        
        mock_w3 = AsyncMock()
        mock_w3.eth.get_block = AsyncMock(side_effect=Exception("Connection failed"))
        
        with patch('base_engine.execution.contract_manager.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            
            with pytest.raises(ConnectionError) as exc_info:
                await manager.ensure_client()
            
            assert "Failed to connect" in str(exc_info.value)
            assert manager.w3 is None


class TestTheGraphQueryFormats:
    """Test TheGraph query format fallbacks."""
    
    @pytest.mark.asyncio
    async def test_get_market_by_condition_id_tries_multiple_formats(self):
        """Test that get_market_by_condition_id tries multiple query formats."""
        client = _make_thegraph_client()
        condition_id = "0x1234567890abcdef"
        # Valid 40-hex-char address for the mock
        valid_address = "0x" + "a1b2c3d4e5f6a7b8c9d0" * 2

        # Mock _query_graphql to return different results for each format
        call_count = 0

        async def mock_query(query, variables):
            nonlocal call_count
            call_count += 1

            # Format 1 fails (conditions_contains not supported)
            if call_count == 1:
                return {"errors": [{"message": "Invalid value for conditions_contains"}]}

            # Format 2 succeeds with a valid 40-hex-char address
            if call_count == 2:
                return {
                    "fixedProductMarketMakers": [
                        {"id": valid_address}
                    ]
                }

            # Formats 3+ not reached
            return {}

        client._query_graphql = mock_query

        result = await client.get_market_by_condition_id(condition_id)

        # Should have tried at least 2 formats
        assert call_count >= 2
        # Should return result from format 2
        assert result is not None
        assert result["id"] == valid_address
        assert result["conditionId"] == condition_id
    
    @pytest.mark.asyncio
    async def test_get_market_by_condition_id_all_formats_fail(self):
        """Test that get_market_by_condition_id returns None when all formats fail."""
        client = _make_thegraph_client()
        condition_id = "0x1234567890abcdef"
        
        # Mock _query_graphql to always fail
        async def mock_query(query, variables):
            return {"errors": [{"message": "Query failed"}]}
        
        client._query_graphql = mock_query
        
        result = await client.get_market_by_condition_id(condition_id)
        
        # Should return None after trying all formats
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_market_by_condition_id_handles_empty_results(self):
        """Test that get_market_by_condition_id handles empty results gracefully."""
        client = _make_thegraph_client()
        condition_id = "0x1234567890abcdef"
        
        # Mock _query_graphql to return empty results
        async def mock_query(query, variables):
            return {"fixedProductMarketMakers": []}
        
        client._query_graphql = mock_query
        
        result = await client.get_market_by_condition_id(condition_id)
        
        # Should return None for empty results
        assert result is None


class TestErrorScenarios:
    """Test error scenarios and edge cases."""
    
    @pytest.mark.asyncio
    async def test_blockchain_client_handles_rpc_timeout(self):
        """Test that BlockchainClient handles RPC timeout gracefully."""
        client = BlockchainClient(rpc_url="https://polygon-rpc.com")
        
        mock_w3 = AsyncMock()
        mock_w3.eth.get_block = AsyncMock(side_effect=asyncio.TimeoutError("RPC timeout"))
        mock_w3.middleware_onion = MagicMock()
        
        with patch('base_engine.data.blockchain_client.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            
            with pytest.raises(ConnectionError):
                await client.ensure_client()
    
    @pytest.mark.asyncio
    async def test_thegraph_client_handles_rate_limit(self):
        """Test that TheGraphClient handles rate limiting."""
        client = _make_thegraph_client()
        
        # Mock rate limit response
        async def mock_query(query, variables):
            return {}  # Empty response (rate limited)
        
        client._query_graphql = mock_query
        
        result = await client.get_market_by_condition_id("0x123")
        
        # Should return None when rate limited
        assert result is None
    
    @pytest.mark.asyncio
    async def test_contract_manager_handles_invalid_address(self):
        """Test that ContractManager handles invalid addresses."""
        manager = ContractManager()
        
        # Should return error for invalid address
        result = await manager.check_allowance(
            token_address="invalid",
            owner_address="0x123",
            spender_address="0x456"
        )
        
        assert result["success"] is False
        assert "Invalid" in result["error"]


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple components."""
    
    @pytest.mark.asyncio
    async def test_blockchain_ingestion_with_web3_fix(self):
        """Test that blockchain ingestion works with Web3.py v6+ fixes."""
        client = BlockchainClient()
        
        # Mock successful connection
        mock_w3 = AsyncMock()
        mock_w3.eth.get_block = AsyncMock(return_value={"number": 100, "timestamp": 1000})
        mock_w3.eth.get_block_number = AsyncMock(return_value=100)
        mock_w3.middleware_onion = MagicMock()
        
        # Mock contract events
        mock_contract = AsyncMock()
        mock_contract.events.Trade.get_logs = AsyncMock(return_value=[])
        mock_w3.eth.contract = MagicMock(return_value=mock_contract)
        
        with patch('base_engine.data.blockchain_client.AsyncWeb3') as mock_async_web3:
            mock_async_web3.return_value = mock_w3
            mock_async_web3.AsyncHTTPProvider = MagicMock()
            mock_async_web3.to_checksum_address = lambda x: x
            
            # Should work without is_connected
            await client.ensure_client()
            
            # Should be able to query events (use valid 40-hex-char address)
            events = await client.query_fpmm_trade_events(
                fpmm_contract_address="0x" + "a1b2c3d4e5" * 4,
                from_block=1,
                to_block=100
            )
            
            assert isinstance(events, list)
    
    @pytest.mark.asyncio
    async def test_thegraph_fallback_chain(self):
        """Test that TheGraph client properly falls back through query formats."""
        client = _make_thegraph_client()
        condition_id = "0x1234567890abcdef"
        # Valid 40-hex-char address for the mock
        valid_address = "0x" + "a1b2c3d4e5f6a7b8c9d0" * 2

        query_formats_tried = []

        async def mock_query(query, variables):
            query_formats_tried.append(query[:50])  # Store first 50 chars

            # All formats fail except the last one
            if len(query_formats_tried) < 4:
                return {"errors": [{"message": "query format not supported"}]}

            # Final format succeeds with valid address
            return {
                "fixedProductMarketMakers": [
                    {"id": valid_address}
                ]
            }

        client._query_graphql = mock_query

        result = await client.get_market_by_condition_id(condition_id)

        # Should have tried all 4 formats
        assert len(query_formats_tried) == 4
        # Should return result from the final format
        assert result is not None
        assert result["id"] == valid_address


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
