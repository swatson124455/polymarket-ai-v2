"""S223: _SemaphoreSession semaphore-slot leak on failed __aenter__.

Defect (2026-07-03 08:24 incident): _SemaphoreSession.__aenter__ acquired the
DB semaphore, then awaited session creation. Python never calls __aexit__ when
__aenter__ raises, so any failure after the acquire (pool-checkout timeout,
connect failure, CancelledError from an outer scan timeout) leaked the slot
permanently. Under the 07-03 03:00 DB storm, 15 leaked slots drained the
semaphore to 0 (`db_pool_health checked_in=9 checked_out=1
semaphore_available=0`) and wedged WeatherBot for ~54h: every scan failed with
"DB semaphore timeout — all slots occupied for 15s", no predictions, no
heartbeat, and the (pre-S249) watchdog co-wedged so systemd never restarted it.

Fix: everything after the semaphore acquire is wrapped in
except-BaseException that unwinds session/timeout-ctx and releases the slot
exactly once (finally-guaranteed) before re-raising.

Covers BOTH trees (S239 dead-tree trap):
  - base_engine/data/database.py            (imported by the running service)
  - bots/weather/engine/base_engine/data/database.py  (vendored weather silo)
"""

import asyncio

import pytest

from base_engine.data.database import (
    _SemaphoreSession as _TopLevelSemaphoreSession,
    DatabaseError,
)
from bots.weather.engine.base_engine.data.database import (
    _SemaphoreSession as _VendoredSemaphoreSession,
)


class _FailingEnterSession:
    """Simulates SQLAlchemy pool-checkout failure inside session.__aenter__."""

    def __init__(self, exc):
        self._exc = exc
        self.aexit_called = False

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.aexit_called = True
        return False


class _HealthySession:
    """Happy-path session: SET statements succeed."""

    def __init__(self):
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False

    async def execute(self, stmt):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _CorruptSetSession(_HealthySession):
    """SET statement_timeout fails with an asyncpg protocol-corruption signature
    (S235 discard path)."""

    async def execute(self, stmt):
        raise RuntimeError(
            "cannot switch to state 4; another operation (2) is in progress"
        )


@pytest.mark.parametrize(
    "sem_session_cls",
    [_TopLevelSemaphoreSession, _VendoredSemaphoreSession],
    ids=["top-level", "vendored"],
)
class TestSemaphoreLeakOnFailedEnter:
    @pytest.mark.asyncio
    async def test_pool_checkout_failure_releases_slot(self, sem_session_cls):
        """S223 defect repro: session.__aenter__ raises (pool checkout timeout)
        → the semaphore slot MUST be released. Pre-fix: leaked forever."""
        sem = asyncio.Semaphore(1)
        failing = _FailingEnterSession(TimeoutError("pool checkout timed out"))
        cm = sem_session_cls(lambda: failing, sem)

        with pytest.raises(TimeoutError):
            await cm.__aenter__()

        assert sem._value == 1, (
            f"Semaphore slot leaked on failed session enter: value={sem._value} "
            "(pre-S223 defect — 15 of these wedged WB for 54h on 2026-07-03)"
        )

    @pytest.mark.asyncio
    async def test_cancelled_error_releases_slot(self, sem_session_cls):
        """CancelledError (outer scan-timeout cancelling the task mid-checkout)
        must also release the slot — Exception-only handling misses it."""
        sem = asyncio.Semaphore(1)
        failing = _FailingEnterSession(asyncio.CancelledError())
        cm = sem_session_cls(lambda: failing, sem)

        with pytest.raises(asyncio.CancelledError):
            await cm.__aenter__()

        assert sem._value == 1, "Semaphore slot leaked on CancelledError"

    @pytest.mark.asyncio
    async def test_happy_path_releases_exactly_once(self, sem_session_cls):
        """Normal enter/exit: slot count returns to initial (no leak, no
        over-release growing the semaphore past its limit)."""
        sem = asyncio.Semaphore(2)
        session = _HealthySession()
        cm = sem_session_cls(lambda: session, sem)

        result = await cm.__aenter__()
        assert result is session
        assert sem._value == 1, "Slot must be held while the session is open"
        await cm.__aexit__(None, None, None)
        assert sem._value == 2, "Slot must be released exactly once on exit"
        assert session.exited


class TestCorruptPathSingleRelease:
    """Top-level only: the S235 corrupt-connection discard path raises out of
    __aenter__ — it must release the slot exactly ONCE (the S223 outer handler
    owns the release; a second release inside the corrupt path would grow the
    semaphore beyond its limit and quietly raise the concurrency ceiling)."""

    @pytest.mark.asyncio
    async def test_corrupt_connection_discard_releases_exactly_once(self):
        sem = asyncio.Semaphore(1)
        session = _CorruptSetSession()
        cm = _TopLevelSemaphoreSession(lambda: session, sem)

        with pytest.raises(DatabaseError, match="corrupted"):
            await cm.__aenter__()

        assert sem._value == 1, (
            f"Expected exactly one release (value back to 1), got {sem._value} — "
            "0 means leak, 2 means double-release"
        )
        assert session.exited, "Corrupted session must be closed, not returned"
