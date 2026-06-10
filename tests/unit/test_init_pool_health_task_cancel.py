"""A1-GAP-3 (2026-06-08) — Database.init() cancels the prior _pool_health_task on
re-init, so each re-initialization does not orphan a 60s pool-health logging task
(previously only close() cancelled it).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.data.database import Database


class TestInitCancelsPoolHealthTask:
    @pytest.mark.asyncio
    async def test_reinit_cancels_prior_pool_health_task(self, monkeypatch):
        db = Database()

        # Simulate a live engine + a running pool-health task from a prior init.
        db.engine = MagicMock()
        db.engine.dispose = AsyncMock()
        db.session_factory = object()
        db._engine_loop_id = id(asyncio.get_running_loop())  # so same-loop dispose fires

        async def _forever():
            while True:
                await asyncio.sleep(3600)
        prior_task = asyncio.create_task(_forever())
        db._pool_health_task = prior_task

        # Stub the real engine creation + verification so init() doesn't hit a DB.
        async def _fake_init_postgres(url):
            db.session_factory = object()
        monkeypatch.setattr(db, "_init_postgres", _fake_init_postgres)
        monkeypatch.setattr(db, "_verify_database", AsyncMock())

        from config.settings import settings
        monkeypatch.setattr(settings, "DATABASE_URL",
                            "postgresql+asyncpg://u:p@localhost/db", raising=False)

        await db.init()
        await asyncio.sleep(0.05)  # let the cancellation propagate

        # done() not cancelled(): the real _log_pool_health swallows CancelledError and
        # ends done-but-not-cancelled; this stub re-raises so it ends cancelled. done()
        # covers both, pinning "the prior task was terminated" without stub-coupling.
        assert prior_task.done(), "re-init must terminate the prior pool-health task"
        # the re-init block also nulls the reference before _init_postgres sets a new one
        # (our fake _init_postgres doesn't create one), so it stays None here:
        assert db._pool_health_task is None

        if not prior_task.done():
            prior_task.cancel()
