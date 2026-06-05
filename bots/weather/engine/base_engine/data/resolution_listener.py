"""
Resolution listener (#33) - know when markets resolve (API + optional on-chain).

Polls DB/API for newly resolved markets; optionally uses BlockchainClient
to check condition_id resolution on Polygon for instant detection.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional
from structlog import get_logger

logger = get_logger()


class ResolutionListener:
    """
    Notify when markets resolve: poll DB/API and optionally blockchain.

    Callback receives: { market_id, question, resolution, resolved_at }.
    """

    def __init__(
        self,
        db: Optional[Any] = None,
        blockchain_client: Optional[Any] = None,
        poll_interval_seconds: float = 30.0,
    ):
        self.db = db
        self.blockchain_client = blockchain_client
        self.poll_interval = poll_interval_seconds
        self._last_resolved_at: Optional[datetime] = None
        self._known_resolved_ids: set = set()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def _get_recently_resolved_from_db(self) -> List[Dict[str, Any]]:
        """Fetch markets that were resolved since last check (from DB)."""
        if not self.db or not self.db.session_factory:
            return []
        since = self._last_resolved_at or (datetime.now(timezone.utc) - timedelta(hours=24))
        from bots.weather.engine.base_engine.data.database import _naive_utc
        since_naive = _naive_utc(since) if getattr(since, "tzinfo", None) else since
        if getattr(since_naive, "tzinfo", None) is not None:
            since_naive = since_naive.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            from sqlalchemy import select, and_
            from bots.weather.engine.base_engine.data.database import Market
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(Market.id, Market.question, Market.resolution, Market.resolved_at)
                    .where(and_(Market.resolved == True, Market.resolved_at >= since_naive))
                )
                rows = result.all()
            return [
                {
                    "market_id": r.id,
                    "question": r.question,
                    "resolution": r.resolution or "YES",
                    "resolved_at": r.resolved_at,
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("resolution_listener db query failed: %s", e)
            return []

    async def _check_condition_on_chain(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """If blockchain client has check_market_resolution, use it."""
        if not self.blockchain_client or not condition_id:
            return None
        try:
            if hasattr(self.blockchain_client, "check_market_resolution"):
                out = await self.blockchain_client.check_market_resolution(condition_id)
                if out and out.get("resolved"):
                    return {"condition_id": condition_id, "outcome": out.get("outcome"), "payouts": out.get("payouts")}
        except Exception as e:
            logger.debug("resolution_listener blockchain check failed: %s", e)
        return None

    async def run_poll_loop(self, callback: Callable[[Dict[str, Any]], Any]) -> None:
        """
        Run forever: poll DB for newly resolved markets and invoke callback for each.
        """
        self._running = True
        while self._running:
            try:
                resolved_list = await self._get_recently_resolved_from_db()
                now = datetime.now(timezone.utc)
                for r in resolved_list:
                    mid = r.get("market_id")
                    if mid and mid not in self._known_resolved_ids:
                        self._known_resolved_ids.add(mid)
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(r)
                            else:
                                callback(r)
                        except Exception as e:
                            logger.warning("resolution_listener callback error: %s", e)
                self._last_resolved_at = now
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("resolution_listener poll error: %s", e)
            await asyncio.sleep(self.poll_interval)

    def start(self, callback: Callable[[Dict[str, Any]], Any]) -> asyncio.Task:
        """Start background poll loop. Returns task so caller can cancel."""
        self._task = asyncio.create_task(self.run_poll_loop(callback))
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()


class PGNotificationListener:
    """
    PG LISTEN/NOTIFY — Tier 4 #38.

    Real-time PostgreSQL triggers replacing polling. Uses asyncpg's built-in
    LISTEN/NOTIFY for zero-latency event delivery.

    Channels:
      - market_resolved: Fires when a market resolves
      - large_trade: Fires on trades >= $5K

    Setup SQL (run on VPS PostgreSQL — see deploy/pg_triggers.sql):

      CREATE OR REPLACE FUNCTION notify_market_resolved() RETURNS trigger AS $$
      BEGIN
        IF NEW.resolved IS TRUE AND (OLD.resolved IS NULL OR OLD.resolved IS FALSE) THEN
          PERFORM pg_notify('market_resolved', json_build_object(
            'market_id', NEW.id, 'condition_id', NEW.condition_id,
            'resolution', NEW.resolution, 'question', LEFT(NEW.question, 200)
          )::text);
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;

      CREATE TRIGGER trg_market_resolved
        AFTER UPDATE ON markets FOR EACH ROW
        EXECUTE FUNCTION notify_market_resolved();

    Usage:
        listener = PGNotificationListener(database_url)
        listener.on("market_resolved", handle_resolution)
        await listener.start()
    """

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._conn = None
        self._handlers: dict = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def on(self, channel: str, handler) -> None:
        """Register a handler for a NOTIFY channel."""
        self._handlers.setdefault(channel, []).append(handler)

    async def start(self) -> None:
        """Start listening on all registered channels."""
        if self._running:
            return
        try:
            import asyncpg
        except ImportError:
            logger.warning("asyncpg not available for LISTEN/NOTIFY")
            return

        url = self._database_url
        if url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql://")

        try:
            self._conn = await asyncpg.connect(url)
            self._running = True
            for channel in self._handlers:
                await self._conn.add_listener(channel, self._dispatch)
                logger.info("PG LISTEN registered", channel=channel)
            self._task = asyncio.create_task(self._keepalive())
            logger.info("PGNotificationListener started", channels=list(self._handlers.keys()))
        except Exception as e:
            logger.warning("PG LISTEN/NOTIFY connection failed: %s", e)
            self._running = False

    async def stop(self) -> None:
        """Stop listening and close connection."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            try:
                for channel in self._handlers:
                    await self._conn.remove_listener(channel, self._dispatch)
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _dispatch(self, conn, pid, channel: str, payload: str) -> None:
        """Dispatch notification to registered handlers."""
        import json as _json
        handlers = self._handlers.get(channel, [])
        if not handlers:
            return
        try:
            data = _json.loads(payload)
        except (ValueError, TypeError):
            data = {"raw": payload}
        def _handler_done_cb(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error("PG NOTIFY async handler failed",
                             channel=channel, error=str(exc))

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    _t = asyncio.create_task(handler(data))
                    _t.add_done_callback(_handler_done_cb)
                else:
                    handler(data)
            except Exception as e:
                logger.warning("PG NOTIFY handler error", channel=channel, error=str(e))

    async def _keepalive(self) -> None:
        """Periodic keepalive to prevent idle connection timeout."""
        while self._running:
            try:
                await asyncio.sleep(30)
                if self._conn and not self._conn.is_closed():
                    await self._conn.execute("SELECT 1")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("PG LISTEN keepalive failed: %s", e)
                break
