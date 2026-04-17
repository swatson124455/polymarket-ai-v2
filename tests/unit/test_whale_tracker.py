"""S181 Commit 1: WhaleTracker._process_whale_trade uses ON CONFLICT DO NOTHING
on trade_id to handle concurrent UNIQUE races.

These tests pin the statement shape: pg_insert(...).on_conflict_do_nothing(
index_elements=["trade_id"]). PostgreSQL's own semantics cover the race-resolution
at runtime; we just verify our code invokes the correct SQLAlchemy API.

Regression protection: if anyone re-adds an explicit `session.add(movement);
session.commit()` or an `IntegrityError` raise in this path, these tests fail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects.postgresql import Insert as PGInsert

from base_engine.signals.whale_tracker import WhaleTracker


def _sample_trade() -> dict:
    return {
        "id": "trade-abc-123",
        "user_address": "0xdeadbeef",
        "market_id": "0xmarket",
        "token_id": "tok-1",
        "side": "BUY",
        "size": 500.0,
        "price": 0.55,
        "value_usd": 275.0,
        "timestamp": datetime.now(timezone.utc),
    }


def _make_tracker() -> tuple[WhaleTracker, MagicMock]:
    """Build a WhaleTracker with mocks for client/db/cache. Returns (tracker, session_mock)."""
    client = MagicMock()
    client.get_market = AsyncMock(return_value={"category": "politics"})

    # Mock db.get_session() context manager. Session yields mock with execute + commit.
    session_mock = MagicMock()
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()
    # session.execute(select(...)).scalar_one_or_none() → None for "not already processed"
    select_result = MagicMock()
    select_result.scalar_one_or_none = MagicMock(return_value=None)
    session_mock.execute.return_value = select_result

    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=session_mock)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    db = MagicMock()
    db.get_session = MagicMock(return_value=db_cm)

    cache = MagicMock()
    cache.redis = None  # skip redis publish in test
    cache.get = AsyncMock(return_value=None)

    tracker = WhaleTracker(client=client, db=db, cache=cache)
    # Patch internal lookups that would otherwise hit DB
    tracker._get_smart_money_rank = AsyncMock(return_value=1)
    tracker._get_category_accuracy = AsyncMock(return_value=0.6)
    tracker._get_cluster_id = AsyncMock(return_value=None)
    return tracker, session_mock


@pytest.mark.asyncio
async def test_whale_insert_uses_on_conflict_do_nothing():
    """The insert must be a pg_insert(WhaleMovement).on_conflict_do_nothing(['trade_id'])
    statement, not a plain ORM session.add(). This is the S181 Commit 1 contract.
    """
    tracker, session_mock = _make_tracker()
    await tracker._process_whale_trade(_sample_trade())

    # session.execute called at least twice: once for the SELECT pre-check, once for INSERT.
    assert session_mock.execute.await_count >= 2, \
        f"expected >=2 execute calls (SELECT + INSERT), got {session_mock.execute.await_count}"

    # The INSERT call's first positional arg must be a pg Insert with on_conflict_do_nothing.
    insert_call = session_mock.execute.await_args_list[-1]  # last call is the INSERT
    stmt = insert_call.args[0]
    assert isinstance(stmt, PGInsert), \
        f"expected PG Insert statement, got {type(stmt).__name__}"

    # Confirm ON CONFLICT clause targets trade_id and does nothing.
    # SQLAlchemy stores this on the `_post_values_clause` as OnConflictDoNothing.
    ocd = getattr(stmt, "_post_values_clause", None)
    assert ocd is not None, "pg_insert statement has no ON CONFLICT clause"
    assert type(ocd).__name__ == "OnConflictDoNothing", \
        f"expected OnConflictDoNothing, got {type(ocd).__name__}"

    # Verify index_elements=['trade_id']
    ie = getattr(ocd, "constraint_target", None) or getattr(ocd, "inferred_target_elements", None)
    # SQLAlchemy exposes inferred targets via inferred_target_elements as a list of Column objs
    target_names = [getattr(c, "name", str(c)) for c in (ie or [])]
    assert "trade_id" in target_names, \
        f"ON CONFLICT index_elements must include 'trade_id', got {target_names}"

    # Commit was called once for the insert transaction.
    assert session_mock.commit.await_count == 1


@pytest.mark.asyncio
async def test_whale_duplicate_trade_id_no_integrity_error():
    """Processing the same whale trade twice must not raise IntegrityError.
    With ON CONFLICT DO NOTHING the second insert is a no-op.

    This is a behavioural test against the mocked session: we simply verify
    that even when called twice for the same trade_id, _process_whale_trade
    completes without raising. The pre-insert SELECT check returning None
    (mocked) forces both calls down to the INSERT path.
    """
    tracker, session_mock = _make_tracker()
    trade = _sample_trade()

    # Both calls should complete cleanly — no IntegrityError bubbled up
    await tracker._process_whale_trade(trade)
    await tracker._process_whale_trade(trade)

    # Two INSERTs were attempted (session.execute called for SELECT+INSERT twice = 4 total)
    assert session_mock.execute.await_count >= 4
    # Two commits issued
    assert session_mock.commit.await_count == 2
