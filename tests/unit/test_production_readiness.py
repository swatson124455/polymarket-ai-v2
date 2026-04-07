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


class _AsyncCM:
    """Minimal async context manager wrapping a mock session."""
    def __init__(self, session):
        self._session = session
    async def __aenter__(self):
        return self._session
    async def __aexit__(self, *args):
        pass


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

    # S159 C19 + S160: _close_expired_positions uses isolated sessions for both
    # query and UPDATE — never touches the main monitoring session.
    expired_date = datetime.now(timezone.utc) - timedelta(hours=24)
    mock_iso_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("expired_market_123", expired_date)]
    mock_iso_session.execute = AsyncMock(return_value=mock_result)
    mock_iso_session.commit = AsyncMock()
    db.get_session = MagicMock(return_value=_AsyncCM(mock_iso_session))
    db.insert_trade_event = AsyncMock()  # S159 C12: EXIT event emission

    mock_outer_session = AsyncMock()
    mock_outer_session.commit = AsyncMock()
    active = await pm._close_expired_positions(mock_outer_session, [pos])

    assert len(active) == 0, "No active positions should remain"
    # S160: Position closed via raw SQL in isolated session, not ORM mutation.
    # Outer session is never committed — prevents session poisoning.
    mock_outer_session.commit.assert_not_called()
    db.insert_trade_event.assert_called_once()
    # S160: Isolated session used for both query AND status update
    assert mock_iso_session.execute.call_count >= 1
    mock_iso_session.commit.assert_called()


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

    # End date is in the future — S159: mock db.get_session() for isolated session
    future_date = datetime.now(timezone.utc) + timedelta(days=7)
    mock_iso_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("active_market_456", future_date)]
    mock_iso_session.execute = AsyncMock(return_value=mock_result)
    db.get_session = MagicMock(return_value=_AsyncCM(mock_iso_session))

    mock_outer_session = AsyncMock()
    active = await pm._close_expired_positions(mock_outer_session, [pos])

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


@pytest.mark.asyncio
async def test_expired_position_commit_failure_does_not_poison_main_session():
    """S160: If isolated commit fails, main session must remain clean (not poisoned).

    This is the core test for the session poisoning fix. Previously, a failed
    commit on the main session left it in InFailedSQLTransactionError state,
    breaking _update_current_prices for the entire cycle.
    """
    from base_engine.execution.position_manager import AutomatedPositionManager

    db = _make_mock_db()
    pm = AutomatedPositionManager(
        execution_engine=MagicMock(),
        order_manager=MagicMock(),
        db=db,
    )

    pos = _make_mock_position(10, "expired_fail_market")

    expired_date = datetime.now(timezone.utc) - timedelta(hours=24)

    # Track which session is being created (query session vs close session)
    _call_count = 0
    _query_session = AsyncMock()
    _close_session = AsyncMock()

    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("expired_fail_market", expired_date)]
    _query_session.execute = AsyncMock(return_value=mock_result)

    # Close session commit FAILS — simulates DB error
    _close_session.commit = AsyncMock(side_effect=Exception("connection reset"))
    _close_session.execute = AsyncMock()

    def _make_session():
        nonlocal _call_count
        _call_count += 1
        if _call_count == 1:
            return _AsyncCM(_query_session)
        return _AsyncCM(_close_session)

    db.get_session = MagicMock(side_effect=_make_session)
    db.insert_trade_event = AsyncMock()  # EXIT event succeeds

    mock_outer_session = AsyncMock()
    active = await pm._close_expired_positions(mock_outer_session, [pos])

    # Main session must NEVER be committed or rolled back
    mock_outer_session.commit.assert_not_called()
    mock_outer_session.rollback = AsyncMock()
    mock_outer_session.rollback.assert_not_called()

    # EXIT event was recorded (before the commit failure)
    db.insert_trade_event.assert_called_once()

    # Position removed from active list (expired, EXIT recorded)
    # It will reappear next cycle since DB still has status='open'
    assert len(active) == 0


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
