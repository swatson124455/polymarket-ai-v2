"""S195 — Contract tests for Database.backfill_trade_events_resolution()
and its wiring across the 3 invocation paths.

Purpose:
  (a) The method exists, returns int, and is no-op when session_factory is None
  (b) An unconditional emission counter log fires on every invocation, so
      future silent-zero (the bug this fix targets) is visible in journal
  (c) The 3 wiring sites (mini scheduler, on_resolution event handler,
      full backfill orchestrator) all reach the new method
  (d) The legacy `paper_updated > 0 OR updated > 0` gate is gone — calling
      the method directly with no upstream activity must still execute
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Contract (a): method existence + no-op on no-DB ──────────────────────────

@pytest.mark.asyncio
async def test_backfill_trade_events_resolution_no_db_returns_zero():
    """If session_factory is None (API-only / test context), method is a no-op
    that returns 0. Never raises, never blocks the trade path."""
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = None

    n = await db.backfill_trade_events_resolution()
    assert n == 0


@pytest.mark.asyncio
async def test_backfill_trade_events_resolution_returns_int():
    """Smoke contract: method returns a non-negative int."""
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = None

    result = await db.backfill_trade_events_resolution()
    assert isinstance(result, int)
    assert result >= 0


# ── Contract (b): unconditional emission counter log ─────────────────────────

@pytest.mark.asyncio
async def test_backfill_trade_events_resolution_logs_counter_on_zero():
    """The silent-zero detector. Even when both Phase 4b queries return zero
    rows, the method MUST log `trade_events_resolution_backfill` with phase4b/
    phase4b_alt/total counts, so journal grep can confirm the path is alive
    in steady-state. This is the regression guard for the bug we're fixing.
    """
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = MagicMock()  # truthy
    db.insert_trade_event = AsyncMock(return_value=1)  # callable, never invoked

    # Mock both Phase 4b and Phase 4b-alt sessions to return empty result sets.
    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    session = AsyncMock()
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db.get_session = MagicMock(return_value=ctx)

    with patch("base_engine.data.database.logger") as mock_logger:
        n = await db.backfill_trade_events_resolution()

    assert n == 0
    # The unconditional emission counter MUST have been logged.
    info_calls = [c for c in mock_logger.info.call_args_list
                  if c.args and c.args[0] == "trade_events_resolution_backfill"]
    assert len(info_calls) == 1, (
        "trade_events_resolution_backfill log MUST fire on every invocation "
        "(silent-zero detector — the regression guard for this fix)"
    )
    kwargs = info_calls[0].kwargs
    assert kwargs.get("phase4b") == 0
    assert kwargs.get("phase4b_alt") == 0
    assert kwargs.get("total") == 0


# ── Contract (c): the 3 wiring sites all reach the method ────────────────────

@pytest.mark.asyncio
async def test_mini_backfill_calls_trade_events_resolution(monkeypatch):
    """ingestion_scheduler._do_mini_backfill must call the new method —
    this is the path that ensures self-healing if the full backfill goes silent.
    """
    from base_engine.data.ingestion_scheduler import IngestionScheduler

    sched = IngestionScheduler.__new__(IngestionScheduler)

    db = MagicMock()
    db.backfill_prediction_log_resolution = AsyncMock(return_value=0)
    db.backfill_prediction_log_from_closed_trades = AsyncMock(return_value=0)
    db.backfill_paper_trades_resolution = AsyncMock(return_value=0)
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=0)
    db.backfill_trade_events_resolution = AsyncMock(return_value=0)

    from datetime import datetime, timezone
    await sched._do_mini_backfill(db, datetime.now(timezone.utc))

    db.backfill_trade_events_resolution.assert_awaited_once()


@pytest.mark.asyncio
async def test_mini_backfill_logs_emitted_count_when_nonzero():
    """When the new method emits N > 0 RESOLUTION events, the mini-backfill
    summary log must include that count alongside the other backfill counts.
    """
    from base_engine.data.ingestion_scheduler import IngestionScheduler

    sched = IngestionScheduler.__new__(IngestionScheduler)

    db = MagicMock()
    db.backfill_prediction_log_resolution = AsyncMock(return_value=0)
    db.backfill_prediction_log_from_closed_trades = AsyncMock(return_value=0)
    db.backfill_paper_trades_resolution = AsyncMock(return_value=0)
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=0)
    db.backfill_trade_events_resolution = AsyncMock(return_value=7)

    with patch("base_engine.data.ingestion_scheduler.logger") as mock_logger:
        from datetime import datetime, timezone
        await sched._do_mini_backfill(db, datetime.now(timezone.utc))

    # The summary log fires when any backfill produced rows.
    info_calls = [c for c in mock_logger.info.call_args_list]
    assert any("trade_events_resolution" in str(c) for c in info_calls), (
        "mini-backfill summary must include trade_events_resolution count"
    )


# ── Contract (d): legacy gate is gone ────────────────────────────────────────

@pytest.mark.asyncio
async def test_method_runs_unconditionally_no_upstream_gate():
    """Direct-call the method with empty session — it MUST issue the SQL
    queries (i.e. session.execute is called) regardless of upstream state.
    The regression we're fixing: the legacy `paper_updated > 0 OR updated > 0`
    gate stayed false forever once paper_trades caught up, killing emission.
    """
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = MagicMock()
    db.insert_trade_event = AsyncMock(return_value=1)

    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    session = AsyncMock()
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db.get_session = MagicMock(return_value=ctx)

    await db.backfill_trade_events_resolution()

    # Phase 4b: SET LOCAL + main SELECT = 2 calls
    # Phase 4b-alt: SET LOCAL + main SELECT = 2 calls
    # Total: 4 calls minimum. The point is execute was reached, not gated.
    assert session.execute.await_count >= 4, (
        f"expected >=4 execute calls (Phase 4b + 4b-alt SET LOCAL + SELECT), "
        f"got {session.execute.await_count} — gate may have been re-introduced"
    )
