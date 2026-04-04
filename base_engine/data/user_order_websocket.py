"""
Phase 7: User/order WebSocket channel - real-time order and trade events.

Connects to Polymarket CLOB user channel (wss://.../ws/user) with API auth.
Emits order_filled and order_update (position_change) on EventBus for fill-driven bots.
"""
import asyncio
import json
from typing import Any, Dict, Optional
from base_engine.data.json_parse import loads as json_loads

import websockets
import websockets.exceptions  # explicit import required — websockets v15 lazy-loads submodules
from structlog import get_logger

logger = get_logger()


class UserOrderWebSocket:
    """
    Authenticated WebSocket connection to Polymarket user channel.
    Streams order/trade events and emits to EventBus (order_filled, order_update).
    """

    def __init__(
        self,
        ws_url_base: str,
        event_bus: Optional[Any],
        auth: Dict[str, str],
    ):
        self.ws_url_base = (ws_url_base or "").rstrip("/")
        self.event_bus = event_bus
        self.auth = auth  # apiKey, secret, passphrase
        self.ws: Optional[Any] = None
        self.running = False
        self._message_loop_task: Optional[asyncio.Task] = None

    def _ws_url(self) -> str:
        return f"{self.ws_url_base}/ws/user"

    def _connect_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"ping_interval": 30, "ping_timeout": 10}
        return kwargs

    async def connect(self) -> None:
        """Connect to user channel and send auth."""
        if not self.auth.get("apiKey") or not self.auth.get("secret"):
            logger.warning("UserOrderWebSocket: CLOB API keys missing; skipping user channel")
            return
        try:
            self.running = True
            self.ws = await websockets.connect(
                self._ws_url(),
                **self._connect_kwargs(),
            )
            # Polymarket: type "user", auth dict, markets (condition_ids; empty = all)
            await self.ws.send(
                json.dumps(
                    {
                        "type": "user",
                        "auth": self.auth,
                        "markets": [],
                    }
                )
            )
            # Cancel stale message loop from prior connect() (prevents duplicate processing)
            _prev = getattr(self, "_message_loop_task", None)
            if _prev and not _prev.done():
                _prev.cancel()
            self._message_loop_task = asyncio.create_task(self._message_loop())
            logger.info("UserOrderWebSocket connected")
        except Exception as e:
            logger.warning("UserOrderWebSocket connection failed: %s", e)
            self.running = False

    async def disconnect(self) -> None:
        """Close connection."""
        self.running = False
        if self._message_loop_task:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        logger.info("UserOrderWebSocket disconnected")

    async def _message_loop(self) -> None:
        """Process user channel messages; emit order_filled / order_update to EventBus."""
        reconnect_delay = 2.0
        max_reconnect_delay = 60.0
        while self.running:
            if not self.ws:
                break
            try:
                raw = await self.ws.recv()
                reconnect_delay = 2.0  # Reset on successful message
                if raw in ("PONG", "pong"):
                    continue
                data = json_loads(raw)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("UserOrderWebSocket connection closed — reconnecting in %.0fs", reconnect_delay)
                self.ws = None
                if not self.running:
                    break
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                try:
                    self.ws = await websockets.connect(
                        self._ws_url(), **self._connect_kwargs()
                    )
                    await self.ws.send(json.dumps({
                        "type": "user", "auth": self.auth, "markets": []
                    }))
                    logger.info("UserOrderWebSocket reconnected")
                except Exception as e:
                    logger.warning("UserOrderWebSocket reconnect failed: %s", e)
                    self.ws = None
                continue
            except Exception as e:
                logger.debug("UserOrderWebSocket message parse error: %s", e)
                continue

            event_type = data.get("event_type") or data.get("type")
            if event_type == "trade":
                status = (data.get("status") or "").upper()
                if status in ("MATCHED", "CONFIRMED", "MINED"):
                    if self.event_bus:
                        try:
                            payload = {
                                "event_type": "trade",
                                "status": status,
                                "id": data.get("id"),
                                "asset_id": data.get("asset_id"),
                                "market": data.get("market"),
                                "side": data.get("side"),
                                "price": data.get("price"),
                                "size": data.get("size"),
                                "outcome": data.get("outcome"),
                                "timestamp": data.get("timestamp"),
                                "raw": data,
                            }
                            self.event_bus.emit_sync("order_filled", payload)
                        except Exception as e:
                            logger.debug("EventBus order_filled emit failed: %s", e)
            elif event_type == "order":
                if self.event_bus:
                    try:
                        payload = {
                            "event_type": "order",
                            "order_type": data.get("type"),  # PLACEMENT, UPDATE, CANCELLATION
                            "id": data.get("id"),
                            "asset_id": data.get("asset_id"),
                            "market": data.get("market"),
                            "side": data.get("side"),
                            "price": data.get("price"),
                            "original_size": data.get("original_size"),
                            "size_matched": data.get("size_matched"),
                            "outcome": data.get("outcome"),
                            "timestamp": data.get("timestamp"),
                            "raw": data,
                        }
                        self.event_bus.emit_sync("order_update", payload)
                        self.event_bus.emit_sync("position_change", payload)
                    except Exception as e:
                        logger.debug("EventBus order_update/position_change emit failed: %s", e)
