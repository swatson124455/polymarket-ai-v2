"""S182 Phase 1b Commit 2: EsportsMarketService refresh fixes.

Three changes gated by ESPORTS_MARKETS_REFRESH_V2_ENABLED (default true):
1. ORDER BY updated_at ASC NULLS FIRST added to refresh query — deterministic
   rotation of the 1,487-row in-scope set so stale rows rotate to top first.
2. Silent-exception handler's logger.debug → logger.warning(exc_info=True) —
   an 18h+ outage where the refresh loop crashed every iteration was masked
   by DEBUG level.
3. EsportsMarketService_cycle_complete heartbeat log emitted OUTSIDE the
   stats["total"] > 0 guard — zero-row cycles now emit too, so the refresh
   loop's liveness is always visible.

Flag-off path preserves legacy behavior for instant rollback.

Tests use an in-memory stub for the DB session to capture the SQL text
executed, which is enough to verify the contract without a live Postgres.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import esports.markets.esports_market_service as ems


@pytest.mark.asyncio
async def test_refresh_query_includes_order_by_when_flag_on(monkeypatch):
    """With ESPORTS_MARKETS_REFRESH_V2_ENABLED=true (default), the refresh
    query text must contain ORDER BY updated_at ASC NULLS FIRST."""
    monkeypatch.setattr(ems, "_MARKETS_REFRESH_V2_ENABLED", True)

    captured_sql: list[str] = []

    class _FakeResult:
        def fetchall(self):
            return []

    class _FakeSession:
        async def execute(self, stmt):
            # Capture the rendered SQL text for inspection
            captured_sql.append(str(stmt))
            return _FakeResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _FakeDB:
        # session_factory truthy — passes the early-return gate
        session_factory = object()

        def get_session(self):
            return _FakeSession()

    svc = ems.EsportsMarketService(db=_FakeDB())
    await svc.refresh_market_prices()

    assert captured_sql, "expected at least one SQL statement executed"
    assert any("ORDER BY updated_at ASC NULLS FIRST" in s for s in captured_sql), \
        f"ORDER BY clause missing; captured SQL: {captured_sql}"


@pytest.mark.asyncio
async def test_refresh_query_no_order_by_when_flag_off(monkeypatch):
    """With ESPORTS_MARKETS_REFRESH_V2_ENABLED=false, legacy unordered query
    must be used (rollback path preserved)."""
    monkeypatch.setattr(ems, "_MARKETS_REFRESH_V2_ENABLED", False)

    captured_sql: list[str] = []

    class _FakeResult:
        def fetchall(self):
            return []

    class _FakeSession:
        async def execute(self, stmt):
            captured_sql.append(str(stmt))
            return _FakeResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _FakeDB:
        # session_factory truthy — passes the early-return gate
        session_factory = object()

        def get_session(self):
            return _FakeSession()

    svc = ems.EsportsMarketService(db=_FakeDB())
    await svc.refresh_market_prices()

    assert captured_sql, "expected at least one SQL statement executed"
    # Legacy path: no ORDER BY
    assert not any("ORDER BY" in s for s in captured_sql), \
        f"ORDER BY should be absent in legacy path; captured: {captured_sql}"


@pytest.mark.asyncio
async def test_cycle_complete_heartbeat_emitted_on_zero_total(monkeypatch):
    """With flag on, a zero-total cycle must still emit the heartbeat log.
    Pre-S182 the log was suppressed on total=0, which was exactly when the
    service was broken — opacity on failure. Pin the contract via direct
    logger-call capture to avoid test-order dependencies from structlog
    config changes in other tests.
    """
    monkeypatch.setattr(ems, "_MARKETS_REFRESH_V2_ENABLED", True)
    monkeypatch.setattr(ems, "_REFRESH_INTERVAL", 0.01)

    mock_logger = MagicMock()
    monkeypatch.setattr(ems, "logger", mock_logger)

    class _FakeDB:
        pass

    svc = ems.EsportsMarketService(db=_FakeDB())
    svc.refresh_market_prices = AsyncMock(return_value={
        "total": 0, "refreshed": 0, "closed": 0, "errors": 0,
    })

    task = svc.start_background_refresh()
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # Inspect every logger.info call's event name (first positional arg)
    info_events = [c.args[0] for c in mock_logger.info.call_args_list if c.args]
    assert "EsportsMarketService_cycle_complete" in info_events, \
        f"expected heartbeat event on zero-total cycle; info_events={info_events}"


@pytest.mark.asyncio
async def test_exception_logs_at_warning_level_when_flag_on(monkeypatch):
    """With flag on, an exception in refresh_market_prices must surface at
    WARNING level (with exc_info). Pre-S182 it was DEBUG and invisible."""
    monkeypatch.setattr(ems, "_MARKETS_REFRESH_V2_ENABLED", True)
    monkeypatch.setattr(ems, "_REFRESH_INTERVAL", 0.01)

    mock_logger = MagicMock()
    monkeypatch.setattr(ems, "logger", mock_logger)

    class _FakeDB:
        pass

    svc = ems.EsportsMarketService(db=_FakeDB())
    svc.refresh_market_prices = AsyncMock(side_effect=RuntimeError("simulated crash"))

    task = svc.start_background_refresh()
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # V2 path: logger.warning called with the refresh-loop-error event + exc_info=True
    warning_calls = mock_logger.warning.call_args_list
    assert warning_calls, f"expected at least one logger.warning call; got none"
    found_refresh_error = any(
        c.args and "refresh loop error" in str(c.args[0]) and c.kwargs.get("exc_info") is True
        for c in warning_calls
    )
    assert found_refresh_error, \
        f"expected logger.warning('...refresh loop error...', exc_info=True); calls={warning_calls}"

    # And the legacy DEBUG path must NOT have been taken
    debug_calls = mock_logger.debug.call_args_list
    legacy_debug_taken = any(
        c.args and "refresh loop error" in str(c.args[0]) for c in debug_calls
    )
    assert not legacy_debug_taken, \
        f"flag-on should NOT route exception to DEBUG; debug_calls={debug_calls}"


@pytest.mark.asyncio
async def test_exception_logs_at_debug_when_flag_off(monkeypatch):
    """With flag off, legacy logger.debug path preserves rollback."""
    monkeypatch.setattr(ems, "_MARKETS_REFRESH_V2_ENABLED", False)
    monkeypatch.setattr(ems, "_REFRESH_INTERVAL", 0.01)

    mock_logger = MagicMock()
    monkeypatch.setattr(ems, "logger", mock_logger)

    class _FakeDB:
        pass

    svc = ems.EsportsMarketService(db=_FakeDB())
    svc.refresh_market_prices = AsyncMock(side_effect=RuntimeError("simulated crash"))

    task = svc.start_background_refresh()
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # Legacy: exception at DEBUG, NOT warning.
    warning_calls = mock_logger.warning.call_args_list
    v2_warning_taken = any(
        c.args and "refresh loop error" in str(c.args[0]) for c in warning_calls
    )
    assert not v2_warning_taken, \
        f"flag-off should route exception to DEBUG, not warning; warning_calls={warning_calls}"

    debug_calls = mock_logger.debug.call_args_list
    legacy_taken = any(
        c.args and "refresh loop error" in str(c.args[0]) for c in debug_calls
    )
    assert legacy_taken, \
        f"expected legacy debug path to be taken; debug_calls={debug_calls}"
