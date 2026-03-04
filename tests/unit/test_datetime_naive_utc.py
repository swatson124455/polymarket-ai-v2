"""
Unit tests for datetime naive-UTC normalization (PostgreSQL TIMESTAMP WITHOUT TIME ZONE / asyncpg).

Covers:
- Happy path: aware -> naive UTC, naive unchanged, None -> None
- Edge cases: datetime with different timezones, already naive
- Error scenario: invalid types (handled by callers; _naive_utc is defensive)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Naive UTC conversion (mirrors database._naive_utc logic for isolated test)
def _naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class TestNaiveUtcConversion:
    """Test _naive_utc behavior (logic used in database.py)."""

    def test_none_returns_none(self):
        assert _naive_utc(None) is None

    def test_naive_unchanged(self):
        naive = datetime(2025, 1, 15, 12, 0, 0)
        assert _naive_utc(naive) is naive
        assert _naive_utc(naive).tzinfo is None

    def test_aware_utc_becomes_naive(self):
        aware_utc = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        out = _naive_utc(aware_utc)
        assert out.tzinfo is None
        assert out == datetime(2025, 1, 15, 12, 0, 0)

    def test_aware_other_tz_converted_to_utc_then_naive(self):
        from datetime import timezone as tz
        # EST = UTC-5
        est = tz(timedelta(hours=-5))
        aware_est = datetime(2025, 1, 15, 12, 0, 0, tzinfo=est)
        out = _naive_utc(aware_est)
        assert out.tzinfo is None
        assert out == datetime(2025, 1, 15, 17, 0, 0)  # 12 EST = 17 UTC


class TestDatabaseNaiveUtc:
    """Test database module _naive_utc and NaiveUTCDateTime (imports real module)."""

    def test_database_naive_utc_import(self):
        from base_engine.data.database import _naive_utc
        aware = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        out = _naive_utc(aware)
        assert out is not None
        assert out.tzinfo is None

    def test_naive_utc_datetime_process_bind_param(self):
        from base_engine.data.database import NaiveUTCDateTime, _naive_utc
        col = NaiveUTCDateTime()
        aware = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        bound = col.process_bind_param(aware, None)
        assert bound is not None
        assert bound.tzinfo is None
        assert bound == datetime(2025, 1, 15, 12, 0, 0)


class TestSaveMarketPriceNormalizesTimestamp:
    """Test that save_market_price normalizes aware datetime before DB write."""

    @pytest.mark.asyncio
    async def test_save_market_price_merge_receives_naive_timestamp(self):
        from base_engine.data.database import Database, MarketPrice, _naive_utc
        from base_engine.data.database_partitioning import get_partition_key

        captured = []

        async def fake_merge(obj):
            captured.append(obj)

        async def fake_commit():
            pass

        session = MagicMock()
        session.merge = AsyncMock(side_effect=fake_merge)
        session.commit = AsyncMock(side_effect=fake_commit)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        factory = MagicMock()
        factory.return_value = session
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)

        db = Database()
        db.session_factory = factory

        aware_ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        await db.save_market_price(
            market_id="m1",
            token_id="t1",
            price=0.5,
            timestamp=aware_ts,
            side="YES"
        )

        assert len(captured) == 1
        price_obj = captured[0]
        assert price_obj.timestamp is not None
        assert price_obj.timestamp.tzinfo is None, "merge() must receive naive UTC timestamp"


class TestBulkInsertPricesNormalizesTimestamp:
    """Test that bulk_insert_prices normalizes timestamps in dicts."""

    @pytest.mark.asyncio
    async def test_bulk_insert_prices_merge_receives_naive(self):
        from base_engine.data.database import Database

        captured = []

        async def fake_merge(obj):
            captured.append(obj)

        session = MagicMock()
        session.merge = AsyncMock(side_effect=fake_merge)
        begin_cm = MagicMock()
        begin_cm.__aenter__ = AsyncMock(return_value=None)
        begin_cm.__aexit__ = AsyncMock(return_value=None)
        session.begin = MagicMock(return_value=begin_cm)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)

        db = Database()
        db.session_factory = factory

        aware_ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        await db.bulk_insert_prices([
            {"market_id": "m1", "token_id": "t1", "price": 0.5, "timestamp": aware_ts, "side": "YES"}
        ])

        assert len(captured) >= 1
        for price_obj in captured:
            if hasattr(price_obj, "timestamp") and price_obj.timestamp is not None:
                assert price_obj.timestamp.tzinfo is None, "bulk_insert must pass naive UTC to MarketPrice"
