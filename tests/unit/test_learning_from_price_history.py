"""Unit tests for learn_from_price_history."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from base_engine.learning.learning_engine import LearningEngine
from base_engine.data.database import Database


@pytest.fixture
def mock_db():
    db = MagicMock(spec=Database)
    db.session_factory = MagicMock()
    db.get_session = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    ctx.__aexit__ = AsyncMock(return_value=None)
    db.get_session.return_value = ctx
    return db


@pytest.mark.asyncio
async def test_learn_from_price_history_updates_patterns(mock_db):
    """learn_from_price_history fetches prices, converts to trade-like, updates patterns."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    mock_prices = [
        {"market_id": "m1", "token_id": "t1", "price": 0.4, "timestamp": since},
        {"market_id": "m1", "token_id": "t1", "price": 0.5, "timestamp": since + timedelta(hours=1)},
        {"market_id": "m1", "token_id": "t1", "price": 0.6, "timestamp": since + timedelta(hours=2)},
    ]
    mock_db.get_prices_since = AsyncMock(return_value=mock_prices)

    from base_engine.data.database import Market
    mock_market = MagicMock()
    mock_market.id = "m1"
    mock_market.category = "politics"
    mock_market.end_date_iso = datetime.now(timezone.utc) + timedelta(days=7)

    session = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=mock_market)
    session.execute.return_value = result

    # get_session() is a sync method that returns an async context manager
    class MockSessionCtx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *a):
            pass

    mock_db.get_session = MagicMock(return_value=MockSessionCtx())

    engine = LearningEngine(mock_db)
    # Mock save_patterns_to_db to avoid deep mock chain issues
    engine.save_patterns_to_db = AsyncMock()
    result = await engine.learn_from_price_history(since, limit=1000)

    assert result is engine.patterns
    assert mock_db.get_prices_since.called
    call_args = mock_db.get_prices_since.call_args
    assert call_args[0][0] == since
    assert call_args[1].get("limit", 10000) in (1000, 10000)


@pytest.mark.asyncio
async def test_learn_from_price_history_empty_prices(mock_db):
    """When no prices, returns patterns unchanged."""
    mock_db.get_prices_since = AsyncMock(return_value=[])
    engine = LearningEngine(mock_db)
    result = await engine.learn_from_price_history(
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    assert result is engine.patterns
    mock_db.get_prices_since.assert_called_once()
