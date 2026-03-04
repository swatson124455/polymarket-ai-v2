"""
Tests for Session 46 production readiness fixes:
- Expired position auto-close
- Circuit breaker reset on startup
- Circuit breaker state query
"""
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fix 3: Expired position auto-close tests
# ---------------------------------------------------------------------------

def _make_mock_position(pos_id, market_id, status="open"):
    """Create a mock Position object for testing."""
    pos = MagicMock()
    pos.id = pos_id
    pos.market_id = market_id
    pos.status = status
    pos.token_id = f"token_{pos_id}"
    pos.entry_price = 0.60
    pos.current_price = 0.65
    pos.size = 10.0
    pos.unrealized_pnl = 0.50
    pos.entry_cost = 0.12
    pos.breakeven_price = 0.624
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=2)
    pos.bot_id = "ensemble_bot"
    return pos


def _make_mock_db():
    """Create a minimal mock database for position_manager."""
    db = MagicMock()
    db.session_factory = True
    return db


@pytest.mark.asyncio
async def test_expired_market_position_auto_closed():
    """Position on a market past end_date_iso should be auto-closed."""
    from base_engine.execution.position_manager import AutomatedPositionManager

    db = _make_mock_db()
    pm = AutomatedPositionManager(
        execution_engine=MagicMock(),
        order_manager=MagicMock(),
        db=db,
    )

    # Position on expired market
    pos = _make_mock_position(1, "expired_market_123")

    # Mock session that returns expired end_date
    expired_date = datetime.now(timezone.utc) - timedelta(hours=24)
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("expired_market_123", expired_date)]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    active = await pm._close_expired_positions(mock_session, [pos])

    assert pos.status == "closed", "Expired position should be closed"
    assert len(active) == 0, "No active positions should remain"
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_active_market_position_not_closed():
    """Position on a market with future end_date should NOT be closed."""
    from base_engine.execution.position_manager import AutomatedPositionManager

    db = _make_mock_db()
    pm = AutomatedPositionManager(
        execution_engine=MagicMock(),
        order_manager=MagicMock(),
        db=db,
    )

    pos = _make_mock_position(2, "active_market_456")

    # End date is in the future
    future_date = datetime.now(timezone.utc) + timedelta(days=7)
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("active_market_456", future_date)]
    mock_session.execute = AsyncMock(return_value=mock_result)

    active = await pm._close_expired_positions(mock_session, [pos])

    assert pos.status == "open", "Active market position should stay open"
    assert len(active) == 1, "Position should remain in active list"


@pytest.mark.asyncio
async def test_no_end_date_position_not_closed():
    """Position without end_date (no match in markets table) should NOT be closed."""
    from base_engine.execution.position_manager import AutomatedPositionManager

    db = _make_mock_db()
    pm = AutomatedPositionManager(
        execution_engine=MagicMock(),
        order_manager=MagicMock(),
        db=db,
    )

    pos = _make_mock_position(3, "unknown_market_789")

    # No end_date returned for this market
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []  # No matching markets
    mock_session.execute = AsyncMock(return_value=mock_result)

    active = await pm._close_expired_positions(mock_session, [pos])

    assert pos.status == "open", "Position without end_date should stay open"
    assert len(active) == 1, "Position should remain in active list"


# ---------------------------------------------------------------------------
# Fix 2: Circuit breaker tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_reset_on_startup():
    """Circuit breaker should be CLOSED after reset (called in _ensure_client)."""
    from base_engine.data.polymarket_client import CircuitBreaker

    cb = CircuitBreaker()
    # Simulate failures to trip the breaker
    cb.state = "OPEN"
    cb.failure_count = 5
    cb._open_reject_count = 42
    import time
    cb.last_failure_time = time.time()

    await cb.reset()

    assert cb.state == "CLOSED"
    assert cb.failure_count == 0
    assert cb._open_reject_count == 0
    assert cb.last_failure_time is None


def test_circuit_breaker_state_query():
    """get_circuit_breaker_state() should return correct dict."""
    from base_engine.data.polymarket_client import PolymarketClient

    with patch.dict("os.environ", {"DATABASE_URL": "sqlite:///:memory:"}):
        client = PolymarketClient()

    state = client.get_circuit_breaker_state()
    assert isinstance(state, dict)
    assert state["state"] == "CLOSED"
    assert state["failure_count"] == 0
    assert state["rejected_calls"] == 0
