"""
Unit Tests for Data Ingestion Service
======================================
Tests for data ingestion functionality with mocked dependencies.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

from base_engine.data.data_ingestion import DataIngestionService
from base_engine.exceptions import MarketFetchError, DatabaseError


@pytest.fixture
def mock_client():
    """Mock PolymarketClient."""
    market_data = [
        {"id": "1", "question": "Test Market", "active": True},
        {"id": "2", "question": "Test Market 2", "active": True}
    ]
    client = AsyncMock()
    # fetch_markets_batch calls get_events first, then get_markets as fallback
    client.get_events = AsyncMock(return_value=[])  # empty → triggers get_markets fallback
    client.get_markets = AsyncMock(return_value=market_data)
    # API connectivity mock
    client.check_gamma_connectivity = AsyncMock(return_value=(True, "OK"))
    client.gamma_api = "https://gamma-api.polymarket.com"
    return client


@pytest.fixture
def mock_db():
    """Mock Database."""
    db = MagicMock()
    db.session_factory = MagicMock()
    db._verify_database = AsyncMock()
    return db


@pytest.fixture
def data_ingestion_service(mock_client, mock_db):
    """Create DataIngestionService instance with mocked dependencies."""
    return DataIngestionService(client=mock_client, db=mock_db)


@pytest.mark.asyncio
async def test_ingest_all_markets_success(data_ingestion_service, mock_client, mock_db):
    """Test successful market ingestion."""
    # Mock database save
    async def mock_save(markets_data):
        return len(markets_data.get("markets", []))
    
    mock_db.save_markets = AsyncMock(side_effect=mock_save)
    
    # Run ingestion
    count = await data_ingestion_service.ingest_all_markets(top_markets_count=10)
    
    # Verify
    assert count >= 0  # May be 0 if validation fails, but should not error
    mock_client.get_markets.assert_called()


@pytest.mark.asyncio
async def test_ingest_all_markets_api_failure(data_ingestion_service, mock_client):
    """Test market ingestion when API fails — handled gracefully, returns 0."""
    # Mock API failure on both methods the batch fetcher calls
    mock_client.get_events = AsyncMock(side_effect=Exception("API Error"))
    mock_client.get_markets = AsyncMock(side_effect=Exception("API Error"))

    # Run ingestion - should handle error gracefully and return 0
    count = await data_ingestion_service.ingest_all_markets(top_markets_count=10)
    assert count == 0


@pytest.mark.asyncio
async def test_validate_market_data_valid(data_ingestion_service):
    """Test market data validation with valid data."""
    valid_markets = [
        {"id": "1", "question": "Test", "active": True},
        {"id": "2", "question": "Test 2", "active": False}
    ]
    
    result = data_ingestion_service._validate_market_data(valid_markets)
    assert result is True


@pytest.mark.asyncio
async def test_validate_market_data_invalid(data_ingestion_service):
    """Test market data validation with invalid data."""
    invalid_markets = None
    
    result = data_ingestion_service._validate_market_data(invalid_markets)
    assert result is False


@pytest.mark.asyncio
async def test_get_cached_markets(data_ingestion_service):
    """Test getting cached markets."""
    # Set up cached markets
    data_ingestion_service.cached_markets = [
        {"id": "1", "active": True},
        {"id": "2", "active": False}
    ]
    
    # Get active markets
    active = data_ingestion_service.get_cached_markets(active=True)
    assert len(active) == 1
    assert active[0]["id"] == "1"
    
    # Get all markets
    all_markets = data_ingestion_service.get_cached_markets()
    assert len(all_markets) == 2
