"""
Discord + Telegram Monitor — public sports channel monitoring.

Discord:
  - Uses discord.py (discord>=2.3.0) when SPORTS_DISCORD_ENABLED=true
    and DISCORD_BOT_TOKEN is set.
  - Monitors public sports Discord servers (configured via
    SPORTS_DISCORD_CHANNEL_IDS env var, comma-separated).
  - Filters messages containing injury/roster keywords.

Telegram:
  - Uses telethon when SPORTS_TELEGRAM_ENABLED=true and
    TELEGRAM_API_ID + TELEGRAM_API_HASH are set.
  - Monitors: @FantasyLabsNBA, @RotowireNFL, and configured team channels.
  - Filters messages containing injury/roster keywords.

Both gated: if tokens are missing, the respective section silently no-ops.
Puts raw items onto an asyncio.Queue using put_nowait().

NOTE: discord.py and telethon are optional — install when ready:
  pip install discord.py>=2.3.0 telethon>=1.36.0
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import List, Optional
from structlog import get_logger

logger = get_logger()

_INJURY_KEYWORDS = [
    "injured", "injury", "out", "doubtful", "questionable", "day-to-day",
    "dtd", "dnp", "scratched", "il", "ir", "surgery", "sidelined",
    "ruled out", "will not play", "torn", "fractured", "sprained",
    "withdrawal", "withdrawn", "retired", "released", "signs with",
    "free agent", "traded",
]

# Default Telegram sports channels to monitor
_DEFAULT_TELEGRAM_CHANNELS = [
    "@FantasyLabsNBA",
    "@RotowireNFL",
    "@RotowireNBA",
]

_SPORT_KEYWORDS = {
    "nba": ["nba", "basketball"],
    "nfl": ["nfl", "football"],
    "mlb": ["mlb", "baseball"],
    "nhl": ["nhl", "hockey"],
    "soccer": ["soccer", "football", "epl", "premier league"],
    "tennis": ["tennis", "atp", "wta"],
}


class DiscordTelegramMonitor:
    """
    Combined Discord + Telegram monitor for sports injury intel.

    Usage::
        monitor = DiscordTelegramMonitor(output_queue)
        asyncio.create_task(monitor.run_forever())
    """

    def __init__(self, output_queue: asyncio.Queue) -> None:
        self._queue = output_queue
        self._running = False
        self._seen: OrderedDict = OrderedDict()

    async def run_forever(self) -> None:
        """Start both Discord and Telegram monitors as concurrent tasks."""
        from config.settings import settings

        discord_enabled = getattr(settings, "SPORTS_DISCORD_ENABLED", False)
        telegram_enabled = getattr(settings, "SPORTS_TELEGRAM_ENABLED", False)

        if not discord_enabled and not telegram_enabled:
            logger.info(
                "DiscordTelegramMonitor: both disabled",
                hint="Set SPORTS_DISCORD_ENABLED=true or SPORTS_TELEGRAM_ENABLED=true",
            )
            return

        self._running = True
        tasks = []

        if discord_enabled:
            tasks.append(asyncio.create_task(
                self._run_discord(settings), name="discord_monitor"
            ))

        if telegram_enabled:
            tasks.append(asyncio.create_task(
                self._run_telegram(settings), name="telegram_monitor"
            ))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self._running = False

    # ─── Discord ──────────────────────────────────────────────────────────────

    async def _run_discord(self, settings) -> None:
        """Run Discord bot monitor."""
        bot_token = getattr(settings, "DISCORD_BOT_TOKEN", None)
        if not bot_token:
            logger.info("DiscordTelegramMonitor: no DISCORD_BOT_TOKEN — Discord inactive")
            return

        try:
            import discord  # type: ignore
        except ImportError:
            logger.warning(
                "DiscordTelegramMonitor: discord.py not installed",
                hint="pip install discord.py>=2.3.0",
            )
            return

        # Channel IDs to monitor (comma-separated in env)
        channel_ids_str = getattr(settings, "SPORTS_DISCORD_CHANNEL_IDS", "")
        channel_ids = [
            int(cid.strip()) for cid in channel_ids_str.split(",")
            if cid.strip().isdigit()
        ]

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            logger.info(
                "DiscordTelegramMonitor: Discord connected",
                user=str(client.user),
                monitoring_channels=len(channel_ids),
            )

        @client.event
        async def on_message(message):
            if message.author.bot:
                return
            if channel_ids and message.channel.id not in channel_ids:
                return
            text = message.content.strip()
            if not text or not self._has_injury_keyword(text):
                return

            key = self._dedup_key(f"discord_{message.id}")
            if key in self._seen:
                return
            self._seen[key] = None
            self._trim_seen()

            sport = self._infer_sport(text)
            item = {
                "source": "discord",
                "source_id": str(message.id),
                "sport": sport,
                "text": text,
                "url": f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}",
            }
            try:
                self._queue.put_nowait(item)
                logger.info(
                    "DiscordTelegramMonitor: Discord message queued",
                    sport=sport,
                    channel=message.channel.name,
                )
            except asyncio.QueueFull:
                pass

        try:
            await client.start(bot_token)
        except Exception as exc:
            logger.warning("DiscordTelegramMonitor: Discord error", error=str(exc))

    # ─── Telegram ─────────────────────────────────────────────────────────────

    async def _run_telegram(self, settings) -> None:
        """Run Telegram channel monitor via telethon."""
        api_id = getattr(settings, "TELEGRAM_API_ID", None)
        api_hash = getattr(settings, "TELEGRAM_API_HASH", None)

        if not api_id or not api_hash:
            logger.info(
                "DiscordTelegramMonitor: no TELEGRAM_API_ID/HASH — Telegram inactive"
            )
            return

        try:
            from telethon import TelegramClient, events  # type: ignore
        except ImportError:
            logger.warning(
                "DiscordTelegramMonitor: telethon not installed",
                hint="pip install telethon>=1.36.0",
            )
            return

        channels_str = getattr(settings, "SPORTS_TELEGRAM_CHANNELS", "")
        channels: List[str] = (
            [c.strip() for c in channels_str.split(",") if c.strip()]
            if channels_str
            else _DEFAULT_TELEGRAM_CHANNELS
        )

        try:
            client = TelegramClient(
                "sports_monitor",
                int(api_id),
                api_hash,
            )

            @client.on(events.NewMessage(chats=channels))
            async def handler(event):
                text = event.raw_text.strip()
                if not text or not self._has_injury_keyword(text):
                    return

                key = self._dedup_key(f"telegram_{event.id}")
                if key in self._seen:
                    return
                self._seen[key] = None
                self._trim_seen()

                sport = self._infer_sport(text)
                item = {
                    "source": "telegram",
                    "source_id": str(event.id),
                    "sport": sport,
                    "text": text,
                    "url": "",
                }
                try:
                    self._queue.put_nowait(item)
                    logger.info(
                        "DiscordTelegramMonitor: Telegram message queued",
                        sport=sport,
                    )
                except asyncio.QueueFull:
                    pass

            await client.start()
            logger.info(
                "DiscordTelegramMonitor: Telegram connected",
                channels=channels,
            )
            await client.run_until_disconnected()
        except Exception as exc:
            logger.warning("DiscordTelegramMonitor: Telegram error", error=str(exc))

    def _trim_seen(self) -> None:
        _max = 5000
        while len(self._seen) > _max:
            self._seen.popitem(last=False)

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _has_injury_keyword(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in _INJURY_KEYWORDS)

    @staticmethod
    def _infer_sport(text: str) -> str:
        text_lower = text.lower()
        for sport, keywords in _SPORT_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return sport
        return "unknown"

    @staticmethod
    def _dedup_key(raw: str) -> str:
        return hashlib.md5(raw.encode()).hexdigest()
