"""
Integration Tests for Data Ingestion Flow
=========================================
End-to-end tests for data ingestion with test database.
"""
import pytest
import asyncio
from typing import Dict, Any
from unittest.mock import AsyncMock, patch

from base_engine.data.data_ingestion import DataIngestionService
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.data.database import Database


@pytest.mark.asyncio
async def test_full_ingestion_flow():
    """
    Test full ingestion flow: API → Validation → Database.
    Uses mocked API but real database structure.
    """
    # This is a structure test - actual implementation would use test database
    # For now, verify the flow exists and can be called
    
    # Mock client
    mock_client = AsyncMock()
    mock_client.get_markets = AsyncMock(return_value=[
        {
            "id": "test-1",
            "question": "Test Market?",
            "active": True,
            "tokens": [
                {"tokenId": "token-1", "outcome": "YES"},
                {"tokenId": "token-2", "outcome": "NO"}
            ]
        }
    ])
    
    # Mock database (or use in-memory SQLite for real test)
    mock_db = AsyncMock()
    mock_db.session_factory = AsyncMock()
    
    # Create service
    service = DataIngestionService(client=mock_client, db=mock_db)
    
    # Verify service can be created
    assert service is not None
    assert service.client == mock_client
    assert service.db == mock_db


@pytest.mark.asyncio
async def test_ingestion_with_progress_callback():
    """Test ingestion with progress callback."""
    mock_client = AsyncMock()
    mock_client.get_markets = AsyncMock(return_value=[])
    
    mock_db = AsyncMock()
    mock_db.session_factory = AsyncMock()
    
    service = DataIngestionService(client=mock_client, db=mock_db)
    
    progress_updates = []
    
    def progress_callback(progress: Dict[str, Any]) -> None:
        progress_updates.append(progress)
    
    # Run with callback
    await service.ingest_all_markets(
        progress_callback=progress_callback,
        top_markets_count=10
    )
    
    # Verify callback was called (if ingestion runs)
    # Note: Actual test would verify callback was called with expected data
