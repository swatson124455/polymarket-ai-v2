"""
News Aggregator — unified coordinator for all sports news monitors.

Wires together:
  - TwitterInjuryMonitor  (X API filtered stream)
  - RSSInjuryMonitor      (Rotowire + ESPN RSS/JSON)
  - RedditInjuryMonitor   (PRAW async polling)
  - DiscordTelegramMonitor (public team channels)

All monitors write raw items to a shared raw_queue using put_nowait().
The aggregator drains that queue, runs injury_detector, resolves the
player via player_registry, deduplicates via injury_store.is_duplicate(),
saves to DB via injury_store.save(), then enqueues the resolved InjuryEvent
onto the SportsInjuryBot's _injury_queue.

Usage (called from SportsInjuryBot.start())::
    aggregator = NewsAggregator(
        injury_bot_queue=self._injury_queue,
        db=db,
    )
    await aggregator.start()      # launches background tasks
    await aggregator.stop()       # clean shutdown
"""
from __future__ import annotations

import asyncio
from typing import Optional
from structlog import get_logger

from sports.news.injury_detector import detect_injury
from sports.data.player_registry import resolve_player
from sports.data.injury_store import is_duplicate, save as injury_save

logger = get_logger()

_RAW_QUEUE_MAX = 2000


class NewsAggregator:
    """
    Unified news pipeline coordinator.

    Starts background tasks for all enabled monitors and a drain loop
    that processes raw items into resolved InjuryEvent objects.
    """

    def __init__(
        self,
        injury_bot_queue: asyncio.Queue,
        db=None,
    ) -> None:
        self._bot_queue = injury_bot_queue
        self._db = db
        self._raw_queue: asyncio.Queue = asyncio.Queue(maxsize=_RAW_QUEUE_MAX)
        self._tasks: list = []
        self._running = False

    def _on_monitor_done(self, task: asyncio.Task) -> None:
        """Callback for monitor tasks — log crash and auto-restart."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            name = task.get_name()
            logger.warning(
                "NewsAggregator: monitor task crashed — will NOT auto-restart",
                task_name=name,
                error=str(exc),
            )

    async def start(self) -> None:
        """Launch all monitor tasks and the drain loop."""
        self._running = True
        self._tasks = []
        _monitors_started = 0  # I23: count successfully started monitors (not drain)

        # Twitter monitor
        try:
            from sports.news.twitter_monitor import TwitterInjuryMonitor
            twitter = TwitterInjuryMonitor(self._raw_queue)
            self._tasks.append(asyncio.create_task(
                twitter.run_forever(), name="twitter_monitor"
            ))
            self._tasks[-1].add_done_callback(self._on_monitor_done)
            _monitors_started += 1
        except Exception as exc:
            logger.warning("NewsAggregator: failed to start Twitter monitor", error=str(exc))

        # RSS monitor
        try:
            from sports.news.rss_monitor import RSSInjuryMonitor
            rss = RSSInjuryMonitor(self._raw_queue)
            self._tasks.append(asyncio.create_task(
                rss.run_forever(), name="rss_monitor"
            ))
            self._tasks[-1].add_done_callback(self._on_monitor_done)
            _monitors_started += 1
        except Exception as exc:
            logger.warning("NewsAggregator: failed to start RSS monitor", error=str(exc))

        # Reddit monitor (optional — gated by REDDIT_CLIENT_ID)
        try:
            from sports.news.reddit_monitor import RedditInjuryMonitor
            reddit = RedditInjuryMonitor(self._raw_queue)
            self._tasks.append(asyncio.create_task(
                reddit.run_forever(), name="reddit_monitor"
            ))
            self._tasks[-1].add_done_callback(self._on_monitor_done)
            _monitors_started += 1
        except Exception as exc:
            logger.debug("NewsAggregator: Reddit monitor not started", error=str(exc))

        # Discord/Telegram monitor (optional — gated by SPORTS_DISCORD_ENABLED / SPORTS_TELEGRAM_ENABLED)
        try:
            from sports.news.discord_telegram_monitor import DiscordTelegramMonitor
            dt = DiscordTelegramMonitor(self._raw_queue)
            self._tasks.append(asyncio.create_task(
                dt.run_forever(), name="discord_telegram_monitor"
            ))
            self._tasks[-1].add_done_callback(self._on_monitor_done)
            _monitors_started += 1
        except Exception as exc:
            logger.debug("NewsAggregator: Discord/Telegram monitor not started", error=str(exc))

        # I23: Raise if no monitors started — injury pipeline would be completely blind
        if _monitors_started == 0:
            raise RuntimeError(
                "NewsAggregator: 0 monitors started — "
                "SportsInjuryBot would produce no events. "
                "Check imports for TwitterInjuryMonitor and RSSInjuryMonitor."
            )

        # Drain loop
        self._tasks.append(asyncio.create_task(
            self._drain_loop(), name="news_aggregator_drain"
        ))

        logger.info(
            "NewsAggregator: started",
            monitors_started=_monitors_started,
            tasks=[t.get_name() for t in self._tasks],
        )

    async def stop(self) -> None:
        """Cancel all background tasks gracefully."""
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("NewsAggregator: stopped")

    # ─── Drain Loop ───────────────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        """
        Continuously drain raw_queue, detect injuries, resolve players,
        dedup, save to DB, and enqueue resolved InjuryEvent for the bot.
        """
        logger.info("NewsAggregator: drain loop started")
        while self._running:
            try:
                # Block up to 1s waiting for an item
                try:
                    raw_item = await asyncio.wait_for(
                        self._raw_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                await self._process_raw_item(raw_item)
                self._raw_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("NewsAggregator: drain loop error", error=str(exc))
                await asyncio.sleep(1.0)

        logger.info("NewsAggregator: drain loop exiting")

    async def _process_raw_item(self, raw_item: dict) -> None:
        """
        Full pipeline for one raw news item:
          1. Detect injury (3-tier NLP)
          2. Resolve player (fuzzy match → DB ID)
          3. Deduplicate (60-min window)
          4. Save to DB
          5. Enqueue for SportsInjuryBot
        """
        try:
            # Step 1: Detect
            event = await asyncio.wait_for(detect_injury(raw_item), timeout=10.0)
            if event is None:
                return

            # Step 2: Resolve player
            if self._db and event.player_raw:
                try:
                    pid = await asyncio.wait_for(
                        resolve_player(event.player_raw, event.sport, db=self._db),
                        timeout=5.0,
                    )
                    event.player_id = pid
                except asyncio.TimeoutError:
                    # I36: Log WARNING so operators can investigate unresolved players
                    logger.warning(
                        "NewsAggregator: player resolution timeout — player_id=None",
                        player_raw=event.player_raw,
                        sport=event.sport,
                    )

            # Step 3: Dedup check
            try:
                dup = await asyncio.wait_for(
                    is_duplicate(event, db=self._db), timeout=5.0
                )
                if dup:
                    logger.debug(
                        "NewsAggregator: duplicate event suppressed",
                        player=event.player_raw,
                        status=event.detected_status,
                        source=event.source,
                    )
                    return
            except asyncio.TimeoutError:
                pass  # proceed even if dedup check times out

            # Step 4: Save to DB
            if self._db:
                try:
                    event_id = await asyncio.wait_for(
                        injury_save(event, db=self._db), timeout=5.0
                    )
                    event.id = event_id
                except asyncio.TimeoutError:
                    pass

            # Step 5: Enqueue for SportsInjuryBot
            try:
                self._bot_queue.put_nowait(event)
                logger.info(
                    "NewsAggregator: injury event queued",
                    player=event.player_raw,
                    sport=event.sport,
                    status=event.detected_status,
                    confidence=round(event.confidence, 3),
                    source=event.source,
                    nlp_tier=event.nlp_tier,
                )
            except asyncio.QueueFull:
                logger.warning(
                    "NewsAggregator: injury bot queue full — dropping event",
                    player=event.player_raw,
                )

        except asyncio.TimeoutError:
            # I24/I64: Dead-letter queue — re-enqueue raw item (up to 2 retries)
            _retries = raw_item.get("_dlq_retries", 0)
            if _retries < 2:
                raw_item["_dlq_retries"] = _retries + 1
                try:
                    self._raw_queue.put_nowait(raw_item)
                    logger.warning(
                        "NewsAggregator: item timed out — re-enqueued (DLQ)",
                        source=raw_item.get("source", "?"),
                        retry=_retries + 1,
                    )
                except asyncio.QueueFull:
                    logger.warning(
                        "NewsAggregator: DLQ re-enqueue failed — queue full, dropping",
                        source=raw_item.get("source", "?"),
                    )
            else:
                logger.warning(
                    "NewsAggregator: item timed out after 2 retries — discarding",
                    source=raw_item.get("source", "?"),
                )
        except Exception as exc:
            logger.warning(
                "NewsAggregator: item processing error",
                error=str(exc),
                source=raw_item.get("source", "?"),
            )
