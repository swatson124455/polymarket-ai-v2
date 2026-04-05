import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from base_engine.base_engine import BaseEngine
from base_engine.backtesting.backtest_engine import BacktestEngine
from base_engine.learning.simulation_engine import SimulationEngine

# Skip the entire module if the remote DB is unreachable or paused
_DB_AVAILABLE = None

async def _check_db():
    """Quick connectivity check — verify DB is reachable before running integration tests."""
    global _DB_AVAILABLE
    if _DB_AVAILABLE is not None:
        return _DB_AVAILABLE
    try:
        # Fast probe: connect directly with asyncpg (skips full BaseEngine init overhead)
        from base_engine.data.database import Database
        from sqlalchemy import text
        db = Database()
        await db.init()
        async with db.get_session() as session:
            await session.execute(text("SELECT 1"))
        await db.close()
        _DB_AVAILABLE = True
    except Exception:
        _DB_AVAILABLE = False
    return _DB_AVAILABLE


@pytest.fixture
async def base_engine():
    if not await _check_db():
        pytest.skip("Remote DB unavailable or schema out-of-date")
    engine = BaseEngine()
    await engine.init()
    yield engine
    await engine.stop()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.xfail(reason="requires ingested historical data — fails on empty DB, see S156 review", strict=False)
async def test_integration_backtest_learning(base_engine):
    async def test_strategy(trade, positions, capital):
        return {"action": "BUY", "size": 10, "price": trade.get("price", 0.5)}
    
    start_date = datetime.now(timezone.utc) - timedelta(days=7)
    end_date = datetime.now(timezone.utc)
    
    backtest_result = await base_engine.run_backtest(
        test_strategy,
        start_date,
        end_date,
        initial_capital=10000.0
    )
    
    assert backtest_result is not None
    assert hasattr(backtest_result, "total_return")
    
    learning_result = await base_engine.learning_engine.learn_from_backtest(backtest_result)
    assert learning_result is not None
    assert "market_types" in learning_result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_simulation_learning(base_engine):
    simulation_result = await base_engine.run_simulation(
        market_id="test_market",
        token_id="test_token",
        price=0.5,
        iterations=1000
    )
    
    assert simulation_result is not None
    assert "win_probability" in simulation_result
    
    await base_engine.simulation_engine.learn_from_simulation(
        simulation_result,
        actual_outcome=0.6
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.xfail(reason="requires active markets in remote DB — fails on empty/stale DB", strict=False)
async def test_integration_prediction_execution(base_engine):
    # Use a real market from the DB so prediction engine can find it
    markets = await base_engine.get_markets(active=True, limit=5)
    if not markets:
        pytest.skip("No active markets in DB to test predictions")

    market = markets[0]
    market_id = market.get("id") or market.get("condition_id")
    token_id = market.get("token_id") or market.get("condition_id") or market_id

    try:
        prediction = await base_engine.get_predictions(
            market_id=market_id,
            token_id=token_id,
            price=0.5
        )
    except (RuntimeError, ValueError) as e:
        # Market may lack sufficient price history for full prediction
        pytest.skip(f"Prediction not possible for available markets: {e}")

    assert prediction is not None
    assert "confidence" in prediction
    assert 0.0 <= prediction["confidence"] <= 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_risk_execution(base_engine):
    risk_check = await base_engine.risk_manager.check_risk_limits(
        bot_name="TestBot",
        market_id="test_market",
        size=100.0,
        price=0.5,
        confidence=0.7
    )
    
    assert risk_check is not None
    assert "allowed" in risk_check
    
    if risk_check["allowed"]:
        size = await base_engine.risk_manager.calculate_position_size(
            bot_name="TestBot",
            confidence=0.7,
            available_capital=10000.0,
            price=0.5
        )
        assert size > 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.xfail(reason="requires active markets in remote DB — fails on empty/stale DB", strict=False)
async def test_integration_full_workflow(base_engine):
    markets = await base_engine.get_markets(active=True, limit=10)
    assert isinstance(markets, list)

    if not markets:
        pytest.skip("No active markets in DB for full workflow test")

    market = markets[0]
    market_id = market.get("id") or market.get("condition_id")
    token_id = market.get("token_id") or market.get("condition_id") or market_id

    try:
        prediction = await base_engine.get_predictions(
            market_id=market_id,
            token_id=token_id,
            price=0.5
        )
        assert prediction is not None
    except (RuntimeError, ValueError) as e:
        # Market may lack sufficient data; that's OK for workflow test
        pass

    simulation = await base_engine.run_simulation(
        market_id=market_id,
        token_id=token_id,
        price=0.5,
        iterations=100
    )

    assert simulation is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
