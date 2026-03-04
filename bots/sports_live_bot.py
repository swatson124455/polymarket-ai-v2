"""
SportsLiveBot — Real-time in-game event betting bot.

Monitors live game state via SportsDataIO polling feeds and fires
bets immediately on detected events (blowout, momentum shift).

Phase 4: Full implementation.
  - Starts GameMonitor in start() as a background task.
  - scan_and_trade() drains the game update queue.
  - EventDetector classifies each GameState update into LiveEvents.
  - LiveTrigger enforces cooldowns + per-game caps + places orders.

Thresholds (configurable via env):
  NBA: score_diff > 20 at elapsed_pct > 60%
  NFL: score_diff > 17 (3 scores) at elapsed > 75%
  Soccer: goal_diff > 2 at elapsed > 70%
  NHL: score_diff > 3 at elapsed > 70%
  MLB: run_diff > 7 at elapsed > 67%

Scan interval: SCAN_INTERVAL_SPORTS_LIVE (default 10s during live games,
               60s when no live games).
Enable: BOT_ENABLED_SPORTS_LIVE=true (disabled by default).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from structlog import get_logger

from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class SportsLiveBot(BaseBot):
    """
    Live in-game event detection and betting bot.

    Receives GameState updates from GameMonitor and converts detected
    LiveEvents into bet placements via LiveTrigger.
    """

    def __init__(self, base_engine):
        super().__init__("SportsLiveBot", base_engine)
        # Queue for live GameState updates from GameMonitor
        self._game_update_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._game_monitor = None
        self._event_detector = None
        self._live_trigger = None
        self._scanner = None
        self._bankroll_mgr = None

        self._max_bets_per_game: int = int(
            getattr(settings, "SPORTS_LIVE_MAX_BETS_PER_GAME", 3)
        )
        self._cooldown_seconds: int = int(
            getattr(settings, "SPORTS_LIVE_BET_COOLDOWN_SECONDS", 30)
        )

    def _get_scan_interval_seconds(self) -> float:
        """10s during live games, 60s when idle."""
        if self._game_monitor and self._game_monitor.active_games:
            return float(getattr(settings, "SCAN_INTERVAL_SPORTS_LIVE", 10))
        return 60.0

    async def start(self) -> None:
        """
        Start GameMonitor WebSocket task before entering the scan loop.
        """
        db = getattr(self.base_engine, "db", None)
        gw = getattr(self.base_engine, "order_gateway", None)

        # Initialize components
        from sports.live.game_monitor import GameMonitor
        from sports.live.event_detector import EventDetector
        from sports.live.live_trigger import LiveTrigger
        from sports.markets.sports_market_scanner import SportsMarketScanner
        from sports.kelly.bankroll_manager import SportsBankrollManager

        self._game_monitor = GameMonitor(self._game_update_queue)
        self._event_detector = EventDetector()
        self._live_trigger = LiveTrigger()
        self._scanner = SportsMarketScanner(db=db)
        self._bankroll_mgr = SportsBankrollManager(order_gateway=gw)

        # Start game monitor as background task
        self._monitor_task = asyncio.create_task(
            self._game_monitor.run_forever(),
            name="sports_game_monitor",
        )
        self._monitor_task.add_done_callback(
            lambda t: self._on_bg_task_done(t, "sports_game_monitor")
        )
        logger.info(
            "SportsLiveBot: started (game monitor running)",
            enabled=getattr(settings, "BOT_ENABLED_SPORTS_LIVE", False),
        )

        await super().start()

    def _on_bg_task_done(self, task, name):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("bg_task_failed", task_name=name, error=str(exc))

    async def stop(self) -> None:
        """Stop game monitor and scan loop."""
        if self._game_monitor:
            await self._game_monitor.stop()
        await super().stop()

    async def scan_and_trade(self) -> None:
        """
        Drain game update queue, detect events, fire live bets.

        Processes up to 20 game state updates per scan cycle.
        """
        db = getattr(self.base_engine, "db", None)
        processed = 0
        events_detected = 0

        # Prune expired cooldowns periodically
        if self._live_trigger:
            self._live_trigger.prune_cooldowns()

        while processed < 20:
            try:
                from sports.live.game_monitor import GameState
                game_state: GameState = self._game_update_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if self._event_detector:
                live_events = self._event_detector.detect(game_state)
                for live_event in live_events:
                    events_detected += 1
                    logger.info(
                        "SportsLiveBot: live event detected",
                        event_type=live_event.event_type,
                        game_id=live_event.game_id,
                        sport=live_event.sport,
                        description=live_event.description,
                        confidence=round(live_event.confidence, 3),
                    )
                    if self._live_trigger:
                        try:
                            await asyncio.wait_for(
                                self._live_trigger.process_event(
                                    live_event, bot=self, db=db
                                ),
                                timeout=15.0,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "SportsLiveBot: live trigger timed out",
                                event_type=live_event.event_type,
                            )
                        except Exception as exc:
                            logger.warning(
                                "SportsLiveBot: live trigger error",
                                error=str(exc),
                            )
            processed += 1

        if events_detected:
            logger.info(
                "SportsLiveBot: scan complete",
                game_updates=processed,
                events_detected=events_detected,
                active_games=len(self._game_monitor.active_games) if self._game_monitor else 0,
            )

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot ABC. SportsLiveBot is event-driven."""
        return None
