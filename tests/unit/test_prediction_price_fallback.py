"""Unit tests for prediction price-history training fallback."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from base_engine.prediction.prediction_engine import PredictionEngine
from base_engine.learning.learning_engine import LearningEngine
from base_engine.data.database import Database


@pytest.fixture
def mock_db():
    db = MagicMock(spec=Database)
    db.session_factory = MagicMock()
    db.get_session = MagicMock()
    return db


@pytest.mark.asyncio
async def test_fallback_training_from_prices_empty_returns_none(mock_db):
    """_fallback_training_from_prices returns None when no resolved markets with prices."""
    session = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.fetchall = MagicMock(return_value=[])
    session.execute.return_value = result

    learning = LearningEngine(mock_db)
    engine = PredictionEngine(mock_db, learning)

    df = await engine._fallback_training_from_prices(session)
    assert df is None


@pytest.mark.asyncio
async def test_fallback_training_from_prices_returns_df_when_data(mock_db):
    """_fallback_training_from_prices returns DataFrame when resolved markets + prices exist."""
    from sqlalchemy.engine import Row

    mock_row = {
        "market_id": "m1",
        "token_id": "t1",
        "price": 0.6,
        "size": 1.0,
        "liquidity": 1000.0,
        "volume": 500.0,
        "resolved": True,
        "resolution": "YES",
        "resolved_at": None,
        "user_win_rate": 0.5,
        "user_profit": 0.0,
        "outcome": 1,
        "trade_ts": None,
    }
    row_obj = MagicMock()
    row_obj._mapping = mock_row

    session = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.fetchall = MagicMock(return_value=[row_obj])
    session.execute.return_value = result

    learning = LearningEngine(mock_db)
    engine = PredictionEngine(mock_db, learning)

    df = await engine._fallback_training_from_prices(session)
    assert df is not None
    assert len(df) == 1
    assert df.iloc[0]["market_id"] == "m1"
    assert df.iloc[0]["outcome"] == 1
