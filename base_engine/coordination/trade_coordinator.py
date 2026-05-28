"""
TradeCoordinator - Prevents multiple bots from taking the same position.
Uses positions table with status in ('open','reserving') and UNIQUE(bot_id, market_id, side).
Reserve uses INSERT ON CONFLICT for atomicity; retry loop for robustness.
Includes a stale reservation reaper to clean ghost reservations.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from structlog import get_logger
from sqlalchemy import text
from config.settings import settings
from base_engine.data.database import Database, Position

logger = get_logger()

DEFAULT_RESERVE_TIMEOUT = 30
DEFAULT_RESERVE_ATTEMPTS = 3
DEFAULT_RESERVE_BACKOFF = 0.2  # Phase 8: faster retry (was 1.0)
STALE_RESERVATION_MINUTES = 8  # Reservations older than this are reaped (8m: slow ArbitrageBot scans can legitimately hold 3-4m)
REAPER_INTERVAL_SECONDS = 60   # How often to run the reaper (was 120s; reduced to match shorter timeout)



# S178 2H-b: Cross-bot token mutual exclusion (opt-in, default off).
# Prevents two bots from simultaneously holding positions on the same token_id.
# Known limitation: TOCTOU race — two concurrent reserve calls can both pass
# the check before either inserts.  Acceptable for v1 (reduces collision
# probability, doesn't eliminate it).  Upgrade path: pg_try_advisory_xact_lock.
CROSS_BOT_TOKEN_MUTEX = getattr(settings, "ENABLE_CROSS_BOT_TOKEN_MUTEX", False)


class TradeCoordinator:
    """Prevents multiple bots from taking same position. Uses session-based DB."""

    def __init__(self, db: Database, bot_id: str):
        self.db = db
        self.bot_id = bot_id or "default"
        self._reaper_task: Optional[asyncio.Task] = None

    async def can_take_position(self, market_id: str, side: str) -> bool:
        """Check if any bot already has this position (open or reserving).
        Also blocks contradictory positions: if any bot has opposite side, cannot take this side.
        """
        if not self.db.session_factory:
            return True
        from sqlalchemy import select
        try:
            async with self.db.get_session() as session:
                r = await session.execute(
                    select(Position).where(
                        Position.market_id == market_id,
                        Position.status.in_(["open", "reserving"]),
                    )
                )
                for pos in r.scalars().all():
                    if pos.side == side:
                        logger.info(
                            "Position already exists",
                            bot_id=pos.bot_id,
                            market_id=market_id,
                            side=side,
                        )
                        return False
                    # Opposite side: contradictory position, block (bots can't fight each other)
                    req_yes = str(side).upper() in ("YES", "BUY")
                    pos_yes = pos.side and str(pos.side).upper() in ("YES", "BUY")
                    if req_yes != pos_yes:
                        logger.info(
                            "Contradictory position blocked",
                            market_id=market_id,
                            existing_side=pos.side,
                            requested_side=side,
                        )
                        return False
                return True
        except Exception as e:
            logger.warning("can_take_position failed", market_id=market_id, side=side, error=str(e))
            return False

    async def reserve_position(
        self,
        market_id: str,
        side: str,
        token_id: Optional[str] = None,
        reserving_bot_id: Optional[str] = None,
        timeout: float = DEFAULT_RESERVE_TIMEOUT,
    ) -> bool:
        """
        Atomically reserve a position slot with retry.
        Uses INSERT ... ON CONFLICT DO NOTHING RETURNING id for DB-level atomicity.
        reserving_bot_id: when set (e.g. bot_name), reserves under that bot so multiple entities
        (e.g. CryptoBot, PoliticalBot) have separate slots. Omit to use coordinator default bot_id.
        Returns True if reserved, False if that bot already holds it or retries exhausted.
        """
        if not self.db.session_factory:
            return True
        bot_id_for_reserve = reserving_bot_id if reserving_bot_id else self.bot_id
        tok = token_id or ""

        # S178 2H-b: Cross-bot token mutual exclusion — block if another bot
        # already holds this token_id.  Fail-open on DB error.
        if CROSS_BOT_TOKEN_MUTEX and tok:
            try:
                async with self.db.get_session() as _mutex_sess:
                    _conflict = await _mutex_sess.execute(
                        text("""
                            SELECT 1 FROM positions
                            WHERE token_id = :token_id
                              AND bot_id != :bot_id
                              AND status IN ('reserving', 'open')
                            LIMIT 1
                        """),
                        {"token_id": tok, "bot_id": bot_id_for_reserve},
                    )
                    if _conflict.fetchone() is not None:
                        logger.warning(
                            "cross_bot_token_mutex_blocked",
                            token_id=tok[:16],
                            requesting_bot=bot_id_for_reserve,
                            market_id=market_id,
                            side=side,
                        )
                        return False
            except Exception as _e:
                # Fail-open: if check fails, allow the trade
                logger.debug("cross_bot_token_mutex_check_failed", error=str(_e))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        for attempt in range(DEFAULT_RESERVE_ATTEMPTS):
            if loop.time() >= deadline:
                logger.warning("reserve_position timeout", market_id=market_id, side=side)
                return False
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)  # strip tz: opened_at is TIMESTAMP WITHOUT TIME ZONE
                async with self.db.get_session() as session:
                    # Postgres: ON CONFLICT (bot_id, market_id, side) DO UPDATE
                    # Re-use closed position rows (reset to 'reserving') to avoid unique constraint blocking.
                    # Only update if the existing row is closed — open/reserving rows stay blocked.
                    _is_paper = bool(getattr(settings, "SIMULATION_MODE", False))
                    result = await session.execute(
                        text("""
                            INSERT INTO positions (bot_id, market_id, token_id, side, size, entry_price, current_price, unrealized_pnl, opened_at, status, is_paper)
                            VALUES (:bot_id, :market_id, :token_id, :side, 0, 0, 0, 0, :opened_at, 'reserving', :is_paper)
                            ON CONFLICT (bot_id, market_id, side) DO UPDATE
                                SET status = 'reserving', size = 0, entry_price = 0, current_price = 0,
                                    unrealized_pnl = 0, opened_at = :opened_at, token_id = :token_id, is_paper = :is_paper
                                WHERE positions.status = 'closed'
                            RETURNING id
                        """),
                        {"bot_id": bot_id_for_reserve, "market_id": market_id, "token_id": tok, "side": side, "opened_at": now, "is_paper": _is_paper},
                    )
                    row = result.fetchone()
                    await session.commit()
                    if row is not None:
                        return True
            except Exception as e:
                logger.debug("reserve_position attempt %s failed: %s", attempt + 1, e)
            await asyncio.sleep(DEFAULT_RESERVE_BACKOFF)
        return False

    async def confirm_position(
        self,
        market_id: str,
        side: str,
        size: float,
        entry_price: float,
        source_bot: Optional[str] = None,
        bot_id: Optional[str] = None,
        token_id: str = "",
    ) -> None:
        """Confirm reserved position after successful trade. bot_id: which bot reserved (must match reserve). source_bot enables per-bot P&L attribution.

        S232 Bug 19: persist failure here creates an in-memory orphan — the
        caller (order_gateway) has already updated _open_positions and the
        Polymarket order has filled, but the DB row never lands. On restart
        the position is invisible to the bot. Defense: single retry on any
        DB error, then CRITICAL log with structured fields so operator can
        reconcile manually. We do NOT raise — the order succeeded on-chain
        and the caller's in-memory state is authoritative; raising would
        make the caller mark the trade as failed and the on-chain position
        becomes completely unmanaged.
        """
        if not self.db.session_factory:
            return
        which_bot = bot_id if bot_id is not None else self.bot_id
        from sqlalchemy import select
        _is_sell = side == "SELL"
        _MAX_PERSIST_ATTEMPTS = 2  # S232 Bug 19: 1 initial + 1 retry
        _RETRY_BACKOFF_S = 1.0
        _last_err: Optional[Exception] = None
        for _attempt in range(_MAX_PERSIST_ATTEMPTS):
            try:
                async with self.db.get_session() as session:
                    r = await session.execute(
                        select(Position).where(
                            Position.bot_id == which_bot,
                            Position.market_id == market_id,
                            Position.side == side,
                            Position.status == "reserving",
                        ).limit(1).with_for_update()
                    )
                    pos = r.scalar_one_or_none()
                    _cost_rate = (
                        getattr(settings, "FIXED_SLIPPAGE_BPS", 50)
                        + getattr(settings, "TAKER_FEE_BPS", 150)
                    ) / 10000.0
                    if pos:
                        pos.size = size
                        pos.entry_price = entry_price
                        pos.current_price = entry_price
                        pos.entry_cost = size * entry_price * _cost_rate
                        pos.breakeven_price = entry_price * (1.0 + 2 * _cost_rate)
                        if source_bot is not None:
                            pos.source_bot = source_bot
                        if _is_sell:
                            # SELL = exit: mark the SELL record as closed (it's an audit trail only)
                            # and close the original YES/NO position row for this market.
                            pos.status = "closed"
                            r2 = await session.execute(
                                select(Position).where(
                                    Position.bot_id == which_bot,
                                    Position.market_id == market_id,
                                    Position.side.in_(["YES", "NO"]),
                                    Position.status == "open",
                                ).limit(1).with_for_update()
                            )
                            orig = r2.scalar_one_or_none()
                            if orig:
                                orig.status = "closed"
                            else:
                                # H3 FIX: YES/NO row missing — try any open position for same (bot, market).
                                # Without this, ghost rows reappear on next restart via seed_positions_from_db.
                                r3 = await session.execute(
                                    select(Position).where(
                                        Position.bot_id == which_bot,
                                        Position.market_id == market_id,
                                        Position.status == "open",
                                    ).limit(1).with_for_update()
                                )
                                fallback = r3.scalar_one_or_none()
                                if fallback:
                                    fallback.status = "closed"
                                    logger.warning(
                                        "confirm_position: YES/NO row not found for market %s bot %s — "
                                        "closed fallback open row (side=%s) to prevent ghost position",
                                        market_id, which_bot, fallback.side,
                                    )
                                else:
                                    logger.error(
                                        "confirm_position: no open position found for market %s bot %s "
                                        "on SELL confirm — DB may be inconsistent (check seed on next restart)",
                                        market_id, which_bot,
                                    )
                        else:
                            pos.status = "open"
                        await session.commit()
                    elif not _is_sell:
                        # S103 FIX: No reserving row found — reserve was skipped
                        # (e.g. WEATHER_SKIP_COORDINATOR_BUY). Insert directly as open.
                        now = datetime.now(timezone.utc).replace(tzinfo=None)
                        _is_paper = bool(getattr(settings, "SIMULATION_MODE", False))
                        await session.execute(
                            text("""
                                INSERT INTO positions (bot_id, source_bot, market_id, token_id, side, size,
                                    entry_price, current_price, unrealized_pnl, opened_at, status, is_paper,
                                    entry_cost, breakeven_price)
                                VALUES (:bot_id, :source_bot, :market_id, :token_id, :side, :size,
                                    :entry_price, :entry_price, 0, :opened_at, 'open', :is_paper,
                                    :entry_cost, :breakeven_price)
                                ON CONFLICT (bot_id, market_id, side) DO UPDATE
                                    SET status = 'open', size = :size, entry_price = :entry_price,
                                        current_price = :entry_price, unrealized_pnl = 0,
                                        opened_at = :opened_at, is_paper = :is_paper,
                                        source_bot = :source_bot, token_id = :token_id,
                                        entry_cost = :entry_cost, breakeven_price = :breakeven_price
                                    WHERE positions.status = 'closed'
                            """),
                            {
                                "bot_id": which_bot,
                                "source_bot": source_bot or which_bot,
                                "market_id": market_id,
                                "token_id": token_id,
                                "side": side,
                                "size": size,
                                "entry_price": entry_price,
                                "opened_at": now,
                                "is_paper": _is_paper,
                                "entry_cost": size * entry_price * _cost_rate,
                                "breakeven_price": entry_price * (1.0 + 2 * _cost_rate),
                            },
                        )
                        await session.commit()
                        logger.info(
                            "confirm_position: inserted directly (reserve skipped)",
                            market_id=market_id, bot=which_bot, side=side, size=size,
                        )
                # S232 Bug 20: emit the trade_events audit row after the
                # positions write succeeds. Pre-fix the ONLY ENTRY writer
                # lived in paper_trading.py:993 (paper path), so live entries
                # never landed in trade_events — bot_pnl.py and every other
                # downstream consumer was blind to live trades. Now confirm_position
                # is the single source for both ENTRY (BUY) and EXIT (SELL)
                # audit rows, mirroring the paper/live split via execution_mode.
                # Non-fatal on failure: positions row is canonical, trade_events
                # is audit-only — we log and continue rather than retry the
                # whole confirm_position.
                try:
                    _exec_mode = "paper" if bool(getattr(settings, "SIMULATION_MODE", False)) else "live"
                    _evt_type = "EXIT" if _is_sell else "ENTRY"
                    _cost_rate_evt = (
                        getattr(settings, "FIXED_SLIPPAGE_BPS", 50)
                        + getattr(settings, "TAKER_FEE_BPS", 150)
                    ) / 10000.0
                    await self.db.insert_trade_event(
                        event_type=_evt_type,
                        bot_name=which_bot,
                        market_id=market_id,
                        side=side,
                        size=size,
                        price=entry_price,
                        execution_mode=_exec_mode,
                        token_id=token_id or None,
                        fees=size * entry_price * _cost_rate_evt,
                    )
                except Exception as _te_err:
                    logger.warning(
                        "confirm_position_trade_event_emit_failed",
                        market_id=market_id,
                        bot_id=which_bot,
                        side=side,
                        event_type=("EXIT" if _is_sell else "ENTRY"),
                        error=str(_te_err)[:200],
                    )
                return  # success
            except Exception as e:
                _last_err = e
                if _attempt < _MAX_PERSIST_ATTEMPTS - 1:
                    await asyncio.sleep(_RETRY_BACKOFF_S)
                    continue
        # S232 Bug 19: all retries exhausted — escalate to CRITICAL with
        # structured fields. In-memory state is authoritative; operator must
        # reconcile DB <-> on-chain manually until alerting bridge (Bug 18)
        # surfaces this automatically. LogMiner pattern catches this line.
        logger.critical(
            "confirm_position_persist_failed",
            market_id=market_id,
            bot_id=which_bot,
            side=side,
            size=size,
            entry_price=entry_price,
            is_sell=_is_sell,
            attempts=_MAX_PERSIST_ATTEMPTS,
            error=str(_last_err)[:300] if _last_err else "unknown",
            action="manual reconcile required: in-memory + on-chain position exists, DB row missing",
        )

    async def release_reservation(self, market_id: str, side: str, bot_id: Optional[str] = None) -> None:
        """Release reservation if trade failed. bot_id: which bot reserved (must match reserve)."""
        if not self.db.session_factory:
            return
        which_bot = bot_id if bot_id is not None else self.bot_id
        from sqlalchemy import delete
        try:
            async with self.db.get_session() as session:
                await session.execute(
                    delete(Position).where(
                        Position.bot_id == which_bot,
                        Position.market_id == market_id,
                        Position.side == side,
                        Position.status == "reserving",
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning("release_reservation failed: %s", e)

    async def release_all_reservations(self, bot_ids: Optional[Sequence[str]] = None) -> int:
        """
        Release all reserving positions (e.g. on graceful shutdown).
        When one process runs multiple bots (CryptoBot, PoliticalBot), pass bot_ids so all are released.
        bot_ids: if provided, release reserving positions for each of these bot_ids; otherwise release only self.bot_id.
        Returns number of reservations released.
        """
        if not self.db.session_factory:
            return 0
        from sqlalchemy import delete
        try:
            async with self.db.get_session() as session:
                if bot_ids:
                    result = await session.execute(
                        delete(Position).where(
                            Position.bot_id.in_(list(bot_ids)),
                            Position.status == "reserving",
                        )
                    )
                else:
                    result = await session.execute(
                        delete(Position).where(
                            Position.bot_id == self.bot_id,
                            Position.status == "reserving",
                        )
                    )
                await session.commit()
                n = result.rowcount or 0
                if n > 0:
                    logger.info(
                        "Released reservations",
                        count=n,
                        bot_ids=list(bot_ids) if bot_ids else [self.bot_id],
                    )
                return n
        except Exception as e:
            logger.warning("release_all_reservations failed: %s", e)
            return 0    # ── Stale Reservation Reaper ──

    async def reap_stale_reservations(self, max_age_minutes: int = STALE_RESERVATION_MINUTES) -> int:
        """
        Delete 'reserving' positions older than max_age_minutes.

        These are ghost reservations from crashed processes that reserved a position
        slot but never confirmed or released it. Without reaping, they permanently
        block future trades on that market.

        Returns:
            Number of stale reservations reaped.
        """
        if not self.db.session_factory:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        # Normalize to naive UTC for TIMESTAMP WITHOUT TIME ZONE columns
        cutoff_naive = cutoff.replace(tzinfo=None)
        try:
            async with self.db.get_raw_session() as session:
                # Theoretical hardening: wrap DELETE in timeout so a slow DB query
                # doesn't hold the raw session open indefinitely and stall bot scans.
                async def _do_reap():
                    return await session.execute(
                        text("""
                            DELETE FROM positions
                            WHERE status = 'reserving'
                              AND opened_at < :cutoff
                            RETURNING id, bot_id, market_id, side
                        """),
                        {"cutoff": cutoff_naive},
                    )
                result = await asyncio.wait_for(_do_reap(), timeout=30.0)
                reaped = result.fetchall()
                await session.commit()
                if reaped:
                    for row in reaped:
                        logger.warning(
                            "Reaped stale reservation",
                            position_id=row[0],
                            bot_id=row[1],
                            market_id=row[2],
                            side=row[3],
                            max_age_minutes=max_age_minutes,
                        )
                return len(reaped)
        except Exception as e:
            logger.warning("reap_stale_reservations failed", error=str(e))
            return 0

    async def start_reaper(self) -> None:
        """Start background task that periodically reaps stale reservations."""
        if self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        logger.info("Stale reservation reaper started", interval_seconds=REAPER_INTERVAL_SECONDS)

    async def stop_reaper(self) -> None:
        """Stop the background reaper task."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
            logger.info("Stale reservation reaper stopped")

    async def _reaper_loop(self) -> None:
        """Periodic reaper loop. Runs every REAPER_INTERVAL_SECONDS."""
        while True:
            try:
                await asyncio.sleep(REAPER_INTERVAL_SECONDS)
                reaped = await self.reap_stale_reservations()
                if reaped > 0:
                    logger.info("Reaper cycle complete", reaped_count=reaped)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Reaper cycle error", error=str(e))
                await asyncio.sleep(30)  # Back off on errors
