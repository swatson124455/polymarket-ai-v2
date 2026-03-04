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
import time
import websockets
import websockets.exceptions  # explicit import required — websockets v15 lazy-loads submodules
from base_engine.data.json_parse import loads as json_loads
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from structlog import get_logger
from base_engine.data.redis_cache import RedisCache

logger = get_logger()


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
    ):
        self.cache = cache
        self.ws_url = ws_url
        self.whale_threshold_usd = whale_threshold_usd
        self.event_bus = event_bus
        # I49: callable(market_id_str) -> market_dict | None — resolves condition_id to numeric id
        self._market_index_resolver = market_index_resolver
        self.ws = None
        self.subscriptions = set()
        self.handlers: Dict[str, List[Callable]] = {}
        self.running = False
        self.message_loop_task = None
    
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
            return True
        except Exception as e:
            logger.error("WebSocket reconnect failed: %s", e)
            return False

    async def disconnect(self):
        """Close WebSocket connection."""
        self.running = False
        if self.message_loop_task:
            self.message_loop_task.cancel()
            try:
                await self.message_loop_task
            except asyncio.CancelledError:
                pass
        
        if self.ws:
            await self.ws.close()
            logger.info("WebSocket disconnected")
    
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
                # N3 FIX: After 10 consecutive failures (~10 min of retries), log CRITICAL and
                # slow down to 5-minute intervals. Without this circuit breaker, the reconnect
                # loop retries every 60s forever on permanent network failure, consuming CPU.
                _MAX_BEFORE_CRITICAL = 10
                if _reconnect_attempts == _MAX_BEFORE_CRITICAL:
                    logger.critical(
                        "WebSocket: %d consecutive reconnect failures — network may be permanently down. "
                        "Will continue retrying every 5 minutes.",
                        _reconnect_attempts,
                    )
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
            new_price = pc.get("best_bid") or pc.get("best_ask") or pc.get("price")
            if token_id and new_price is not None:
                await self._handle_price_change_one(market_id, token_id, float(new_price), _ws_recv_t=_ws_recv_t)
        token_id = data.get("token_id") or data.get("asset_id")
        new_price = data.get("price")
        if token_id and new_price is not None:
            await self._handle_price_change_one(market_id, token_id, float(new_price), _ws_recv_t=_ws_recv_t)

    async def _handle_price_change_one(self, market_id: Optional[str], token_id: str, new_price: float, _ws_recv_t: float = 0.0):
        """Single price update for cache, EventBus (Phase 4), and Redis publish."""
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
        if self.cache and self.cache.redis:
            try:
                await self.cache.set(f"prices:{token_id}:live", new_price, ttl=60)
                if market_id:
                    await self.cache.set(f"prices:{market_id}:live", new_price, ttl=60)
                await self.cache.redis.publish(
                    f"price_updates:{market_id or ''}",
                    json.dumps({
                        "market_id": market_id,
                        "token_id": token_id,
                        "price": new_price,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                )
            except Exception as e:
                logger.debug("Redis price cache/publish failed (non-fatal): %s", e)

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
