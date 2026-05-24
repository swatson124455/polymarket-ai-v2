"""
Telegram Stream Client — Signal 5: Social Velocity

Event-based streaming from prediction market Telegram channels
using Telethon. Feeds into velocity engine and sentiment scoring.

Dependencies: telethon (optional — graceful fallback).
Register at my.telegram.org/apps for API credentials.
"""
import asyncio
from typing import Callable, List, Optional, Any, Dict
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()


class TelegramStreamClient:
    """
    Telethon-based Telegram streaming client.

    Listens to configured channels and pushes messages
    to a callback for velocity/sentiment processing.
    """

    def __init__(
        self,
        api_id: Optional[str] = None,
        api_hash: Optional[str] = None,
        channels: Optional[List[str]] = None,
    ):
        self._api_id = api_id or settings.TELEGRAM_API_ID
        self._api_hash = api_hash or settings.TELEGRAM_API_HASH
        self._channels = channels or [c.strip() for c in settings.TELEGRAM_CHANNELS.split(",") if c.strip()]
        self._client = None
        self._running = False
        self._available = False
        self._check_availability()

    def _check_availability(self):
        """Check if Telethon is installed and credentials are configured."""
        if not self._api_id or not self._api_hash:
            logger.info("Telegram streaming disabled: TELEGRAM_API_ID/HASH not set")
            return
        try:
            import telethon
            self._available = True
            logger.info("Telethon available: Telegram streaming enabled")
        except ImportError:
            logger.info("telethon not installed — Telegram streaming disabled")

    @property
    def is_available(self) -> bool:
        return self._available

    async def start(self, callback: Callable[[Dict[str, Any]], Any]):
        """
        Start streaming messages from configured channels.

        Args:
            callback: Async or sync function called with each message dict:
                {text, chat, author, timestamp, source}
        """
        if not self._available or not self._channels:
            return

        try:
            from telethon import TelegramClient, events

            self._client = TelegramClient(
                "polymarket_telegram_session",
                int(self._api_id),
                self._api_hash,
            )

            @self._client.on(events.NewMessage(chats=self._channels))
            async def handler(event):
                if not self._running:
                    return
                try:
                    message = {
                        "text": event.message.text or "",
                        "chat": getattr(event.chat, "title", str(event.chat_id)),
                        "author": str(getattr(event.message.sender_id, "", "")),
                        "timestamp": event.message.date.timestamp() if event.message.date else 0,
                        "source": "telegram",
                    }
                    if asyncio.iscoroutinefunction(callback):
                        await callback(message)
                    else:
                        callback(message)
                except Exception as e:
                    logger.debug("Telegram message handler error: %s", e)

            self._running = True
            await self._client.start()
            logger.info("Telegram stream started", channels=self._channels)
            await self._client.run_until_disconnected()

        except Exception as e:
            logger.warning("Telegram stream error: %s", e)
            self._running = False

    async def stop(self):
        """Stop the Telegram stream."""
        self._running = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
