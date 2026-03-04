"""
Discord Stream Client — Signal 5: Social Velocity

Event-based streaming from prediction market Discord servers
using discord.py. Feeds into velocity engine and sentiment scoring.

Dependencies: discord.py (optional — graceful fallback).
Create bot at discord.com/developers/applications.
"""
import asyncio
from typing import Callable, List, Optional, Any, Dict
from structlog import get_logger
from config.settings import settings

logger = get_logger()


class DiscordStreamClient:
    """
    discord.py-based Discord streaming client.

    Listens to configured channels and pushes messages
    to a callback for velocity/sentiment processing.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        channel_ids: Optional[List[int]] = None,
    ):
        self._bot_token = bot_token or settings.DISCORD_BOT_TOKEN
        raw_ids = channel_ids or [
            int(cid.strip()) for cid in settings.DISCORD_CHANNEL_IDS.split(",")
            if cid.strip().isdigit()
        ]
        self._channel_ids = set(raw_ids)
        self._client = None
        self._running = False
        self._available = False
        self._check_availability()

    def _check_availability(self):
        """Check if discord.py is installed and token is configured."""
        if not self._bot_token:
            logger.info("Discord streaming disabled: DISCORD_BOT_TOKEN not set")
            return
        try:
            import discord
            self._available = True
            logger.info("discord.py available: Discord streaming enabled")
        except ImportError:
            logger.info("discord.py not installed — Discord streaming disabled")

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
        if not self._available:
            return

        try:
            import discord

            intents = discord.Intents.default()
            intents.message_content = True
            client = discord.Client(intents=intents)

            @client.event
            async def on_ready():
                logger.info("Discord stream connected", user=str(client.user))

            @client.event
            async def on_message(msg):
                if not self._running:
                    return
                if msg.author.bot:
                    return
                # Filter to configured channels (empty = all channels)
                if self._channel_ids and msg.channel.id not in self._channel_ids:
                    return
                try:
                    message = {
                        "text": msg.content or "",
                        "chat": getattr(msg.channel, "name", str(msg.channel.id)),
                        "author": str(msg.author),
                        "timestamp": msg.created_at.timestamp() if msg.created_at else 0,
                        "source": "discord",
                    }
                    if asyncio.iscoroutinefunction(callback):
                        await callback(message)
                    else:
                        callback(message)
                except Exception as e:
                    logger.debug("Discord message handler error: %s", e)

            self._client = client
            self._running = True
            await client.start(self._bot_token)

        except Exception as e:
            logger.warning("Discord stream error: %s", e)
            self._running = False

    async def stop(self):
        """Stop the Discord stream."""
        self._running = False
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
