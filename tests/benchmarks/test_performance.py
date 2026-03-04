"""
Performance Benchmarks
======================
Benchmarks for critical operations to track performance over time.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from base_engine.data.data_ingestion import DataIngestionService


@pytest.mark.benchmark
def benchmark_market_validation(benchmark):
    """Benchmark market data validation."""
    service = DataIngestionService(
        client=AsyncMock(),
        db=MagicMock()
    )
    
    markets = [
        {"id": f"market-{i}", "question": f"Question {i}", "active": True}
        for i in range(100)
    ]
    
    result = benchmark(service._validate_market_data, markets)
    assert result is True


@pytest.mark.benchmark
@pytest.mark.asyncio
async def benchmark_market_fetch(benchmark):
    """Benchmark market fetching from API."""
    mock_client = AsyncMock()
    mock_client.get_markets = AsyncMock(return_value=[
        {"id": f"market-{i}", "question": f"Question {i}", "active": True}
        for i in range(100)
    ])
    
    service = DataIngestionService(client=mock_client, db=MagicMock())
    
    # Benchmark the API call (mocked)
    result = await benchmark.pedantic(
        mock_client.get_markets,
        kwargs={"active": True, "limit": 100},
        rounds=10
    )
    
    assert len(result) == 100


@pytest.mark.benchmark
def benchmark_data_parsing(benchmark):
    """Benchmark market data parsing."""
    service = DataIngestionService(
        client=AsyncMock(),
        db=MagicMock()
    )
    
    market_data = {
        "id": "test-1",
        "question": "Test Market?",
        "active": True,
        "tokens": [
            {"tokenId": "token-1", "outcome": "YES", "outcomePrice": "0.65"},
            {"tokenId": "token-2", "outcome": "NO", "outcomePrice": "0.35"}
        ],
        "endDateISO": "2025-12-31T23:59:59Z"
    }
    
    # Benchmark token extraction
    result = benchmark(
        service._extract_tokens_from_market,
        market_data,
        "test-1"
    )
    
    assert result is not None
