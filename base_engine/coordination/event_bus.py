"""
Event-Driven Architecture (#25) - real-time alerts and actions.

Register handlers for events (resolution, trade, anomaly); emit events to
webhooks and internal handlers. Trigger trades/alerts on specific market events.

Handlers run concurrently (asyncio.gather) with per-handler timeout to prevent
one slow handler from blocking the entire event pipeline.
"""
import asyncio
from typing import Any, Callable, Dict, List, Optional
from structlog import get_logger

logger = get_logger()

_DEFAULT_HANDLER_TIMEOUT = 10.0  # seconds; slow handlers are cancelled
_MAX_HANDLERS_PER_EVENT = 50     # prevent unbounded registration

# F19: High-frequency events dispatched to subscribers but NOT persisted to DB
_SKIP_PERSIST_EVENTS = {"price_update", "price_tick", "orderbook_update"}


class EventBus:
    """
    In-process event bus: register handlers, emit events.

    Events: market_resolved, big_trade, anomaly_detected, sync_failed, position_opened, etc.
    Handlers can be async callables; webhook_dispatcher can be registered for external push.

    Safety:
    - Handlers run concurrently via asyncio.gather (one slow handler doesn't block others)
    - Per-handler timeout (default 10s) prevents a hung handler from blocking the pipeline
    - Max handlers per event type (50) prevents unbounded registration
    """

    def __init__(self, handler_timeout: float = _DEFAULT_HANDLER_TIMEOUT):
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}
        self._once: Dict[str, List[Callable[..., Any]]] = {}
        self._handler_timeout = handler_timeout

    def on(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Register a handler for event_type (called every time)."""
        handlers = self._handlers.setdefault(event_type, [])
        if len(handlers) >= _MAX_HANDLERS_PER_EVENT:
            logger.warning("event_bus max handlers reached for %s, ignoring new handler", event_type)
            return
        handlers.append(handler)

    def once(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Register a one-time handler for event_type."""
        self._once.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: Optional[Callable[..., Any]] = None) -> None:
        """Remove handler(s) for event_type. If handler is None, remove all."""
        if handler is None:
            self._handlers.pop(event_type, None)
            self._once.pop(event_type, None)
            return
        if event_type in self._handlers:
            self._handlers[event_type] = [h for h in self._handlers[event_type] if h != handler]
        if event_type in self._once:
            self._once[event_type] = [h for h in self._once[event_type] if h != handler]

    async def _run_handler(self, handler: Callable, payload: Dict[str, Any], event_type: str) -> Any:
        """Run a single handler with timeout protection."""
        try:
            if asyncio.iscoroutinefunction(handler):
                return await asyncio.wait_for(handler(payload), timeout=self._handler_timeout)
            else:
                return handler(payload)
        except asyncio.TimeoutError:
            _name = getattr(handler, "__name__", repr(handler))
            logger.warning("event_bus handler timed out", event=event_type, handler=_name,
                           timeout=self._handler_timeout)
            return TimeoutError(f"Handler {_name} timed out after {self._handler_timeout}s")
        except Exception as e:
            logger.warning("event_bus handler error", event=event_type, error=str(e))
            return e

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> List[Any]:
        """
        Emit event to all registered handlers concurrently.
        Returns list of handler results (or exceptions for failed/timed-out handlers).
        """
        all_handlers = self._handlers.get(event_type, []) + self._once.get(event_type, [])
        if not all_handlers:
            return []

        # Run all handlers concurrently with individual timeout protection
        results = await asyncio.gather(
            *(self._run_handler(h, payload, event_type) for h in all_handlers),
            return_exceptions=True,
        )

        if event_type in self._once:
            self._once[event_type] = []
        return list(results)

    def emit_sync(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Schedule emit in the running event loop (fire-and-forget)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit(event_type, payload))
        except RuntimeError:
            pass
        return None


class EventSourcingBus(EventBus):
    """
    Append-only event sourcing bus — Tier 4 #42.

    Extends EventBus with persistent logging to a PostgreSQL
    decision_events table. Every emitted event is recorded for
    full audit trail and replay capability.

    Table SQL (run on VPS):
      CREATE TABLE IF NOT EXISTS decision_events (
          id BIGSERIAL PRIMARY KEY,
          event_type TEXT NOT NULL,
          payload JSONB NOT NULL DEFAULT '{}',
          correlation_id TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      );
      CREATE INDEX idx_decision_events_type ON decision_events (event_type);
      CREATE INDEX idx_decision_events_created ON decision_events (created_at);
      CREATE INDEX idx_decision_events_corr ON decision_events (correlation_id)
          WHERE correlation_id IS NOT NULL;
    """

    def __init__(self, db=None, handler_timeout: float = _DEFAULT_HANDLER_TIMEOUT):
        super().__init__(handler_timeout=handler_timeout)
        self._db = db

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> List[Any]:
        """Emit event: persist to DB, then dispatch to handlers."""
        # Fire-and-forget persist (don't block handler dispatch)
        # F19: Skip persisting high-frequency events to avoid DB bloat
        if self._db and self._db.session_factory and event_type not in _SKIP_PERSIST_EVENTS:
            asyncio.create_task(self._persist_event(event_type, payload))

        return await super().emit(event_type, payload)

    async def _persist_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Append event to decision_events table."""
        try:
            import json
            async with self._db.get_session() as session:
                from sqlalchemy import text
                await session.execute(
                    text("""
                        INSERT INTO decision_events (event_type, payload, correlation_id)
                        VALUES (:event_type, :payload, :correlation_id)
                    """),
                    {
                        "event_type": event_type,
                        "payload": json.dumps(payload, default=str),
                        "correlation_id": payload.get("correlation_id"),
                    },
                )
                await session.commit()
        except Exception as e:
            logger.debug("Event sourcing persist failed: %s", e)

    async def _retention_cleanup(self, retain_days: int = 30) -> None:
        """Delete decision_events rows older than retain_days."""
        if not self._db or not self._db.session_factory:
            return
        try:
            async with self._db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(
                    text("""
                        DELETE FROM decision_events
                        WHERE created_at < NOW() - INTERVAL '1 day' * :retain_days
                    """),
                    {"retain_days": retain_days},
                )
                count = result.rowcount
                await session.commit()
            logger.info("event_log_cleanup", deleted=count, retain_days=retain_days)
        except Exception as e:
            logger.warning("event_log_cleanup_failed", error=str(e))

    async def replay_events(
        self,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Replay events from the decision_events table.

        Args:
            event_type: Filter by event type (None = all)
            since: ISO timestamp to replay from
            limit: Max events to return

        Returns:
            List of event dicts
        """
        if not self._db or not self._db.session_factory:
            return []

        try:
            async with self._db.get_session() as session:
                from sqlalchemy import text
                query = "SELECT event_type, payload, correlation_id, created_at FROM decision_events"
                params: Dict[str, Any] = {"limit": limit}
                conditions = []

                if event_type:
                    conditions.append("event_type = :event_type")
                    params["event_type"] = event_type
                if since:
                    conditions.append("created_at >= :since")
                    params["since"] = since

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                query += " ORDER BY created_at ASC LIMIT :limit"

                result = await session.execute(text(query), params)
                rows = result.fetchall()
                return [
                    {
                        "event_type": r[0],
                        "payload": r[1],
                        "correlation_id": r[2],
                        "created_at": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.debug("Event replay failed: %s", e)
            return []
