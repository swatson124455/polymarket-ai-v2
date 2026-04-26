"""S195 §8A — contract tests for the advisory-lock helper.

Pins the API + the dispatched SQL shape against a mocked AsyncSession.
These are unit tests; the actual cross-bot serialisation behaviour
needs a real Postgres + concurrent transactions, which lives in the
testcontainers-backed Phase F suite (deferred per design doc §4).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from base_engine.data.advisory_locks import advisory_lock_for_market


@pytest.mark.asyncio
async def test_helper_dispatches_pg_advisory_xact_lock_with_market_id() -> None:
    session = AsyncMock()
    async with advisory_lock_for_market(session, "0xabc"):
        pass
    session.execute.assert_awaited_once()
    args, _ = session.execute.call_args
    sql, params = args
    assert "pg_advisory_xact_lock(hashtext(:market_id))" in str(sql)
    assert params == {"market_id": "0xabc"}


@pytest.mark.asyncio
async def test_helper_uses_xact_not_session_lock() -> None:
    """xact-scoped, not session-scoped. Releases on commit/rollback —
    safer for asyncpg+SQLAlchemy where exceptions during retries can
    leak session-scoped locks until connection drop.
    """
    session = AsyncMock()
    async with advisory_lock_for_market(session, "0xdef"):
        pass
    sql_str = str(session.execute.call_args.args[0])
    assert "pg_advisory_xact_lock" in sql_str
    assert "pg_advisory_lock(" not in sql_str


@pytest.mark.asyncio
async def test_helper_rejects_empty_market_id() -> None:
    """Empty market_id would collapse all racers onto one lock slot —
    fail-loud rather than silently mass-serialising the system.
    """
    session = AsyncMock()
    with pytest.raises(ValueError, match="non-empty market_id"):
        async with advisory_lock_for_market(session, ""):
            pass
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_helper_rejects_none_market_id() -> None:
    session = AsyncMock()
    with pytest.raises(ValueError, match="non-empty market_id"):
        async with advisory_lock_for_market(session, None):  # type: ignore[arg-type]
            pass
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_helper_does_not_swallow_inner_exception() -> None:
    """Caller's transaction-management is responsible for rolling back
    on exception (which auto-releases the xact-scoped lock). The helper
    must NOT suppress exceptions raised inside the with-body.
    """
    session = AsyncMock()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with advisory_lock_for_market(session, "0x123"):
            raise _Boom("downstream failure")

    # Lock SQL still issued before the body raised.
    session.execute.assert_awaited_once()
