"""
RTDS WebSocket — Polymarket Real-Time Data Socket for global trade feed.

Connects to wss://ws-live-data.polymarket.com and subscribes to activity/trades.
Broadcasts ALL trades on the platform with proxyWallet (trader address).
No per-market subscription needed. No auth required.

Used by EliteWatchlist for instant copy trading — O(1) watchlist lookup on every trade.
"""
import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets
import websockets.exceptions
from structlog import get_logger

logger = get_logger()

_DEFAULT_URL = "wss://ws-live-data.polymarket.com"
_PING_INTERVAL = 5  # seconds — RTDS requires keep-alive pings


class RTDSWebSocket:
    """Global trade feed via Polymarket RTDS. Streams ALL trades with proxyWallet."""

    def __init__(
        self,
        handler: Callable[[Dict[str, Any]], Awaitable[None]],
        ws_url: str = _DEFAULT_URL,
        ping_interval: int = _PING_INTERVAL,
    ):
        self._handler = handler
        self._ws_url = ws_url
        self._ping_interval = ping_interval
        self.ws: Optional[Any] = None
        self.running = False
        self._ping_task: Optional[asyncio.Task] = None
        self._message_loop_task: Optional[asyncio.Task] = None
        self._events_total: int = 0
        self._events_dispatched: int = 0
        self._debug_samples: int = 0  # Log first N raw events for payload verification
        self._MAX_DEBUG_SAMPLES: int = 5

    async def connect(self) -> None:
        """Connect to RTDS and subscribe to activity/trades."""
        try:
            self.running = True
            self.ws = await websockets.connect(
                self._ws_url,
                ping_interval=None,  # We handle pings manually (RTDS protocol)
                ping_timeout=None,
            )
            # Subscribe to global trade feed
            await self.ws.send(json.dumps({
                "action": "subscribe",
                "subscriptions": [{"topic": "activity", "type": "trades"}],
            }))
            self._ping_task = asyncio.create_task(self._ping_loop())
            self._message_loop_task = asyncio.create_task(self._message_loop())
            logger.info("rtds_connected", url=self._ws_url)
        except Exception as e:
            logger.warning("rtds_connect_failed", error=str(e))
            self.running = False
            raise

    async def disconnect(self) -> None:
        """Clean shutdown."""
        self.running = False
        for task in (self._ping_task, self._message_loop_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        logger.info("rtds_disconnected")

    async def _ping_loop(self) -> None:
        """Send PING every N seconds to keep RTDS connection alive."""
        while self.running:
            try:
                await asyncio.sleep(self._ping_interval)
                if self.ws:
                    await self.ws.send("PING")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("rtds_ping_error", error=str(e))

    async def _reconnect(self) -> bool:
        """Reconnect and re-subscribe."""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        try:
            self.ws = await websockets.connect(
                self._ws_url,
                ping_interval=None,
                ping_timeout=None,
            )
            await self.ws.send(json.dumps({
                "action": "subscribe",
                "subscriptions": [{"topic": "activity", "type": "trades"}],
            }))
            logger.info("rtds_reconnected")
            return True
        except Exception as e:
            logger.warning("rtds_reconnect_failed", error=str(e))
            return False

    async def _message_loop(self) -> None:
        """Process incoming RTDS messages with exponential backoff on reconnection."""
        reconnect_attempts = 0
        max_backoff = 60
        while self.running:
            try:
                if not self.ws:
                    await asyncio.sleep(1)
                    continue

                raw = await self.ws.recv()
                reconnect_attempts = 0  # Reset on successful recv

                # Skip PONG responses and non-JSON
                if raw in ("PONG", "pong"):
                    continue

                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                # RTDS wraps events — handle both single dicts and lists
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            self._events_total += 1
                            await self._dispatch(item)
                elif isinstance(data, dict):
                    self._events_total += 1
                    await self._dispatch(data)

                # Periodic throughput log (every 1000 events)
                if self._events_total > 0 and self._events_total % 1000 == 0:
                    logger.info("rtds_throughput", events_total=self._events_total,
                                events_dispatched=self._events_dispatched)

            except websockets.exceptions.ConnectionClosed:
                reconnect_attempts += 1
                backoff = min(2 ** reconnect_attempts, max_backoff)
                logger.warning("rtds_connection_closed", attempt=reconnect_attempts, backoff_s=backoff)
                await asyncio.sleep(backoff)
                await self._reconnect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("rtds_message_error", error=str(e))
                await asyncio.sleep(1)

    async def _dispatch(self, data: Dict[str, Any]) -> None:
        """Route trade events to handler.

        RTDS wraps events: {connection_id, payload, timestamp, topic, type}.
        The actual trade data lives in data["payload"] (may be a dict or list).
        """
        # Unwrap RTDS envelope: trade data is in "payload"
        payload = data.get("payload", data)

        # Log first N raw events for payload verification (then stop)
        if self._debug_samples < self._MAX_DEBUG_SAMPLES:
            self._debug_samples += 1
            _keys = sorted(data.keys()) if isinstance(data, dict) else []
            _p_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
            logger.info("rtds_raw_sample", sample_num=self._debug_samples,
                        envelope_keys=_keys,
                        payload_keys=_p_keys,
                        has_proxyWallet=bool(payload.get("proxyWallet") if isinstance(payload, dict) else False),
                        has_asset=bool(payload.get("asset") if isinstance(payload, dict) else False),
                        side=payload.get("side") if isinstance(payload, dict) else None,
                        outcome=payload.get("outcome") if isinstance(payload, dict) else None)

        # Handle payload: could be a single dict or a list of dicts
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and (item.get("proxyWallet") or item.get("asset")):
                    self._events_dispatched += 1
                    try:
                        await self._handler(item)
                    except Exception as e:
                        logger.debug("rtds_handler_error", error=str(e))
        elif isinstance(payload, dict) and (payload.get("proxyWallet") or payload.get("asset")):
            self._events_dispatched += 1
            try:
                await self._handler(payload)
            except Exception as e:
                logger.debug("rtds_handler_error", error=str(e))
