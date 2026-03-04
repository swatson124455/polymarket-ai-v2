import pytest
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.data.database import Database
from base_engine.data.redis_cache import RedisCache
from base_engine.learning.learning_engine import LearningEngine
from base_engine.prediction.prediction_engine import PredictionEngine
from base_engine.risk.risk_manager import RiskManager
from base_engine.backtesting.backtest_engine import BacktestEngine
from base_engine.learning.simulation_engine import SimulationEngine
from base_engine.base_engine import BaseEngine


def _make_mock_db():
    """Create a mock Database that doesn't require PostgreSQL.

    Provides:
    - db.engine / db.session_factory as truthy MagicMocks
    - db.get_session() as an async context manager yielding a mock session
    - Mock session.execute() returns empty result sets (0 counts, no rows)
    """
    database = MagicMock(spec=Database)
    database.engine = MagicMock()
    database.session_factory = MagicMock()

    mock_session = AsyncMock()

    # Default result mock — used by RiskManager queries (position counts, exposure sums)
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0
    mock_result.scalars.return_value.all.return_value = []
    mock_result.fetchall.return_value = []
    mock_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    @asynccontextmanager
    async def _get_session():
        yield mock_session
    database.get_session = _get_session

    # Common DB methods used by various engines
    database.get_trades_since = AsyncMock(return_value=[])
    database.get_bot_metrics = AsyncMock(return_value={"trades_won": 0, "total_pnl": 0.0})
    database.get_all_bots_metrics = AsyncMock(return_value=[])
    database.close = AsyncMock()
    database.init = AsyncMock()

    return database


@pytest.fixture
def db():
    """Mock Database — no PostgreSQL needed."""
    return _make_mock_db()


@pytest.fixture
async def cache():
    redis_cache = RedisCache()
    await redis_cache.init()
    yield redis_cache
    await redis_cache.close()


@pytest.fixture
def learning_engine(db):
    return LearningEngine(db)


@pytest.fixture
def risk_manager(db):
    return RiskManager(db)


@pytest.mark.asyncio
async def test_database_init(db):
    assert db.engine is not None
    assert db.session_factory is not None


@pytest.mark.asyncio
async def test_redis_cache(cache):
    """With Redis: round-trip set/get. Without Redis: no-op (get returns None, set does nothing)."""
    await cache.set("test_key", {"test": "value"}, ttl=60)
    value = await cache.get("test_key")
    if cache.redis is None:
        assert value is None
    else:
        assert value == {"test": "value"}


@pytest.mark.asyncio
async def test_learning_engine(learning_engine):
    confidence = await learning_engine.calculate_combined_confidence(
        user_address="0x123",
        price=0.5,
        category="politics",
        time_to_res="7-30days"
    )
    assert 0.0 <= confidence <= 1.0


@pytest.mark.asyncio
async def test_risk_manager_check_limits(risk_manager):
    checks = await risk_manager.check_risk_limits(
        bot_name="TestBot",
        market_id="test_market",
        size=100.0,
        price=0.5,
        confidence=0.6
    )
    assert "allowed" in checks
    assert isinstance(checks["allowed"], bool)


@pytest.mark.asyncio
async def test_risk_manager_position_size(risk_manager):
    size = await risk_manager.calculate_position_size(
        bot_name="TestBot",
        confidence=0.7,
        available_capital=10000.0,
        price=0.5
    )
    assert size > 0
    assert size <= 2000.0


@pytest.mark.asyncio
async def test_simulation_engine(db, learning_engine):
    sim_engine = SimulationEngine(db, learning_engine)
    result = await sim_engine.run_monte_carlo_simulation(
        market_id="test_market",
        token_id="test_token",
        price=0.5,
        iterations=1000
    )
    assert "win_probability" in result
    assert 0.0 <= result["win_probability"] <= 1.0
    assert "confidence_intervals" in result


@pytest.mark.asyncio
async def test_backtest_engine(db):
    import pandas as pd

    backtest_engine = BacktestEngine(db)

    # Minimal trade-like row so backtest runs without requiring real DB data
    minimal_df = pd.DataFrame([{
        "id": "t1",
        "market_id": "m1",
        "token_id": "tok1",
        "side": "BUY",
        "timestamp": datetime.now(timezone.utc),
        "price": 0.5,
        "size": 100.0,
    }])

    with patch.object(backtest_engine, "_get_trades_dataframe", new_callable=AsyncMock, return_value=minimal_df):
        with patch.object(backtest_engine, "_get_price_dataframe", new_callable=AsyncMock, return_value=pd.DataFrame()):
            async def dummy_strategy(trade, positions, capital):
                return {"action": "HOLD", "size": 0, "price": 0}

            start_date = datetime.now(timezone.utc) - timedelta(days=30)
            end_date = datetime.now(timezone.utc)

            result = await backtest_engine.run_backtest(
                dummy_strategy,
                start_date,
                end_date,
                initial_capital=10000.0
            )

    assert result is not None
    assert hasattr(result, "total_return")
    assert hasattr(result, "total_trades")


@pytest.mark.asyncio
async def test_backtest_engine_uses_price_history_when_trades_empty(db):
    import pandas as pd

    backtest_engine = BacktestEngine(db)
    empty_trades = pd.DataFrame()
    minimal_price_df = pd.DataFrame([{
        "id": "0",
        "market_id": "m1",
        "token_id": "tok1",
        "side": "YES",
        "size": 1.0,
        "price": 0.5,
        "timestamp": datetime.now(timezone.utc),
        "user_address": "backtest_price_data",
    }])

    with patch.object(backtest_engine, "_get_trades_dataframe", new_callable=AsyncMock, return_value=empty_trades):
        with patch.object(backtest_engine, "_get_price_dataframe", new_callable=AsyncMock, return_value=minimal_price_df):
            async def dummy_strategy(trade, positions, capital):
                return {"action": "HOLD", "size": 0, "price": 0}

            start_date = datetime.now(timezone.utc) - timedelta(days=30)
            end_date = datetime.now(timezone.utc)

            result = await backtest_engine.run_backtest(
                dummy_strategy, start_date, end_date, initial_capital=10000.0
            )

    assert result is not None
    assert hasattr(result, "total_return")
    assert hasattr(result, "total_trades")


@pytest.mark.asyncio
async def test_backtest_engine_prefers_prices_when_data_source_prices(db):
    import pandas as pd

    backtest_engine = BacktestEngine(db)
    minimal_price_df = pd.DataFrame([{
        "id": "0",
        "market_id": "m1",
        "token_id": "tok1",
        "side": "YES",
        "size": 1.0,
        "price": 0.5,
        "timestamp": datetime.now(timezone.utc),
        "user_address": "backtest_price_data",
    }])

    with patch.object(backtest_engine, "_get_trades_dataframe", new_callable=AsyncMock, return_value=pd.DataFrame()):
        with patch.object(backtest_engine, "_get_price_dataframe", new_callable=AsyncMock, return_value=minimal_price_df) as mock_prices:
            async def dummy_strategy(trade, positions, capital):
                return {"action": "HOLD", "size": 0, "price": 0}

            start_date = datetime.now(timezone.utc) - timedelta(days=30)
            end_date = datetime.now(timezone.utc)

            result = await backtest_engine.run_backtest(
                dummy_strategy, start_date, end_date, initial_capital=10000.0,
                data_source="prices",
            )

    assert result is not None
    assert mock_prices.called


@pytest.mark.asyncio
async def test_base_engine_init():
    base_engine = BaseEngine()
    try:
        await base_engine.init()
        assert base_engine.db is not None
        assert base_engine.client is not None
        assert base_engine.learning_engine is not None
        assert base_engine.prediction_engine is not None
        assert base_engine.risk_manager is not None
        assert base_engine.execution_engine is not None
        assert base_engine.simulation_engine is not None
    finally:
        await base_engine.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
