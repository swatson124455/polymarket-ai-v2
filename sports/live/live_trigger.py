"""
Live Trigger — immediate bet execution for live game events.

Bypasses the normal 10s scan interval and fires immediately when
EventDetector emits a LiveEvent.

Controls:
  - 30s cooldown per market (SPORTS_LIVE_BET_COOLDOWN_SECONDS)
  - Max 3 live bets per game (SPORTS_LIVE_MAX_BETS_PER_GAME)
  - Min confidence gate (SPORTS_LIVE_MIN_CONFIDENCE, default 0.70)

Integrates with SportsLiveBot via live_trigger.process_event(event, bot).
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional
from structlog import get_logger

from sports.live.event_detector import LiveEvent

logger = get_logger()


class LiveTrigger:
    """
    Manages cooldowns and per-game caps for live bets, then fires orders.

    Usage (from SportsLiveBot.scan_and_trade)::
        trigger = LiveTrigger()
        for event in events:
            await trigger.process_event(event, bot=self)
    """

    def __init__(self) -> None:
        # market_id → last bet time (monotonic)
        self._cooldowns: Dict[str, float] = {}
        # I29: game_id → {event_type → count} — block duplicate event_type bets per game
        self._bets_per_game: Dict[str, Dict[str, int]] = {}

    async def process_event(
        self,
        event: LiveEvent,
        bot,           # SportsLiveBot instance (has place_order, _scanner, _bankroll_mgr)
        db=None,
    ) -> bool:
        """
        Evaluate a LiveEvent and place a bet if all guards pass.

        Returns True if a bet was placed.
        """
        from config.settings import settings

        min_conf = float(getattr(settings, "SPORTS_LIVE_MIN_CONFIDENCE", 0.70))
        max_bets = int(getattr(settings, "SPORTS_LIVE_MAX_BETS_PER_GAME", 3))
        cooldown_s = int(getattr(settings, "SPORTS_LIVE_BET_COOLDOWN_SECONDS", 30))

        # Guard 1: Confidence
        if event.confidence < min_conf:
            logger.debug(
                "LiveTrigger: below confidence threshold",
                conf=event.confidence, threshold=min_conf,
            )
            return False

        # Guard 2: Per-game cap + I29: also block duplicate event_type per game
        game_bets = self._bets_per_game.get(event.game_id, {})
        total_bets = sum(game_bets.values())
        if total_bets >= max_bets:
            logger.debug(
                "LiveTrigger: per-game cap reached",
                game_id=event.game_id, cap=max_bets,
            )
            return False
        # I29: Block same event_type from firing twice on same game (e.g. 2× blowout bets)
        if game_bets.get(event.event_type, 0) >= 1:
            logger.debug(
                "LiveTrigger: duplicate event_type blocked",
                game_id=event.game_id, event_type=event.event_type,
            )
            return False
        bets_so_far = total_bets  # for the increment below

        # Find markets for this game
        try:
            scanner = getattr(bot, "_scanner", None)
            if scanner is None:
                return False
            markets = await asyncio.wait_for(
                scanner.find_markets_for_game(event.game_id, event.sport, db=db),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            logger.warning("LiveTrigger: market scanner timed out")
            return False

        if not markets:
            logger.debug("LiveTrigger: no markets found", game_id=event.game_id)
            return False

        placed_any = False
        for market in markets[:3]:  # max 3 markets per event
            market_id = market.market_id

            # Guard 3: Cooldown
            last_bet_time = self._cooldowns.get(market_id, 0.0)
            if time.monotonic() - last_bet_time < cooldown_s:
                logger.debug(
                    "LiveTrigger: cooldown active",
                    market_id=market_id,
                    remaining_s=round(cooldown_s - (time.monotonic() - last_bet_time), 1),
                )
                continue

            # Size via bankroll manager
            bankroll_mgr = getattr(bot, "_bankroll_mgr", None)
            if bankroll_mgr:
                fair_prob = 0.5 + event.edge_estimate
                market_price = market.current_price or 0.5
                size = await bankroll_mgr.get_bet_size(
                    fair_prob=fair_prob,
                    market_price=market_price,
                    sport=event.sport,
                    market_type="moneyline",
                    db=db,
                )
            else:
                size = 0.0

            if size <= 0.0:
                logger.debug("LiveTrigger: zero size from bankroll manager")
                continue

            # Place order via bot.place_order (inherited from BaseBot)
            try:
                result = await asyncio.wait_for(
                    bot.place_order(
                        market_id=market_id,
                        token_id=market.yes_token_id or "",
                        side=event.market_side,
                        size=size,
                        price=market.current_price or 0.5,
                        confidence=event.confidence,
                        correlation_id=f"live_{event.game_id}_{event.event_type}",
                    ),
                    timeout=10.0,
                )
                if result.get("success"):
                    self._cooldowns[market_id] = time.monotonic()
                    # I29: Track by event_type for per-type dedup
                    if event.game_id not in self._bets_per_game:
                        self._bets_per_game[event.game_id] = {}
                    self._bets_per_game[event.game_id][event.event_type] = (
                        self._bets_per_game[event.game_id].get(event.event_type, 0) + 1
                    )
                    placed_any = True
                    logger.info(
                        "LiveTrigger: live bet placed",
                        game_id=event.game_id,
                        event_type=event.event_type,
                        market_id=market_id,
                        side=event.market_side,
                        size=size,
                        confidence=event.confidence,
                    )
            except asyncio.TimeoutError:
                logger.warning("LiveTrigger: place_order timed out", market_id=market_id)
            except Exception as exc:
                logger.warning("LiveTrigger: place_order error", error=str(exc))

        return placed_any

    def _can_bet(self, event: "LiveEvent", max_bets: int = 3) -> bool:
        """
        Check if a live event is eligible to be bet on.

        Returns False if:
          - The per-game total cap has been reached.
          - The same event_type already has a bet on this game (I29).
        Returns True otherwise.
        """
        game_bets = self._bets_per_game.get(event.game_id, {})
        total_bets = sum(game_bets.values())
        if total_bets >= max_bets:
            return False
        if game_bets.get(event.event_type, 0) >= 1:
            return False
        return True

    def prune_cooldowns(self) -> None:
        """Remove expired cooldown entries. Call periodically."""
        from config.settings import settings
        cooldown_s = int(getattr(settings, "SPORTS_LIVE_BET_COOLDOWN_SECONDS", 30))
        now = time.monotonic()
        expired = [k for k, t in self._cooldowns.items() if now - t > cooldown_s * 2]
        for k in expired:
            del self._cooldowns[k]

    async def load_cooldowns_from_db(self, db) -> None:
        """
        I30: Load recent live bets from sports_live_events on startup.

        Rebuilds _cooldowns using UTC wall-clock time (not monotonic) so
        cooldowns correctly expire even after a restart.
        """
        from config.settings import settings
        from sqlalchemy import text
        cooldown_s = int(getattr(settings, "SPORTS_LIVE_BET_COOLDOWN_SECONDS", 30))
        import datetime as _dt
        cutoff = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _dt.timedelta(seconds=cooldown_s)
        try:
            async with db.get_session() as session:
                result = await session.execute(
                    text(
                        "SELECT bet_market_id, detected_at "
                        "FROM sports_live_events "
                        "WHERE bet_triggered = TRUE "
                        "  AND detected_at >= :cutoff "
                        "ORDER BY detected_at DESC"
                    ),
                    {"cutoff": cutoff},
                )
                rows = result.fetchall()
            loaded = 0
            now_mono = time.monotonic()
            now_utc = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
            for row in rows:
                market_id = str(row[0]) if row[0] else None
                detected_at = row[1]
                if market_id and detected_at:
                    # Convert wall-clock age to monotonic equivalent
                    age_s = (now_utc - detected_at).total_seconds()
                    if age_s < cooldown_s:
                        self._cooldowns[market_id] = now_mono - age_s
                        loaded += 1
            if loaded:
                logger.info("LiveTrigger: loaded cooldowns from DB", count=loaded)
        except Exception as exc:
            logger.debug("LiveTrigger: cooldown DB load failed (non-critical): %s", exc)
