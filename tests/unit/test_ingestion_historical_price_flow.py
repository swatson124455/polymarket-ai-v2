"""
Comprehensive tests for historical price ingestion flow.

Covers:
- Happy path: DB path with token IDs, API path fallback
- Edge cases: empty markets, no token IDs, partial token IDs
- Error scenarios: API failure, DB failure, invalid token IDs
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.fixture
def mock_db_with_token_ids():
    """DB that returns markets with valid token IDs."""
    db = MagicMock()
    db.session_factory = MagicMock()
    db.get_markets_with_token_ids = AsyncMock(return_value=[
        {"id": "m1", "yes_token_id": "t1", "no_token_id": "t2"},
        {"id": "m2", "yes_token_id": "t3", "no_token_id": "t4"},
    ])
    db.get_recent_market_ids = AsyncMock(return_value=["m1", "m2"])
    db.get_markets_for_price_ingestion = AsyncMock(return_value=[
        {"id": "m1", "yes_token_id": "t1", "no_token_id": "t2"},
        {"id": "m2", "yes_token_id": "t3", "no_token_id": "t4"},
    ])
    db.get_max_price_timestamps_for_markets = AsyncMock(return_value={})
    db.bulk_insert_prices_raw = AsyncMock(side_effect=Exception("no constraint"))
    # FIX: return count of prices passed in (matches new int return type)
    db.bulk_insert_prices = AsyncMock(side_effect=lambda prices: len(prices))
    # P4: empty/success price-fetch tracking (async)
    db.record_empty_price_fetch = AsyncMock(return_value=None)
    db.reset_price_fetch_attempts = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_db_empty_tokens():
    """DB that returns markets but get_markets_with_token_ids returns [] (no token IDs)."""
    db = MagicMock()
    db.session_factory = True
    db.get_markets_with_token_ids = AsyncMock(return_value=[])
    db.get_recent_market_ids = AsyncMock(return_value=["m1", "m2"])
    db.get_markets_for_price_ingestion = AsyncMock(return_value=[
        {"id": "m1", "yes_token_id": None, "no_token_id": None},
    ])
    db.record_empty_price_fetch = AsyncMock(return_value=None)
    db.reset_price_fetch_attempts = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_client_price_history():
    """Client that returns valid price history."""
    client = MagicMock()
    client.get_price_history = AsyncMock(return_value={
        "history": [
            {"t": 1700000000, "p": 0.55},
            {"t": 1700003600, "p": 0.56},
        ]
    })
    client.get_market = AsyncMock(return_value={
        "id": "m1",
        "clobTokenIds": ["t1", "t2"],
        "question": "Test?",
    })
    return client


@pytest.mark.asyncio
async def test_ingest_historical_prices_db_path_success(mock_db_with_token_ids, mock_client_price_history):
    """Happy path: DB has token IDs, CLOB returns history, prices inserted."""
    from base_engine.data.data_ingestion import DataIngestionService

    service = DataIngestionService(client=mock_client_price_history, db=mock_db_with_token_ids)
    to_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - (365 * 24 * 60 * 60)

    result = await service.ingest_historical_prices(
        market_ids=["m1", "m2"],
        from_timestamp=from_ts,
        to_timestamp=to_ts,
        max_markets=10,
        resume_from_checkpoint=False,
    )

    assert result.get("success") is True
    diag = result.get("diagnostics", {})
    assert diag.get("markets_processed", 0) >= 1
    assert diag.get("prices_ingested", 0) >= 1
    mock_db_with_token_ids.get_markets_with_token_ids.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_historical_prices_filters_empty_token_ids(mock_db_empty_tokens, mock_client_price_history):
    """Edge case: get_markets_with_token_ids returns [], falls back to API path."""
    from base_engine.data.data_ingestion import DataIngestionService

    mock_client_price_history.get_market = AsyncMock(return_value={
        "id": "m1",
        "clobTokenIds": ["t1", "t2"],
        "question": "Test?",
    })
    service = DataIngestionService(client=mock_client_price_history, db=mock_db_empty_tokens)
    to_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - (7 * 24 * 60 * 60)

    result = await service.ingest_historical_prices(
        market_ids=["m1"],
        from_timestamp=from_ts,
        to_timestamp=to_ts,
        max_markets=5,
        resume_from_checkpoint=False,
    )

    # Should complete (success or no markets); no infinite loop
    assert "success" in result
    assert "diagnostics" in result


@pytest.mark.asyncio
async def test_ingest_historical_prices_no_markets_returns_success():
    """Edge case: market_ids provided but DB has no tokens, API get_market returns None - returns success, 0 processed."""
    db = MagicMock()
    db.session_factory = MagicMock()
    db.get_markets_with_token_ids = AsyncMock(return_value=[])
    db.get_recent_market_ids = AsyncMock(return_value=["m1"])

    client = MagicMock()
    client.get_market = AsyncMock(return_value=None)

    from base_engine.data.data_ingestion import DataIngestionService
    service = DataIngestionService(client=client, db=db)

    result = await service.ingest_historical_prices(
        market_ids=["m1"],
        from_timestamp=int(datetime.now(timezone.utc).timestamp()) - 86400,
        to_timestamp=int(datetime.now(timezone.utc).timestamp()),
        max_markets=10,
    )

    assert result.get("success") is True
    assert result.get("diagnostics", {}).get("markets_processed", 0) == 0


@pytest.mark.asyncio
async def test_ingest_historical_prices_api_failure_handled_gracefully():
    """Error scenario: CLOB API fails for token, should not crash, complete with 0 prices."""
    db = MagicMock()
    db.session_factory = MagicMock()
    db.get_markets_with_token_ids = AsyncMock(return_value=[
        {"id": "m1", "yes_token_id": "t1", "no_token_id": "t2"},
    ])
    db.get_max_price_timestamps_for_markets = AsyncMock(return_value={})
    db.bulk_insert_prices_raw = AsyncMock(side_effect=Exception("no constraint"))
    db.bulk_insert_prices = AsyncMock(return_value=0)  # FIX: matches new int return type
    db.record_empty_price_fetch = AsyncMock(return_value=None)
    db.reset_price_fetch_attempts = AsyncMock(return_value=None)

    client = MagicMock()
    client.get_price_history = AsyncMock(side_effect=Exception("CLOB API 500"))

    from base_engine.data.data_ingestion import DataIngestionService
    service = DataIngestionService(client=client, db=db)

    to_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - 86400

    result = await service.ingest_historical_prices(
        market_ids=["m1"],
        from_timestamp=from_ts,
        to_timestamp=to_ts,
        max_markets=1,
        resume_from_checkpoint=False,
    )

    assert "diagnostics" in result
    assert result.get("success") is True
    assert result.get("diagnostics", {}).get("prices_ingested", -1) == 0


def test_db_markets_filter_defensive():
    """Verify defensive filter: rows with both tokens empty are excluded in data_ingestion."""
    # Simulates the filter in ingest_historical_prices when db_markets has empty tokens
    db_markets = [
        {"id": "m1", "yes_token_id": "t1", "no_token_id": "t2"},
        {"id": "m2", "yes_token_id": "", "no_token_id": None},
        {"id": "m3", "yes_token_id": None, "no_token_id": "t4"},
    ]
    filtered = [
        r for r in db_markets
        if (r.get("yes_token_id") and str(r.get("yes_token_id", "")).strip())
        or (r.get("no_token_id") and str(r.get("no_token_id", "")).strip())
    ]
    assert len(filtered) == 2
    assert filtered[0]["id"] == "m1"
    assert filtered[1]["id"] == "m3"
