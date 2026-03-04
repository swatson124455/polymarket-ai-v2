"""
Unit tests for ingestion error handling and historical price flow.

Tests cover:
- DB verification failure: error message is never empty (use exception type when str is empty).
- Historical price flow: DB path vs API path, no double-processing, edge cases.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from base_engine.data.data_ingestion import DataIngestionService


class TestDBVerificationErrorMessage:
    """Ensure DB verification failure always shows a non-empty message."""

    @pytest.mark.asyncio
    async def test_db_verification_error_message_not_empty_when_exception_str_empty(self):
        """When DB check raises an exception with empty __str__, error_info must contain exception type."""
        client = AsyncMock()
        client.check_gamma_connectivity = AsyncMock(return_value=(True, "OK"))
        client.gamma_api = "https://gamma-api.polymarket.com"

        class EmptyMessageError(Exception):
            def __str__(self):
                return ""

        db = MagicMock()
        db.session_factory = MagicMock()
        # _verify_database is called with await; mock it to raise with empty str
        db._verify_database = AsyncMock(side_effect=EmptyMessageError())

        service = DataIngestionService(client=client, db=db)
        result = await service.ingest_all_markets(top_markets_count=5)

        assert result == 0
        assert service.ingestion_progress["status"] == "error"
        err_info = (service.ingestion_progress.get("error_info") or "").strip()
        # Fix: we use type name when str is empty, so "EmptyMessageError" must appear.
        assert "EmptyMessageError" in err_info or "Database connection failed verification" in err_info
        assert "verification failed:" in err_info
        # Must not end with just empty content after the exception type.
        assert err_info.endswith(":") is False

    @pytest.mark.asyncio
    async def test_db_verification_error_message_contains_detail_when_non_empty(self):
        """When DB check raises with a message, error_info must contain that message."""
        client = AsyncMock()
        client.check_gamma_connectivity = AsyncMock(return_value=(True, "OK"))
        client.gamma_api = "https://gamma-api.polymarket.com"

        db = MagicMock()
        db.session_factory = MagicMock()
        # _verify_database is called with await; mock it to raise ConnectionError
        db._verify_database = AsyncMock(side_effect=ConnectionError("Connection refused"))

        service = DataIngestionService(client=client, db=db)
        result = await service.ingest_all_markets(top_markets_count=5)

        assert result == 0
        err_info = (service.ingestion_progress.get("error_info") or "").strip()
        assert "Connection refused" in err_info


class TestHistoricalPriceFlow:
    """Historical price ingestion: single path (DB or API), no double-processing."""

    @pytest.mark.asyncio
    async def test_ingest_historical_prices_db_path_returns_success_without_api_loop(self):
        """When DB returns markets with token IDs, we use DB path and return; API path loop never runs."""
        client = AsyncMock()
        db = MagicMock()
        db.session_factory = MagicMock()

        db.get_recent_market_ids = AsyncMock(return_value=["m1", "m2"])
        db.get_markets_with_token_ids = AsyncMock(return_value=[
            {"id": "m1", "yes_token_id": "t1", "no_token_id": "t2"},
            {"id": "m2", "yes_token_id": "t3", "no_token_id": "t4"},
        ])
        # Range-aware fetch support (awaited in DB path)
        db.get_max_price_timestamps_for_markets = AsyncMock(return_value={})
        # Empty/reset price fetch tracking (awaited in DB path)
        db.record_empty_price_fetch = AsyncMock()
        db.reset_price_fetch_attempts = AsyncMock()

        # CLOB returns empty history so we don't need to mock complex responses.
        async def mock_price_history(*args, **kwargs):
            return []

        with patch(
            "base_engine.data.data_ingestion._fetch_price_history_chunked",
            new_callable=AsyncMock,
            side_effect=mock_price_history,
        ):
            db.bulk_insert_prices = AsyncMock(return_value=None)
            service = DataIngestionService(client=client, db=db)
            to_ts = int(datetime.now(timezone.utc).timestamp())
            from_ts = to_ts - (7 * 24 * 3600)

            result = await service.ingest_historical_prices(
                market_ids=None,
                from_timestamp=from_ts,
                to_timestamp=to_ts,
                max_markets=10,
            )

        assert result.get("success") is True
        assert result["diagnostics"]["markets_successful"] == 2
        # DB path: we never call get_markets(active=True) for the API path.
        client.get_markets.assert_not_called()

    @pytest.mark.asyncio
    async def test_ingest_historical_prices_no_markets_to_process_returns_success(self):
        """When we have IDs but all get_market calls return None, markets_to_process is empty; return success."""
        client = AsyncMock()
        client.get_market = AsyncMock(return_value=None)  # each ID fetch returns None
        db = MagicMock()
        db.session_factory = MagicMock()
        db.get_recent_market_ids = AsyncMock(return_value=["m1", "m2"])
        db.get_markets_with_token_ids = AsyncMock(return_value=[])  # no token IDs in DB

        service = DataIngestionService(client=client, db=db)
        to_ts = int(datetime.now(timezone.utc).timestamp())
        from_ts = to_ts - (7 * 24 * 3600)

        # ids_to_use = ["m1","m2"], but get_market(m1) and get_market(m2) return None -> markets_to_process = [].
        result = await service.ingest_historical_prices(
            market_ids=None,
            from_timestamp=from_ts,
            to_timestamp=to_ts,
            max_markets=10,
        )

        assert result.get("success") is True
        assert result["diagnostics"].get("markets_processed", 0) == 0
        assert "No markets to process" in result.get("message", "")
