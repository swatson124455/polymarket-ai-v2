"""
StreamingPersister - Persist real-time WebSocket trade/price events to the database.
Batches events and calls db.bulk_insert_trades / bulk_insert_prices_raw to avoid one-row-per-commit.
Register with WebSocketManager (on_trade / on_price callbacks) or subscribe to Redis channels.
"""
import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from base_engine.data.database import _naive_utc
from base_engine.data.database_partitioning import get_partition_key
from structlog import get_logger

logger = get_logger()

BATCH_SIZE = 50
FLUSH_INTERVAL_SEC = 10.0


class StreamingPersister:
    """
    Queues trade and price updates from WebSocket; flushes to DB in batches.
    Use register_with_websocket(ws_manager) to attach to WebSocketManager handlers.
    """

    # High-watermark thresholds: trigger an early flush when queues exceed these sizes
    # rather than waiting the full FLUSH_INTERVAL_SEC (10s).  During high-volume periods
    # (1000+ trades/min) this keeps price data ≤1-2s stale instead of ≤10s stale.
    _TRADE_HWM = 200
    _PRICE_HWM = 500

    def __init__(self, db: Any):
        self.db = db
        self._trade_queue: deque = deque()
        self._price_queue: deque = deque()
        # Split locks: trade and price flushes are independent.
        # Previously a single lock serialised ALL flushes; now trade and price can be
        # dequeued concurrently so a slow trade DB insert doesn't block price flush.
        self._trade_lock = asyncio.Lock()
        self._price_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        # Phase 5: fast-path whale hook — set by base_engine after both objects are initialized
        self._whale_callback: Optional[Any] = None
        self._whale_threshold_usd: float = 10000.0
        # resolve_market_ids_batch cache: condition_id -> (market_id, timestamp)
        self._resolve_cache: Dict[str, tuple] = {}
        self._RESOLVE_CACHE_TTL: float = 300.0  # 5 minutes

    async def start(self) -> None:
        """Start background flush task."""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        # C5 FIX: Register error callback so crash in _flush_loop surfaces as ERROR
        # rather than disappearing silently. Without this, the loop dies and WS price/trade
        # queues grow unbounded in memory with no log entry and no auto-restart.
        self._flush_task.add_done_callback(self._on_flush_task_done)
        logger.info("StreamingPersister started")

    def _on_flush_task_done(self, task: asyncio.Task) -> None:
        """C5 FIX: Log unexpected flush task exit and auto-restart if still running."""
        if task.cancelled():
            return  # Normal stop via stop()
        try:
            exc = task.exception()
        except Exception:
            exc = None
        if exc is not None:
            _restart_count = getattr(self, "_flush_restart_count", 0) + 1
            self._flush_restart_count = _restart_count
            _delay = min(1.0 * (2 ** (_restart_count - 1)), 30.0)  # 1, 2, 4, 8, 16, 30s cap
            logger.error(
                "StreamingPersister flush loop crashed (attempt %d, restart in %.0fs): %s",
                _restart_count, _delay, exc, exc_info=exc
            )
            if self._running:
                async def _delayed_restart(delay: float) -> None:
                    await asyncio.sleep(delay)
                    if self._running:
                        self._flush_task = asyncio.create_task(self._flush_loop())
                        self._flush_task.add_done_callback(self._on_flush_task_done)
                asyncio.create_task(_delayed_restart(_delay))

    async def stop(self) -> None:
        """Stop and flush remaining."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        await self._flush()
        logger.info("StreamingPersister stopped")

    async def on_trade(self, data: Dict[str, Any]) -> None:
        """
        Call from WebSocketManager (register as handler for trade/last_trade_price). Normalizes payload and enqueues.
        Polymarket last_trade_price: asset_id, market, price, size (strings).
        """
        try:
            market_id = str(data.get("market_id") or data.get("market") or "")
            token_id = str(data.get("token_id") or data.get("asset_id") or "")
            price = float(data.get("price") or 0)
            size = float(data.get("size") or 0)
        except (TypeError, ValueError):
            return
        if not market_id and not token_id:
            return
        ts = datetime.now(timezone.utc)
        trade_id = f"ws_{int(ts.timestamp() * 1000)}_{market_id}_{token_id}_{id(data) % 100000}"
        record = {
            "id": trade_id,
            "market_id": market_id or None,
            "token_id": token_id or None,
            "user_address": str(data.get("user_address") or data.get("maker") or ""),
            "bot_id": None,
            "side": str(data.get("side") or "BUY"),
            "size": size,
            "price": price,
            "pnl": None,
            "entry_time": None,
            "exit_time": None,
            "timestamp": _naive_utc(ts),
            "partition_month": get_partition_key(_naive_utc(ts)),
        }
        self._trade_queue.append(record)

        # Phase 5 fast-path: fire whale callback <1ms after WebSocket tick (no DB round-trip)
        if self._whale_callback is not None and size * price >= self._whale_threshold_usd:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._whale_callback(dict(record)))
            except RuntimeError:
                pass

    def on_price_change(self, market_id: Optional[str], token_id: str, price: float) -> None:
        """Call from WebSocketManager _handle_price_change. Enqueues one price row."""
        if not token_id:
            return
        ts = datetime.now(timezone.utc)
        ts_naive = _naive_utc(ts)
        record = {
            "market_id": market_id or "",
            "token_id": token_id,
            "price": price,
            "side": None,
            "timestamp": ts_naive,
            "partition_month": get_partition_key(ts_naive),
        }
        self._price_queue.append(record)

    async def _flush(self) -> None:
        """Flush trade and price queues to DB using independent locks per queue."""
        async with self._trade_lock:
            trades: List[Dict[str, Any]] = []
            while self._trade_queue and len(trades) < BATCH_SIZE * 2:
                trades.append(self._trade_queue.popleft())
        async with self._price_lock:
            prices: List[Dict[str, Any]] = []
            while self._price_queue and len(prices) < BATCH_SIZE * 2:
                prices.append(self._price_queue.popleft())

        if trades and self.db and self.db.session_factory:
            try:
                from base_engine.data.id_resolver import resolve_market_ids_batch
                raw_ids = list({t.get("market_id") for t in trades if t.get("market_id")})
                # Use cache: split into cached hits and uncached misses
                now = time.time()
                cached = {cid: mid for cid, (mid, ts) in self._resolve_cache.items()
                          if now - ts < self._RESOLVE_CACHE_TTL and cid in raw_ids}
                uncached = [cid for cid in raw_ids if cid not in cached]
                resolved = dict(cached)
                if uncached:
                    db_results = await resolve_market_ids_batch(self.db, uncached)
                    for cid, mid in db_results.items():
                        self._resolve_cache[cid] = (mid, now)
                    resolved.update(db_results)
                for t in trades:
                    rid = t.get("market_id")
                    if rid and rid in resolved:
                        t["market_id"] = resolved[rid]
                await self.db.bulk_insert_trades(trades)
                logger.debug("StreamingPersister flushed %s trades", len(trades))
            except Exception as e:
                logger.warning("StreamingPersister bulk_insert_trades failed: %s", e)
                for t in trades:
                    self._trade_queue.append(t)
        if prices and self.db and hasattr(self.db, "bulk_insert_prices_raw"):
            try:
                # Resolve price market_ids from condition_id (WS format) → numeric (matches trades)
                # Mirrors the same resolution applied to trades above to keep market_prices consistent
                if self.db.session_factory:
                    try:
                        from base_engine.data.id_resolver import resolve_market_ids_batch
                        _raw_price_ids = list({p.get("market_id") for p in prices if p.get("market_id")})
                        if _raw_price_ids:
                            # Use cache: split into cached hits and uncached misses
                            _now = time.time()
                            _cached_p = {cid: mid for cid, (mid, ts) in self._resolve_cache.items()
                                         if _now - ts < self._RESOLVE_CACHE_TTL and cid in _raw_price_ids}
                            _uncached_p = [cid for cid in _raw_price_ids if cid not in _cached_p]
                            _resolved_p = dict(_cached_p)
                            if _uncached_p:
                                _db_res = await resolve_market_ids_batch(self.db, _uncached_p)
                                for cid, mid in _db_res.items():
                                    self._resolve_cache[cid] = (mid, _now)
                                _resolved_p.update(_db_res)
                            for p in prices:
                                _rid = p.get("market_id")
                                if _rid and _rid in _resolved_p:
                                    p["market_id"] = _resolved_p[_rid]
                    except Exception as _e:
                        logger.debug("Price market_id resolution failed (non-fatal): %s", _e)
                await self.db.bulk_insert_prices_raw(prices, batch_size=BATCH_SIZE)
                logger.debug("StreamingPersister flushed %s prices", len(prices))
            except Exception as e:
                logger.warning("StreamingPersister bulk_insert_prices_raw failed: %s", e)
                for p in prices:
                    self._price_queue.append(p)

    async def _flush_loop(self) -> None:
        """Periodically flush queues; also flushes early when queues hit high-watermark."""
        # Reset restart counter — we've successfully started so next crash restarts with 1s delay
        self._flush_restart_count = 0
        last = time.monotonic()
        while self._running:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            # Trigger flush if interval elapsed OR if either queue has grown large
            # (high-watermark flush: prevents 10s stale data during volume spikes).
            _high_water = (
                len(self._trade_queue) > self._TRADE_HWM
                or len(self._price_queue) > self._PRICE_HWM
            )
            if _high_water or now - last >= FLUSH_INTERVAL_SEC:
                last = now
                await self._flush()

    def register_with_websocket(self, ws_manager: Any) -> None:
        """
        Register our on_trade and on_price with WebSocketManager.
        WebSocketManager calls registered handlers for event_type (trade, last_trade_price, price_change).
        """
        if not ws_manager:
            return
        if hasattr(ws_manager, "register_handler"):
            ws_manager.register_handler("trade", self.on_trade)
            ws_manager.register_handler("last_trade_price", self.on_trade)
            ws_manager.register_handler("price_change", self._on_price_change_handler)
            logger.info("StreamingPersister registered with WebSocketManager")

    async def _on_price_change_handler(self, data: Dict[str, Any]) -> None:
        """Adapter for price_change event (may have price_changes array or single token/price)."""
        price_changes = data.get("price_changes") or []
        if isinstance(price_changes, list):
            for pc in price_changes:
                token_id = pc.get("token_id") or pc.get("asset_id")
                price = pc.get("best_bid") or pc.get("best_ask") or pc.get("price")
                if token_id is not None and price is not None:
                    self.on_price_change(
                        data.get("market_id") or data.get("market"),
                        str(token_id),
                        float(price),
                    )
        token_id = data.get("token_id") or data.get("asset_id")
        price = data.get("price")
        if token_id is not None and price is not None:
            self.on_price_change(
                data.get("market_id") or data.get("market"),
                str(token_id),
                float(price),
            )
