"""A2 (2026-06-08) — recovery distinguishes a busy-but-LIVE engine from a genuinely
broken one, so it does NOT tear down + re-init (the leak/restart storm driver) on
transient pool back-pressure.

Covers: false-positive-no-cascade (busy → no re-init), genuinely-broken-still-recovers
(factory None / genuine error → re-init), and restart-loop-gone (busy → success=True so
recovery.monitor_and_recover's 3-consecutive-failure sys.exit can never fire on busyness).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.monitoring.recovery import RecoveryProcedure
from base_engine.data.database import DatabaseError


def _session_cm(execute_side_effect=None):
    """A mock async context manager standing in for db.get_session()."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _recovery(db):
    return RecoveryProcedure(health_monitor=MagicMock(), db=db)


class TestRecoverDatabaseBusyVsBroken:
    @pytest.mark.asyncio
    async def test_live_engine_no_reinit(self):
        # Engine present, SELECT 1 succeeds → success, NO db.init() (false-positive-no-cascade)
        db = MagicMock()
        db.session_factory = object()
        db.get_session = MagicMock(return_value=_session_cm())
        db.init = AsyncMock()
        result = await _recovery(db)._recover_database()
        assert result["success"] is True
        db.init.assert_not_called()

    @pytest.mark.asyncio
    async def test_busy_transient_no_reinit_and_not_a_failure(self):
        # Engine present, probe raises DatabaseError (semaphore timeout) → success, NO re-init.
        # success=True is what stops recovery's 3-failure sys.exit restart loop (restart-loop-gone).
        db = MagicMock()
        db.session_factory = object()
        db.get_session = MagicMock(return_value=_session_cm(
            execute_side_effect=DatabaseError(
                "DB semaphore timeout — all slots occupied for 15s",
                operation="get_session", table=None,
            )))
        db.init = AsyncMock()
        result = await _recovery(db)._recover_database()
        assert result["success"] is True   # busy != failure → no sys.exit churn
        db.init.assert_not_called()         # no re-init storm on back-pressure

    @pytest.mark.asyncio
    async def test_session_factory_none_reinits(self):
        # Engine genuinely gone (session_factory None) → re-init (genuinely-broken-still-recovers)
        db = MagicMock()
        db.session_factory = None

        async def _init():
            db.session_factory = object()  # init brings the engine back
        db.init = AsyncMock(side_effect=_init)
        db.get_session = MagicMock(return_value=_session_cm())  # post-init verify ok
        result = await _recovery(db)._recover_database()
        db.init.assert_awaited_once()
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_corrupted_discard_databaseerror_treated_as_busy(self):
        # The OTHER DatabaseError the probe can hit: single corrupted connection
        # discarded at checkout (S235 path). Engine is live — must NOT re-init.
        db = MagicMock()
        db.session_factory = object()
        db.get_session = MagicMock(return_value=_session_cm(
            execute_side_effect=DatabaseError(
                "asyncpg connection corrupted (discarded): cannot switch to state",
                operation="get_session", table=None,
            )))
        db.init = AsyncMock()
        result = await _recovery(db)._recover_database()
        assert result["success"] is True
        db.init.assert_not_called()

    @pytest.mark.asyncio
    async def test_dead_db_escalation_preserved(self):
        # Genuinely dead DB: probe raises a NON-DatabaseError connection error,
        # re-init runs but cannot restore session_factory → success=False, so
        # monitor_and_recover's 3-consecutive-failure sys.exit escape hatch is
        # PRESERVED for real outages (the property A2 must not break).
        db = MagicMock()
        db.session_factory = object()
        db.get_session = MagicMock(return_value=_session_cm(
            execute_side_effect=ConnectionError("connection refused")))

        async def _init_fails():
            db.session_factory = None  # init swallowed the failure, factory nulled
        db.init = AsyncMock(side_effect=_init_fails)
        result = await _recovery(db)._recover_database()
        db.init.assert_awaited_once()
        assert result["success"] is False  # counts toward escalation

    @pytest.mark.asyncio
    async def test_genuine_connection_error_reinits(self):
        # Engine present but probe raises a non-DatabaseError connection error → re-init.
        db = MagicMock()
        db.session_factory = object()
        _calls = {"n": 0}

        def _get_session():
            _calls["n"] += 1
            if _calls["n"] == 1:
                return _session_cm(execute_side_effect=ConnectionError("connection refused"))
            return _session_cm()  # post-init verify ok
        db.get_session = MagicMock(side_effect=_get_session)
        db.init = AsyncMock()
        result = await _recovery(db)._recover_database()
        db.init.assert_awaited_once()       # genuine error → engine re-created
        assert result["success"] is True
