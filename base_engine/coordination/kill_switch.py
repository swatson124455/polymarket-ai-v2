"""
Kill Switch - Emergency stop for all trading.
Uses system_config table (key='kill_switch', value='true'|'false') and db.get_session().
Phase 8: 5s TTL cache to reduce DB reads on hot path.
"""
import time
from typing import Optional, Any
from structlog import get_logger
from base_engine.data.database import Database

logger = get_logger()
KILL_KEY = "kill_switch"
KILL_CACHE_TTL_SECONDS = 30.0  # TTL cache to reduce DB pressure


class KillSwitch:
    """Emergency stop for all bots. Uses system_config and session-based DB."""

    def __init__(self, db: Database, telegram_bot: Optional[Any] = None,
                 on_engage_callback=None):
        self.db = db
        self.telegram = telegram_bot
        self._on_engage_callback = on_engage_callback
        self._killed = False
        self._cache_engaged: Optional[bool] = None
        self._cache_until: float = 0.0
        self._order_gateway: Optional[Any] = None  # Wired by BaseEngine after construction

    async def check_kill_status(self) -> bool:
        """Check if kill switch is engaged. Phase 8: 5s cache to avoid DB on every check."""
        if self._killed:
            return True
        now = time.monotonic()
        if self._cache_until > now and self._cache_engaged is not None:
            return self._cache_engaged
        if not self.db.session_factory:
            return False
        from sqlalchemy import select
        from base_engine.data.database import SystemConfig
        try:
            # Use get_raw_session() to bypass the DB semaphore.
            # Kill switch is a single tiny SELECT — must never block on semaphore
            # exhaustion, or all bots hang indefinitely at the scan loop gate.
            async with self.db.get_raw_session() as session:
                r = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == KILL_KEY)
                )
                row = r.scalar_one_or_none()
                engaged = row is not None and (row.value or "").lower() == "true"
            self._cache_engaged = engaged
            self._cache_until = now + KILL_CACHE_TTL_SECONDS
            return engaged
        except Exception as e:
            logger.debug("Kill switch DB check failed (using cached): %s", e)
            # Return last known value instead of False — avoids flooding logs
            # and prevents trading when DB is down and we don't know the real state
            if self._cache_engaged is not None:
                return self._cache_engaged
            return True  # First-ever check failed, fail-safe: block trading

    async def is_engaged(self) -> bool:
        """Alias for check_kill_status for execution-loop use (check before execute)."""
        return await self.check_kill_status()

    def cached_engaged(self) -> Optional[bool]:
        """Last-known engaged state from the in-memory TTL cache, IGNORING the TTL.

        Non-blocking, performs NO DB I/O — safe to call from a scan-loop timeout
        handler. Returns:
          True  — engaged (or hard-killed in-process)
          False — not engaged per the last successful check (may be stale)
          None  — no successful check has populated the cache yet (state unknown)

        Deliberately ignores ``_cache_until``: a stale "not engaged" reading is
        acceptable for the staleness-tolerant scan-loop decision (the next cycle
        refreshes). NOT used by the execution path, which keeps the live check.
        """
        if self._killed:
            return True
        return self._cache_engaged

    def cache_age_seconds(self) -> Optional[float]:
        """Age (seconds) of the cached value, or None if never cached.

        Observability for the scan-loop timeout path: a growing age means the bot
        keeps timing out without ever refreshing the kill-switch state.
        """
        if self._cache_engaged is None:
            return None
        return max(0.0, time.monotonic() - (self._cache_until - KILL_CACHE_TTL_SECONDS))

    async def engage(self, reason: str = "Manual trigger") -> None:
        """Engage kill switch - stop all trading."""
        self._killed = True
        self._cache_engaged = True
        self._cache_until = time.monotonic() + KILL_CACHE_TTL_SECONDS
        # Kill switch blocks NEW orders and cancels pending open orders via
        # on_engage_callback (OMS.cancel_all_orders). Open POSITIONS are NOT
        # automatically closed — manual intervention required for position exits.
        logger.warning(
            "kill_switch_engaged_open_orders_persist",
            reason=reason,
            note="Open positions are NOT automatically cancelled — manual close required",
        )
        if not self.db.session_factory:
            return
        from sqlalchemy import select
        from base_engine.data.database import SystemConfig
        try:
            async with self.db.get_session() as session:
                r = await session.execute(select(SystemConfig).where(SystemConfig.key == KILL_KEY))
                row = r.scalar_one_or_none()
                if row:
                    row.value = "true"
                else:
                    session.add(SystemConfig(key=KILL_KEY, value="true"))
                await session.commit()
        except Exception as e:
            logger.error("Kill switch engage failed: %s", e)
            raise
        if self.telegram and hasattr(self.telegram, "send_alert"):
            await self.telegram.send_alert(f"KILL SWITCH ENGAGED: {reason}")
        # Mark open positions as halted in DB so bots don't re-enter them on restart
        if self._order_gateway is not None:
            try:
                halted = await self._order_gateway.mark_positions_halted()
                logger.warning("kill_switch_positions_halted", count=halted)
            except Exception as e:
                logger.error("kill_switch_halt_positions_failed", error=str(e))
        # Cancel all open orders (e.g., pending CLOB limit orders)
        if self._on_engage_callback:
            try:
                cancelled = await self._on_engage_callback()
                logger.warning("kill_switch_cancelled_open_orders", count=cancelled)
            except Exception as e:
                logger.error("kill_switch_cancel_orders_failed", error=str(e))
        logger.warning("Kill switch engaged: %s", reason)

    async def disengage(self) -> None:
        """Disengage kill switch - resume trading."""
        self._killed = False
        self._cache_engaged = False
        self._cache_until = time.monotonic() + KILL_CACHE_TTL_SECONDS
        if not self.db.session_factory:
            return
        from sqlalchemy import update
        from base_engine.data.database import SystemConfig
        try:
            async with self.db.get_session() as session:
                await session.execute(
                    update(SystemConfig).where(SystemConfig.key == KILL_KEY).values(value="false")
                )
                await session.commit()
        except Exception as e:
            logger.error("Kill switch disengage failed: %s", e)
            raise
        if self.telegram and hasattr(self.telegram, "send_alert"):
            await self.telegram.send_alert("Kill switch disengaged. Trading can resume.")
        logger.info("Kill switch disengaged")
