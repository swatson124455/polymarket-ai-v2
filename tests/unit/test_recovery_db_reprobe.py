"""Regression guard for the EsportsBot DB engine-leak root fix (2026-06-08).

`RecoveryProcedure._recover_database()` used to call `db.init()` (full engine
teardown+rebuild) on EVERY health-check "unhealthy", including a transient
pool-pressure timeout. Each rebuild orphaned the prior engine (dispose() can
raise on cancellation-poisoned asyncpg conns), pinning connections in
session-mode PgBouncer → the dominant shared-pool saturator (esports ~50/80).

The fix: when a live engine already exists, re-PROBE it non-destructively and
ONLY re-init when the probe fails with a real connection error (or no engine
exists at all). These tests pin all three branches.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.monitoring.recovery import RecoveryProcedure


def _ok_ctx():
    """An async-context-manager session whose SELECT 1 succeeds."""
    sess = AsyncMock()
    sess.execute = AsyncMock(return_value=MagicMock())
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=sess)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _raising_ctx(exc):
    """An async-context-manager session whose entry raises (probe failure)."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=exc)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_rp(db):
    return RecoveryProcedure(health_monitor=MagicMock(), db=db, cache=None)


@pytest.mark.asyncio
async def test_recover_database_skips_reinit_when_live_engine_probes_ok():
    """LEAK FIX: a transient timeout on a live engine must NOT rebuild it."""
    db = MagicMock()
    db.session_factory = MagicMock()        # engine already exists
    db.get_session = MagicMock(return_value=_ok_ctx())
    db.init = AsyncMock()
    rp = _make_rp(db)

    result = await rp._recover_database()

    db.init.assert_not_awaited()            # the leak: no rebuild on a blip
    assert result["success"] is True
    assert "skipped engine re-init" in result["message"]


@pytest.mark.asyncio
async def test_recover_database_reinits_when_reprobe_fails():
    """A genuinely broken engine (re-probe raises a real error) still rebuilds."""
    db = MagicMock()
    db.session_factory = MagicMock()
    # 1st get_session = re-probe (fails); 2nd = post-init verify (ok)
    db.get_session = MagicMock(side_effect=[_raising_ctx(OSError("connection refused")), _ok_ctx()])
    db.init = AsyncMock()
    rp = _make_rp(db)

    result = await rp._recover_database()

    db.init.assert_awaited_once()
    assert result["success"] is True
    assert result["message"] == "Database reconnected successfully"


@pytest.mark.asyncio
async def test_recover_database_reinits_when_no_session_factory():
    """No engine yet (session_factory is None) → must initialize."""
    db = MagicMock()
    db.session_factory = None

    async def _init():
        db.session_factory = MagicMock()    # init brings the engine up

    db.init = AsyncMock(side_effect=_init)
    db.get_session = MagicMock(return_value=_ok_ctx())   # post-init verify
    rp = _make_rp(db)

    result = await rp._recover_database()

    db.init.assert_awaited_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_recover_database_no_db_configured():
    """Unchanged contract: no db → failure dict, no crash."""
    rp = _make_rp(None)
    result = await rp._recover_database()
    assert result["success"] is False
