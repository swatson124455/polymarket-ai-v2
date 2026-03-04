"""
PostgreSQL advisory locks for pipeline concurrency.
Prevents data corruption when multiple processes (scheduler, Dashboard, CLI) run simultaneously.
Lock ordering: ingestion -> resolution_backfill -> elite_update -> model_training

IMPORTANT: Uses get_raw_session() to BYPASS the DB semaphore.
Advisory lock sessions are lightweight (hold a PG lock, no heavy queries).
If they went through the semaphore, they'd consume a slot for the entire duration
of the caller's work, causing deadlocks when the caller also needs get_session().
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy import text
from structlog import get_logger

logger = get_logger()

LOCK_IDS = {
    "ingestion": 100001,
    "resolution_backfill": 100002,
    "model_training": 100003,
    "elite_update": 100004,
    "trade_execution": 100005,
    "ingestion_markets": 100006,
    "ingestion_trades": 100007,
    "ingestion_prices": 100008,
}


class LockAcquisitionError(Exception):
    """Raised when advisory lock cannot be acquired within timeout."""


@asynccontextmanager
async def acquire_lock(db, lock_name: str, timeout_seconds: int = 300):
    """
    Acquire PostgreSQL advisory lock. Non-blocking retries every second.
    Yields control to caller; releases lock on exit.

    Uses get_raw_session() to bypass the DB semaphore — this prevents deadlocks
    when the caller needs additional DB sessions inside the locked section.
    """
    if db is None or not getattr(db, "session_factory", None):
        logger.warning("Database not available, skipping lock acquisition")
        yield
        return

    lock_id = LOCK_IDS.get(lock_name)
    if lock_id is None:
        raise ValueError(f"Unknown lock name: {lock_name}")

    # Use get_raw_session() — bypasses semaphore to avoid deadlock.
    # Advisory lock session is lightweight (holds PG lock, no heavy queries).
    _get_session = getattr(db, "get_raw_session", db.get_session)
    session_cm = _get_session()
    session = await session_cm.__aenter__()
    try:
        for attempt in range(timeout_seconds):
            r = await session.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
            acquired = r.scalar() if hasattr(r, "scalar") else r.fetchone()[0]
            # Commit immediately to close the implicit transaction.
            # pg_advisory_lock is session-scoped (not transaction-scoped) so the lock
            # remains held after commit, but committing clears "idle in transaction" state.
            try:
                await session.commit()
            except Exception:
                pass
            if acquired:
                logger.debug("Acquired lock %s (id=%s)", lock_name, lock_id)
                try:
                    yield
                finally:
                    try:
                        await session.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                        await session.commit()
                    except Exception:
                        pass  # Session closing will release advisory locks anyway
                    logger.debug("Released lock %s", lock_name)
                return
            await asyncio.sleep(1)
        raise LockAcquisitionError(
            f"Could not acquire lock '{lock_name}' within {timeout_seconds}s. "
            "Another process may be running. Try again later."
        )
    finally:
        await session_cm.__aexit__(None, None, None)
