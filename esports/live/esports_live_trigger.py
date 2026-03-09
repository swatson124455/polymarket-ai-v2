"""
Esports Live Trigger — enforces cooldowns + per-match caps + places orders.

Mirrors sports/live/live_trigger.py pattern.

Cooldown: 60s between bets on the same match (configurable).
Max bets per match: 5 (configurable).
Max bets per map: 2 (configurable).

Usage::
    trigger = EsportsLiveTrigger()
    await trigger.process_event(event, bot=esports_live_bot, db=db)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from structlog import get_logger
from config.settings import settings

logger = get_logger()

_DEFAULT_COOLDOWN = float(getattr(settings, "ESPORTS_LIVE_COOLDOWN_SECONDS", 60.0))
_DEFAULT_MAX_PER_MATCH = int(getattr(settings, "ESPORTS_LIVE_MAX_PER_MATCH", 5))
_DEFAULT_MAX_PER_MAP = int(getattr(settings, "ESPORTS_LIVE_MAX_PER_MAP", 2))


class EsportsLiveTrigger:
    """
    Enforces cooldown and caps before placing live esports bets.
    """

    def __init__(self) -> None:
        self._last_bet_time: Dict[str, float] = {}          # match_id → monotonic timestamp
        self._bets_per_match: Dict[str, int] = {}            # match_id → count
        self._bets_per_map: Dict[str, int] = {}              # "match_id:map_num" → count
        self._cooldown: float = _DEFAULT_COOLDOWN
        self._max_per_match: int = _DEFAULT_MAX_PER_MATCH
        self._max_per_map: int = _DEFAULT_MAX_PER_MAP

    def configure(
        self,
        cooldown: Optional[float] = None,
        max_per_match: Optional[int] = None,
        max_per_map: Optional[int] = None,
    ) -> None:
        """Override default limits."""
        if cooldown is not None:
            self._cooldown = cooldown
        if max_per_match is not None:
            self._max_per_match = max_per_match
        if max_per_map is not None:
            self._max_per_map = max_per_map

    async def process_event(
        self,
        event,
        bot=None,
        db=None,
        scanner=None,
        bankroll_mgr=None,
    ) -> bool:
        """
        Process a live event: check cooldowns, find market, size bet, place order.

        Args:
            event: EsportsLiveEvent from the detector.
            bot: The EsportsLiveBot instance (for place_order).
            db: Database session.
            scanner: EsportsMarketScanner for finding Polymarket markets.
            bankroll_mgr: EsportsBankrollManager for sizing.

        Returns:
            True if a bet was placed, False if skipped.
        """
        from esports.live.esports_event_detector import EsportsLiveEvent
        if not isinstance(event, EsportsLiveEvent):
            return False

        match_id = event.match_id
        map_key = f"{match_id}:{event.map_number}"
        now = time.monotonic()

        # ── Cooldown check ──────────────────────────────────────────────
        last = self._last_bet_time.get(match_id, 0.0)
        if now - last < self._cooldown:
            logger.debug(
                "EsportsLiveTrigger: cooldown active",
                match_id=match_id,
                seconds_remaining=round(self._cooldown - (now - last), 1),
            )
            return False

        # ── Per-match cap ───────────────────────────────────────────────
        match_count = self._bets_per_match.get(match_id, 0)
        if match_count >= self._max_per_match:
            logger.debug(
                "EsportsLiveTrigger: max bets per match reached",
                match_id=match_id,
                count=match_count,
            )
            return False

        # ── Per-map cap ─────────────────────────────────────────────────
        map_count = self._bets_per_map.get(map_key, 0)
        if map_count >= self._max_per_map:
            logger.debug(
                "EsportsLiveTrigger: max bets per map reached",
                match_id=match_id,
                map_number=event.map_number,
                count=map_count,
            )
            return False

        # ── Find matching Polymarket market ─────────────────────────────
        market_id = None
        token_id = None
        price = None

        if scanner:
            try:
                markets = await asyncio.wait_for(
                    scanner.find_markets_for_match(match_id, event.game, db=db),
                    timeout=5.0,
                )
                if markets:
                    # Use first matching market
                    m = markets[0]
                    market_id = m.get("market_id")
                    token_id = m.get("token_id")
                    price = m.get("price")
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsLiveTrigger: market scan failed", error=str(exc))

        if not market_id or not token_id:
            logger.debug(
                "EsportsLiveTrigger: no matching market found",
                match_id=match_id,
                game=event.game,
            )
            return False

        # ── Size the bet ────────────────────────────────────────────────
        size = 0.0
        if bankroll_mgr:
            try:
                size = await asyncio.wait_for(
                    bankroll_mgr.get_bet_size(
                        fair_prob=event.confidence,
                        market_price=price or 0.5,
                        game=event.game,
                        market_type="live_event",
                        db=db,
                    ),
                    timeout=3.0,
                )
            except (asyncio.TimeoutError, Exception):
                size = 0.0

        if size <= 0:
            return False

        # ── Place the order ─────────────────────────────────────────────
        if bot:
            try:
                order = await bot.place_order(
                    market_id=market_id,
                    token_id=str(token_id),
                    side=event.market_side,
                    size=size,
                    price=price,
                    confidence=event.confidence,
                )
                if order and order.get("success"):
                    self._last_bet_time[match_id] = now
                    self._bets_per_match[match_id] = match_count + 1
                    self._bets_per_map[map_key] = map_count + 1

                    logger.info(
                        "EsportsLiveTrigger: bet placed",
                        match_id=match_id,
                        game=event.game,
                        event_type=event.event_type,
                        side=event.market_side,
                        size=round(size, 2),
                        confidence=round(event.confidence, 3),
                    )
                    return True
            except Exception as exc:
                logger.warning("EsportsLiveTrigger: order failed", error=str(exc))

        return False

    def prune_cooldowns(self) -> None:
        """Remove expired cooldown entries to prevent memory growth."""
        now = time.monotonic()
        expired = [k for k, v in self._last_bet_time.items() if now - v > self._cooldown * 10]
        for k in expired:
            del self._last_bet_time[k]
            self._bets_per_match.pop(k, None)

        # Prune map counts too
        expired_maps = [k for k in self._bets_per_map if k.split(":")[0] in expired]
        for k in expired_maps:
            del self._bets_per_map[k]
