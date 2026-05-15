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


# ---------------------------------------------------------------------------
# S216 Item 5-v2: keyword filter false-positive coverage.
# Same module as the refresh fixes above; the keyword filter is what gates
# which markets even reach the refresh path, so the tests live together.
# ---------------------------------------------------------------------------

class TestKeywordFilterFalsePositives:
    """_is_real_esports() must reject non-esports markets that contain
    short esports-adjacent tokens (e.g. 'COD' as in COD Mekn\xe8s soccer club)."""

    def test_cod_meknes_soccer_rejected(self):
        """COD Mekn\xe8s is a Moroccan soccer club. Markets about its matches
        (vs Wydad, FathUnionSport, RS Berkane) must NOT be classified as
        esports. S215 found 18+ such markets in DB; S216 drops the bare
        'cod ' substring to eliminate the dormant FP class."""
        soccer_questions = [
            "Will COD Mekn\xe8s win on 2026-04-29?",
            "AS FAR vs. COD Mekn\xe8s: O/U 4.5",
            "Will COD Mekn\xe8s vs. RS Berkane end in a draw?",
            "Wydad Athletic Club vs. COD Mekn\xe8s: O/U 2.5",
            "Will FathUnionSport vs. COD Mekn\xe8s end in a draw?",
            "COD Mekn\xe8s vs. RS Berkane: Both Teams to Score",
            "Spread: COD Mekn\xe8s (-2.5)",
        ]
        for q in soccer_questions:
            assert not ems._is_real_esports(q), \
                f"expected FP rejection for soccer question: {q!r}"

    def test_call_of_duty_branded_still_accepted(self):
        """The unambiguous 'call of duty' substring must still classify
        Polymarket's branded CoD markets as esports."""
        cod_questions = [
            "Who wins the Call of Duty League 2026 Championship?",
            "Will OpTic Texas win the Call of Duty Major IV?",
            "Call of Duty Major V winner",
        ]
        for q in cod_questions:
            assert ems._is_real_esports(q), \
                f"expected esports classification: {q!r}"

    def test_cdl_acronym_still_accepted(self):
        """The \\bcdl\\b boundary regex must still classify CDL-prefixed
        markets as esports (alternate path when 'call of duty' is omitted)."""
        cdl_questions = [
            "Will Atlanta FaZe win the CDL Major IV?",
            "CDL Champs 2026 winner",
        ]
        for q in cdl_questions:
            assert ems._is_real_esports(q), \
                f"expected esports classification via CDL: {q!r}"

    def test_other_esports_unaffected(self):
        """Spot-check that the cod-substring removal didn't break other games."""
        assert ems._is_real_esports("LoL: Movistar KOI vs GIANTX (BO3)")
        assert ems._is_real_esports("Will FUT Esports win VCT 2026: EMEA League Stage 1?")
        assert ems._is_real_esports("Counter-Strike Major 2026 winner")
        assert ems._is_real_esports("Will Team Spirit win The International 2026?")
