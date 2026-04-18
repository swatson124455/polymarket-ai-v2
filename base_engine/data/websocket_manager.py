"""
WebSocket Manager - Real-time data via Polymarket WebSocket.

Features:
- Real-time price updates
- Order book changes
- Trade notifications
- Whale alerts
"""
import asyncio
import json
import os
import time
import websockets
import websockets.exceptions  # explicit import required — websockets v15 lazy-loads submodules
from base_engine.data.json_parse import loads as json_loads
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from structlog import get_logger
from base_engine.data.redis_cache import RedisCache

logger = get_logger()

# S182 Commit 4: MB WS heartbeat-driven force-reconnect. MB observed 44 closes vs 43
# reconnects over 6h — one stuck disconnected state where ws.recv() hangs instead of
# raising ConnectionClosed. Heartbeat monitor checks time-since-last-message periodically
# and force-closes a stale ws so the message loop's existing reconnect handler fires.
# Fail-closed default (flag off). Only opted-in via .env.mirror per the scope decision
# in S182 plan Commit 4. WB/EB unchanged pending WS disconnect-storm root-cause work.
_WS_HEARTBEAT_ENABLED = os.getenv("WS_HEARTBEAT_RECONNECT_ENABLED", "false").lower() in ("true", "1", "yes")
_WS_HEARTBEAT_TIMEOUT_S = float(os.getenv("WS_HEARTBEAT_TIMEOUT_S", "120"))
_WS_HEARTBEAT_CHECK_INTERVAL_S = float(os.getenv("WS_HEARTBEAT_CHECK_INTERVAL_S", "60"))


class WebSocketManager:
    """
    Real-time data via Polymarket WebSocket.
    Feeds bots with live updates (direct connection).
    """

    def __init__(
        self,
        cache: RedisCache,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        whale_threshold_usd: float = 10000.0,
        event_bus: Optional[Any] = None,
        market_index_resolver: Optional[Any] = None,
        alerting: Optional[Any] = None,
        on_reconnect: Optional[Callable] = None,
    ):
        self.cache = cache
        self.ws_url = ws_url
        self.whale_threshold_usd = whale_threshold_usd
        self.event_bus = event_bus
        # I49: callable(market_id_str) -> market_dict | None — resolves condition_id to numeric id
        self._market_index_resolver = market_index_resolver
        self._alerting = alerting  # Session 51: AlertingSystem for circuit breaker alerts
        # Optional async callback fired after successful reconnect — use for REST price resync.
        self._on_reconnect: Optional[Callable] = on_reconnect
        self.ws = None
        self.subscriptions = set()
        self.handlers: Dict[str, List[Callable]] = {}
        self.running = False
        self.message_loop_task = None
        # S182 Commit 4: heartbeat state. Always initialized; task only starts
        # if _WS_HEARTBEAT_ENABLED. _last_message_ts gets refreshed on every
        # successful ws.recv() in _message_loop so the heartbeat can measure silence.
        self._last_message_ts: float = time.monotonic()
        self._heartbeat_task: Optional[asyncio.Task] = None
    
    def _connect_kwargs(self) -> Dict[str, Any]:
        """Build kwargs for websockets.connect (ping settings)."""
        kwargs: Dict[str, Any] = {
            "ping_interval": 30,
            "ping_timeout": 10,
        }
        return kwargs

    async def connect(self):
        """Establish WebSocket connection. Polymarket requires /ws/market path and initial subscribe message."""
        try:
            self.running = True
            self.ws = await websockets.connect(
                self.ws_url,
                **self._connect_kwargs(),
            )
            # Polymarket market channel: send type and assets_ids on connect (empty; subscribe via operation later)
            await self.ws.send(json.dumps({"type": "market", "assets_ids": []}))
            # H3 FIX: Cancel any existing message loop before creating a new one.
            # If connect() is called while a previous loop task is still alive, two tasks
            # would process messages concurrently → duplicate price updates, duplicate DB inserts.
            if self.message_loop_task and not self.message_loop_task.done():
                self.message_loop_task.cancel()
                try:
                    await self.message_loop_task
                except asyncio.CancelledError:
                    pass
            self.message_loop_task = asyncio.create_task(self._message_loop())
            # S182 Commit 4: (re)start heartbeat only when opted-in via env flag.
            # Opt-in set in .env.mirror only; WB/EB run with flag off.
            if _WS_HEARTBEAT_ENABLED:
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._last_message_ts = time.monotonic()  # reset stall clock at connect
                self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())
            logger.info("WebSocket connected")
        except Exception as e:
            logger.error(f"WebSocket connection failed: {str(e)}", exc_info=True)
            raise

    async def _reconnect(self) -> bool:
        """
        Replace the socket and re-send initial message (and re-subscribe). Does NOT start a new _message_loop.
        Call this from inside _message_loop on ConnectionClosed so only one recv() caller exists.
        Returns True if reconnected, False otherwise.
        """
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        try:
            self.ws = await websockets.connect(
                self.ws_url,
                **self._connect_kwargs(),
            )
            await self.ws.send(json.dumps({"type": "market", "assets_ids": []}))
            # Re-subscribe all; Polymarket may require asset_ids again
            for sub_key in list(self.subscriptions):
                if sub_key.startswith("orderbook:"):
                    token_id = sub_key.replace("orderbook:", "")
                    await self.ws.send(json.dumps({"assets_ids": [token_id], "operation": "subscribe"}))
                elif sub_key.startswith("price:"):
                    token_id = sub_key.replace("price:", "")
                    await self.ws.send(json.dumps({"assets_ids": [token_id], "operation": "subscribe"}))
            logger.info("WebSocket reconnected")
            if self._on_reconnect is not None:
                try:
                    await self._on_reconnect()
                except Exception as _cb_exc:
                    logger.warning("ws_reconnect_callback_failed", error=str(_cb_exc))
            return True
        except Exception as e:
            logger.error("WebSocket reconnect failed: %s", e)
            return False

    async def disconnect(self):
        """Close WebSocket connection."""
        self.running = False
        # S182 Commit 4: cancel heartbeat before message loop so it doesn't try
        # to force-close a ws that's already being shut down cleanly.
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.message_loop_task:
            self.message_loop_task.cancel()
            try:
                await self.message_loop_task
            except asyncio.CancelledError:
                pass

        if self.ws:
            await self.ws.close()
            logger.info("WebSocket disconnected")

    async def _heartbeat_monitor(self):
        """S182 Commit 4: force-reconnect on prolonged ws silence.

        Runs as a background task. Every _WS_HEARTBEAT_CHECK_INTERVAL_S (default 60s),
        measures time since last successful ws.recv(). If silence exceeds
        _WS_HEARTBEAT_TIMEOUT_S (default 120s), closes the ws so the _message_loop's
        ConnectionClosed handler reconnects. Avoids the "stuck disconnected" state
        where ws.recv() hangs forever without raising (MB observed 44 closes vs
        43 reconnects over 6h).
        """
        while self.running:
            try:
                await asyncio.sleep(_WS_HEARTBEAT_CHECK_INTERVAL_S)
                if not self.running or not self.ws:
                    continue
                silence_s = time.monotonic() - self._last_message_ts
                if silence_s > _WS_HEARTBEAT_TIMEOUT_S:
                    logger.warning(
                        "ws_heartbeat_stale_force_reconnect",
                        silence_s=round(silence_s, 1),
                        threshold_s=_WS_HEARTBEAT_TIMEOUT_S,
                    )
                    try:
                        await self.ws.close()
                    except Exception as _e:
                        logger.debug("ws_heartbeat_close_failed", error=str(_e))
                    # Reset the clock so we don't spam force-closes while the
                    # message loop reconnects — next natural message will re-reset.
                    self._last_message_ts = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("ws_heartbeat_monitor_error", error=str(e))
    
    async def subscribe_market(self, market_id: str, token_id: str):
        """Subscribe to market updates."""
        if not self.ws:
            await self.connect()
        
        subscription_key = f"orderbook:{token_id}"
        
        if subscription_key not in self.subscriptions:
            try:
                await self.ws.send(json.dumps({"assets_ids": [token_id], "operation": "subscribe"}))
                self.subscriptions.add(subscription_key)
                logger.debug(f"Subscribed to market {market_id} (token {token_id})")
            except Exception as e:
                logger.error(f"Subscription failed: {str(e)}", exc_info=True)
    
    def register_handler(self, event_type: str, handler: Callable):
        """Register handler for specific event type."""
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)
    
    async def _message_loop(self):
        """Process incoming WebSocket messages with exponential backoff on reconnection."""
        _reconnect_attempts = 0
        _MAX_BACKOFF = 60  # seconds
        while self.running:
            try:
                if not self.ws:
                    await asyncio.sleep(1)
                    continue
                
                message = await self.ws.recv()
                _ws_recv_t = time.monotonic()  # High-res receipt timestamp for signal latency
                # S182 Commit 4: refresh heartbeat anchor on every successful recv
                # so _heartbeat_monitor measures real silence, not stale reads.
                self._last_message_ts = _ws_recv_t
                data = json_loads(message)

                # Polymarket may send arrays of events; process each element
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            await self._dispatch_message(item, _ws_recv_t=_ws_recv_t)
                elif isinstance(data, dict):
                    await self._dispatch_message(data, _ws_recv_t=_ws_recv_t)
                
            except websockets.exceptions.ConnectionClosed:
                _reconnect_attempts += 1
                _backoff = min(2 ** _reconnect_attempts, _MAX_BACKOFF)
                # S145: Reduced from 10→5 for faster detection (~62s vs ~362s).
                # Same 300s backoff after trip — no reconnect storm risk.
                _MAX_BEFORE_CRITICAL = 5
                if _reconnect_attempts == _MAX_BEFORE_CRITICAL:
                    logger.critical(
                        "WebSocket: %d consecutive reconnect failures — network may be permanently down. "
                        "Will continue retrying every 5 minutes.",
                        _reconnect_attempts,
                    )
                    # Session 51 P1-3: Alert on WebSocket circuit breaker
                    if self._alerting:
                        try:
                            from base_engine.monitoring.alerting import AlertSeverity
                            _t = asyncio.create_task(self._alerting.send_alert(
                                title="WebSocket circuit breaker tripped",
                                message=f"{_reconnect_attempts} consecutive reconnect failures. Network may be down.",
                                severity=AlertSeverity.CRITICAL,
                                source="websocket_manager",
                            ))
                            _t.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                        except Exception:
                            pass
                if _reconnect_attempts > _MAX_BEFORE_CRITICAL:
                    _backoff = 300  # 5 min after circuit breaker trips
                logger.warning("WebSocket connection closed, reconnect attempt %d (backoff %ds)...",
                               _reconnect_attempts, _backoff)
                await asyncio.sleep(_backoff)
                ok = await self._reconnect()
                if ok:
                    _reconnect_attempts = 0  # Reset on success
            except Exception as e:
                if isinstance(e, websockets.exceptions.ConcurrencyError):
                    # Another coroutine already in recv (e.g. duplicate loop); reconnect so only this loop owns ws
                    logger.warning("WebSocket recv concurrency error, reconnecting...")
                    await asyncio.sleep(min(2 ** _reconnect_attempts, _MAX_BACKOFF))
                    await self._reconnect()
                    _reconnect_attempts = 0
                else:
                    logger.error("WebSocket message error: %s", e, exc_info=True)
                    await asyncio.sleep(1)
    
    async def _dispatch_message(self, data: Dict[str, Any], _ws_recv_t: float = 0.0) -> None:
        """Route a single WebSocket message to the appropriate handler."""
        event_type = data.get("event_type") or data.get("type")
        if event_type == "book" or event_type == "orderbook_update":
            await self._handle_orderbook_update(data)
        elif event_type == "last_trade_price" or event_type == "trade":
            await self._handle_trade(data)
        elif event_type == "price_change":
            await self._handle_price_change(data, _ws_recv_t=_ws_recv_t)
        # Invoke registered handlers (StreamingPersister, bots via EventBus)
        if event_type and event_type in self.handlers:
            for handler in self.handlers[event_type]:
                try:
                    await handler(data)
                except Exception as e:
                    logger.error("Handler error: %s", str(e), exc_info=True)

    async def subscribe_price_stream(self, token_ids: List[str]):
        """Subscribe to real-time price streaming for multiple tokens."""
        if not self.ws:
            await self.connect()
        
        for token_id in token_ids:
            subscription_key = f"price:{token_id}"
            
            if subscription_key not in self.subscriptions:
                try:
                    await self.ws.send(json.dumps({"assets_ids": [token_id], "operation": "subscribe"}))
                    self.subscriptions.add(subscription_key)
                    logger.debug(f"Subscribed to price stream for token {token_id}")
                except Exception as e:
                    logger.error(f"Price stream subscription failed: {str(e)}", exc_info=True)
    
    def prune_stale_subscriptions(self, active_token_ids: set) -> int:
        """Remove subscriptions for tokens no longer in any bot's active set.
        Call once per scan cycle (~10-30 min) as primary cleanup trigger.
        Returns count of pruned subscriptions."""
        _before = len(self.subscriptions)
        _active_keys = {f"orderbook:{tid}" for tid in active_token_ids} | {f"price:{tid}" for tid in active_token_ids}
        self.subscriptions = self.subscriptions & _active_keys
        _pruned = _before - len(self.subscriptions)
        if _pruned > 0:
            logger.info("ws_subscriptions_pruned", pruned=_pruned, remaining=len(self.subscriptions))
        # Safety cap: if prune hasn't run or active set is huge, cap at 5000
        if len(self.subscriptions) > 5000:
            _sorted = sorted(self.subscriptions)
            self.subscriptions = set(_sorted[len(_sorted) // 2:])
            logger.warning("ws_subscriptions_capped", remaining=len(self.subscriptions))
        return _pruned

    async def get_latest_price(self, token_id: str) -> Optional[float]:
        """Get latest price from cache (updated via WebSocket)."""
        if self.cache.redis:
            price = await self.cache.get(f"prices:{token_id}:live")
            if price:
                return float(price)
        return None
    
    def _resolve_market_id(self, raw_id: Optional[str]) -> Optional[str]:
        """I49: Resolve WS condition_id (0x… hex) to numeric market id using the market index.

        Polymarket WS sends `market` field as either a numeric string OR a 0x condition_id hash.
        If the downstream code expects numeric IDs, unresolved condition_ids cause missed lookups.
        """
        if not raw_id:
            return raw_id
        if self._market_index_resolver is not None:
            market_dict = self._market_index_resolver(str(raw_id))
            if market_dict:
                numeric = market_dict.get("id")
                if numeric is not None and str(numeric) != str(raw_id):
                    return str(numeric)
        return raw_id

    async def _handle_price_change(self, data: Dict[str, Any], _ws_recv_t: float = 0.0):
        """Handle real-time price changes. Polymarket sends market + price_changes[] with asset_id, best_bid, best_ask."""
        _raw_market_id = data.get("market_id") or data.get("market")
        # I49: resolve condition_id → numeric market id so downstream lookups succeed
        market_id = self._resolve_market_id(_raw_market_id)
        price_changes = data.get("price_changes") or []
        for pc in price_changes if isinstance(price_changes, list) else []:
            token_id = pc.get("token_id") or pc.get("asset_id")
            _bid = pc.get("best_bid")
            _ask = pc.get("best_ask")
            new_price = _bid if _bid is not None else (_ask if _ask is not None else pc.get("price"))
            if token_id and new_price is not None:
                await self._handle_price_change_one(market_id, token_id, float(new_price), _ws_recv_t=_ws_recv_t)
        token_id = data.get("token_id") or data.get("asset_id")
        new_price = data.get("price")
        if token_id and new_price is not None:
            await self._handle_price_change_one(market_id, token_id, float(new_price), _ws_recv_t=_ws_recv_t)

    async def _handle_price_change_one(self, market_id: Optional[str], token_id: str, new_price: float, _ws_recv_t: float = 0.0):
        """Single price update for cache, EventBus (Phase 4), and Redis publish.

        S178 7A: Stage-level latency instrumentation. The >3s signal_ms latency
        observed post-deploy is investigated here. Redis ops await inline and
        can delay EventBus task scheduling; this measurement identifies whether
        Redis round-trips or EventBus scheduling dominate.
        """
        _t_start = time.monotonic()
        if self.event_bus:
            try:
                payload = {
                    "market_id": market_id,
                    "token_id": token_id,
                    "price": new_price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "_ws_recv_t": _ws_recv_t,  # monotonic timestamp for signal latency calculation
                }
                self.event_bus.emit_sync("price_update", payload)
            except Exception as e:
                logger.debug("EventBus price_update emit failed: %s", e)
        _t_after_bus = time.monotonic()
        if self.cache and self.cache.redis:
            try:
                # S181 7A: batch 3 Redis ops (SET, SET, PUBLISH) into a single
                # pipeline round-trip. Measured before: redis_ops_ms ~= 328ms on
                # every dispatch (3 sequential awaits). Expected after: <100ms.
                # Bare pipeline() is transactional by default in redis-py 5.x
                # (transaction=True → MULTI/EXEC wrap). Readers don't need cross-
                # key atomicity (both SETs hold the same new_price under different
                # keys; consumers query one key at a time) — but the default
                # transactional wrap is stronger than needed, harmless, and
                # matches codebase convention (signal_ingestion.py:707,
                # whale_tracker.py:303). Values must be JSON-encoded here since
                # pipe.set bypasses self.cache.set's implicit json.dumps.
                _price_json = json.dumps(new_price)
                _publish_payload = json.dumps({
                    "market_id": market_id,
                    "token_id": token_id,
                    "price": new_price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                pipe = self.cache.redis.pipeline()
                pipe.set(f"prices:{token_id}:live", _price_json, ex=60)
                if market_id:
                    pipe.set(f"prices:{market_id}:live", _price_json, ex=60)
                pipe.publish(f"price_updates:{market_id or ''}", _publish_payload)
                await pipe.execute()
            except Exception as e:
                logger.debug("Redis price cache/publish failed (non-fatal): %s", e)
        _t_end = time.monotonic()

        # S178 7A: Log if any stage is slow (>50ms for stage, >200ms end-to-end).
        # Sampling via dedup processor — same-level events dedup at 60s window.
        _dispatch_ms = (_t_end - _ws_recv_t) * 1000 if _ws_recv_t > 0 else 0.0
        _redis_ms = (_t_end - _t_after_bus) * 1000
        _bus_ms = (_t_after_bus - _t_start) * 1000
        if _dispatch_ms > 200 or _redis_ms > 50:
            logger.info(
                "ws_price_dispatch_stages",
                dispatch_ms=round(_dispatch_ms, 1),
                bus_emit_ms=round(_bus_ms, 1),
                redis_ops_ms=round(_redis_ms, 1),
                token_id=(token_id[:16] if token_id else None),
            )

    async def _handle_trade(self, data: Dict[str, Any]):
        """Handle real-time trades. Polymarket last_trade_price: asset_id, market, price, size (strings)."""
        try:
            trade_size = float(data.get("size") or 0)
            trade_price = float(data.get("price") or 0)
        except (TypeError, ValueError):
            return
        value_usd = trade_size * trade_price
        
        # Check if large trade (whale alert)
        if value_usd >= self.whale_threshold_usd:
            if self.cache and self.cache.redis:
                try:
                    await self.cache.redis.publish(
                        "whale_alerts",
                        json.dumps({
                            **data,
                            "value_usd": value_usd,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    )
                except Exception as e:
                    logger.debug("Redis whale alert publish failed (non-fatal): %s", e)
            
            logger.info(
                f"Whale trade detected: {value_usd} USD",
                market_id=data.get("market_id") or data.get("market"),
                size=trade_size
            )

    async def _handle_orderbook_update(self, data: Dict[str, Any]):
        """Handle order book updates. Polymarket book: asset_id, market, bids, asks."""
        market_id = data.get("market_id") or data.get("market")
        token_id = data.get("token_id") or data.get("asset_id")

        if not token_id:
            return
        if self.cache and self.cache.redis:
            try:
                await self.cache.set(f"orderbook:{token_id}", data, ttl=60)
                await self.cache.redis.publish(
                    f"orderbook_updates:{market_id or ''}",
                    json.dumps(data)
                )
            except Exception as e:
                logger.debug("Redis orderbook cache/publish failed (non-fatal): %s", e)
