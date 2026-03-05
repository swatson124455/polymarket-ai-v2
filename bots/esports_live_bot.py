"""
EsportsLiveBot — Real-time in-game event detection and betting bot.

Mirrors SportsLiveBot pattern exactly:
  - Starts EsportsGameMonitor as background task
  - scan_and_trade() drains game update queue
  - EsportsEventDetector classifies each state → EsportsLiveEvents
  - EsportsLiveTrigger enforces cooldowns + caps + places orders

Uses own EsportsBankrollManager (separate Kelly pool from risk_manager).

Enable: BOT_ENABLED_ESPORTS_LIVE=true (disabled by default).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from structlog import get_logger

from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class EsportsLiveBot(BaseBot):
    """
    Live in-game event detection and betting bot for esports.

    Receives EsportsGameState updates from EsportsGameMonitor and converts
    detected EsportsLiveEvents into bet placements via EsportsLiveTrigger.
    """

    def __init__(self, base_engine):
        super().__init__("EsportsLiveBot", base_engine)

        # Fail fast if PandaScore API key not configured
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            raise ValueError(
                "EsportsLiveBot requires PANDASCORE_API_KEY — set it in .env"
            )

        self._api_key = api_key
        self._game_update_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._game_monitor = None
        self._event_detector = None
        self._live_trigger = None
        self._scanner = None
        self._bankroll_mgr = None
        self._monitor_task: Optional[asyncio.Task] = None

    def _get_scan_interval_seconds(self) -> float:
        """10s during live games, 60s when idle."""
        if self._game_monitor and getattr(self._game_monitor, "active_games", {}):
            return float(getattr(settings, "SCAN_INTERVAL_ESPORTS_LIVE", 10))
        return 60.0

    async def start(self) -> None:
        """Start game monitor, event detector, live trigger, then scan loop."""
        db = getattr(self.base_engine, "db", None)
        gw = getattr(self.base_engine, "order_gateway", None)

        from esports.data.pandascore_client import PandaScoreClient
        from esports.live.esports_game_monitor import EsportsGameMonitor
        from esports.live.esports_event_detector import EsportsEventDetector
        from esports.live.esports_live_trigger import EsportsLiveTrigger
        from esports.markets.esports_market_scanner import EsportsMarketScanner
        from esports.kelly.esports_bankroll_manager import EsportsBankrollManager

        pandascore = PandaScoreClient(api_key=self._api_key)
        await pandascore.init()

        self._game_monitor = EsportsGameMonitor(
            update_queue=self._game_update_queue,
            pandascore_client=pandascore,
        )
        self._event_detector = EsportsEventDetector()
        self._live_trigger = EsportsLiveTrigger()
        self._scanner = EsportsMarketScanner(db=db)
        self._bankroll_mgr = EsportsBankrollManager(order_gateway=gw)

        # Start game monitor as background task
        self._monitor_task = asyncio.create_task(
            self._game_monitor.run_forever(),
            name="esports_game_monitor",
        )
        self._monitor_task.add_done_callback(
            lambda t: self._on_bg_task_done(t, "esports_game_monitor")
        )

        logger.info(
            "EsportsLiveBot: started (game monitor running)",
            enabled=getattr(settings, "BOT_ENABLED_ESPORTS_LIVE", False),
        )

        await super().start()

    def _on_bg_task_done(self, task: asyncio.Task, name: str) -> None:
        """Log background task failures."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("bg_task_failed", task_name=name, error=str(exc))

    async def stop(self) -> None:
        """Stop game monitor and scan loop."""
        if self._game_monitor:
            await self._game_monitor.stop()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        await super().stop()

    async def on_price_update(self, event: dict) -> None:
        """React to WS price updates — log price moves on active game markets."""
        await super().on_price_update(event)
        if not self.running or not self._game_monitor:
            return

        import time as _time

        market_id = event.get("market_id", "")
        new_price = float(event.get("price", 0))
        if not market_id or new_price <= 0:
            return

        # Significance threshold for live games (tighter: 0.5%)
        threshold = float(getattr(settings, "ESPORTS_LIVE_WS_PRICE_CHANGE_PCT", 0.005))
        if not hasattr(self, "_ws_prev_prices"):
            self._ws_prev_prices: dict = {}
        old_price = self._ws_prev_prices.get(market_id)
        self._ws_prev_prices[market_id] = new_price
        if old_price is None or abs(new_price - old_price) / max(old_price, 0.01) < threshold:
            return

        # Cooldown
        now = _time.monotonic()
        if not hasattr(self, "_ws_cooldowns"):
            self._ws_cooldowns: dict = {}
        cooldown = int(getattr(settings, "ESPORTS_LIVE_WS_COOLDOWN_SECONDS", 5))
        if now - self._ws_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_cooldowns[market_id] = now

        # Log significant price move during active game
        active_games = getattr(self._game_monitor, "active_games", {})
        if active_games:
            logger.info(
                "EsportsLiveBot: significant price move during active games",
                market_id=market_id,
                price_move=f"{old_price:.4f}→{new_price:.4f}",
                active_games=len(active_games),
            )

    async def scan_and_trade(self) -> None:
        """
        Drain game update queue, detect events, fire live bets.

        Processes up to 20 game state updates per scan cycle.
        """
        db = getattr(self.base_engine, "db", None)
        processed = 0
        events_detected = 0
        _trades = 0

        # Prune expired cooldowns
        if self._live_trigger:
            self._live_trigger.prune_cooldowns()

        while processed < 20:
            try:
                from esports.live.esports_game_monitor import EsportsGameState
                game_state: EsportsGameState = self._game_update_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if self._event_detector:
                live_events = self._event_detector.detect(game_state)
                for live_event in live_events:
                    events_detected += 1
                    logger.info(
                        "EsportsLiveBot: live event detected",
                        event_type=live_event.event_type,
                        match_id=live_event.match_id,
                        game=live_event.game,
                        description=live_event.description,
                        confidence=round(live_event.confidence, 3),
                    )
                    if self._live_trigger:
                        try:
                            result = await asyncio.wait_for(
                                self._live_trigger.process_event(
                                    live_event,
                                    bot=self,
                                    db=db,
                                    scanner=self._scanner,
                                    bankroll_mgr=self._bankroll_mgr,
                                ),
                                timeout=15.0,
                            )
                            if result:
                                _trades += 1
                        except asyncio.TimeoutError:
                            logger.warning(
                                "EsportsLiveBot: live trigger timed out",
                                event_type=live_event.event_type,
                            )
                        except Exception as exc:
                            logger.warning(
                                "EsportsLiveBot: live trigger error",
                                error=str(exc),
                            )
            processed += 1

        self._last_scan_markets = processed
        self._last_scan_opportunities = events_detected
        self._last_scan_trades = _trades

        if events_detected:
            logger.info(
                "EsportsLiveBot: scan complete",
                game_updates=processed,
                events_detected=events_detected,
                active_games=len(self._game_monitor.active_games) if self._game_monitor else 0,
            )

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot ABC. EsportsLiveBot is event-driven."""
        return None
