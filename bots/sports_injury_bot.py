"""
SportsInjuryBot — injury and roster-move-driven sports betting bot.

Monitors injury/news events via the NewsAggregator pipeline (Twitter, RSS,
Reddit, Discord, Telegram) and places bets when player availability or
roster status creates a pricing edge on Polymarket/Kalshi sports markets.

Core alpha: news breaks via beat reporters 15–60 min before prediction
markets reprice. Speed of information detection is the primary edge.

Phase 2: Full implementation.
  - Starts NewsAggregator in start() as a background task.
  - scan_and_trade() drains _injury_queue (max 10 events per 10s scan).
  - Uses SPORT_IMPACT_TABLE for fixed edge until projections are live (Phase 3+).
  - Calls SportsMarketScanner → find_markets_for_game → get_bet_size → place_order.
  - NFL offseason: scans for free_agent_move / draft / combine markets.

Scan interval: SCAN_INTERVAL_SPORTS_INJURY (default 10s).
Enable: BOT_ENABLED_SPORTS_INJURY=true (disabled by default).
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
from structlog import get_logger

from bots.base_bot import BaseBot
from config.settings import settings
from sports.data.injury_store import InjuryEvent

logger = get_logger()


# ─── Fixed sport impact table ─────────────────────────────────────────────────
# Maps (sport, detected_status) → edge (probability delta from fair 0.50).
# Used as fallback until projection engine (Phase 3+) is live.
# Positive = bet YES on the winning-team / non-injured player.
# Negative or large = bet NO on the affected team market.

SPORT_IMPACT_TABLE: Dict[str, Dict[str, float]] = {
    "nba": {
        "out":          0.10,   # key player out → opponent +10pp
        "doubtful":     0.06,
        "questionable": 0.03,
        "day-to-day":   0.02,
        "sp_scratch":   0.00,   # not applicable in NBA
        "goalie_swap":  0.00,
        "free_agent_move": 0.05,
        "withdrawal":   0.00,
        "retirement":   0.00,
    },
    "nfl": {
        "out":            0.12,
        "doubtful":       0.07,
        "questionable":   0.04,
        "day-to-day":     0.03,
        "free_agent_move": 0.08,  # NFL offseason: destination affects futures markets
        "sp_scratch":     0.00,
        "goalie_swap":    0.00,
        "withdrawal":     0.00,
        "retirement":     0.00,
    },
    "mlb": {
        "out":          0.08,
        "sp_scratch":   0.15,   # Starting pitcher change is highest impact in MLB
        "closer_out":   0.03,
        "doubtful":     0.05,
        "questionable": 0.02,
        "day-to-day":   0.02,
        "goalie_swap":  0.00,
        "free_agent_move": 0.04,
        "withdrawal":   0.00,
        "retirement":   0.00,
    },
    "nhl": {
        "out":         0.08,
        "goalie_swap": 0.12,   # Goalie change has outsized impact in hockey
        "doubtful":    0.05,
        "questionable": 0.02,
        "day-to-day":  0.02,
        "sp_scratch":  0.00,
        "free_agent_move": 0.04,
        "withdrawal":  0.00,
        "retirement":  0.00,
    },
    "soccer": {
        "out":          0.07,
        "out_striker":  0.07,
        "out_midfielder": 0.05,
        "out_defender": 0.04,
        "doubtful":     0.04,
        "questionable": 0.02,
        "day-to-day":   0.02,
        "sp_scratch":   0.00,
        "goalie_swap":  0.06,
        "free_agent_move": 0.03,
        "withdrawal":   0.00,
        "retirement":   0.00,
    },
    "tennis": {
        "withdrawal":   1.00,   # Market resolves for the other player
        "retirement":   0.80,   # In-game retirement → opponent wins
        "out":          0.95,
        "doubtful":     0.50,
        "questionable": 0.20,
        "day-to-day":   0.10,
        "sp_scratch":   0.00,
        "goalie_swap":  0.00,
        "free_agent_move": 0.00,
    },
    "unknown": {
        "out":          0.08,
        "doubtful":     0.05,
        "questionable": 0.02,
        "day-to-day":   0.02,
        "sp_scratch":   0.10,
        "goalie_swap":  0.10,
        "free_agent_move": 0.05,
        "withdrawal":   0.80,
        "retirement":   0.70,
    },
}


class SportsInjuryBot(BaseBot):
    """
    Event-driven sports injury / roster-move betting bot.

    Receives InjuryEvent objects from the NewsAggregator and converts
    them to bet placements when edge exceeds SPORTS_MIN_EDGE.
    """

    def __init__(self, base_engine):
        super().__init__("SportsInjuryBot", base_engine)
        # Queue for resolved InjuryEvent objects from NewsAggregator
        self._injury_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._aggregator = None
        self._scanner = None
        self._bankroll_mgr = None

    def _get_scan_interval_seconds(self) -> float:
        return float(getattr(settings, "SCAN_INTERVAL_SPORTS_INJURY", 10))

    async def start(self) -> None:
        """
        Start the NewsAggregator pipeline before entering the scan loop.

        The aggregator starts Twitter/RSS/Reddit/Discord/Telegram monitors
        in background tasks and feeds resolved InjuryEvents into _injury_queue.
        """
        # Initialize scanner and bankroll manager
        db = getattr(self.base_engine, "db", None)
        gw = getattr(self.base_engine, "order_gateway", None)

        from sports.markets.sports_market_scanner import SportsMarketScanner
        self._scanner = SportsMarketScanner(db=db)

        from sports.kelly.bankroll_manager import SportsBankrollManager
        self._bankroll_mgr = SportsBankrollManager(order_gateway=gw)

        # Start news aggregator
        try:
            from sports.news.news_aggregator import NewsAggregator
            self._aggregator = NewsAggregator(
                injury_bot_queue=self._injury_queue,
                db=db,
            )
            await self._aggregator.start()
            logger.info("SportsInjuryBot: NewsAggregator started")
        except Exception as exc:
            logger.warning("SportsInjuryBot: failed to start NewsAggregator", error=str(exc))

        # Delegate to BaseBot scan loop
        await super().start()

    async def stop(self) -> None:
        """Stop aggregator and scan loop."""
        if self._aggregator:
            try:
                await self._aggregator.stop()
            except Exception as exc:
                logger.debug("SportsInjuryBot: aggregator stop error (non-blocking): %s", exc)
        await super().stop()

    async def scan_and_trade(self) -> None:
        """
        Drain up to 10 injury events per scan cycle and process each.

        Uses get_nowait() (non-blocking) so the scan loop never blocks
        waiting for events. Slow processing must not block event reception.
        """
        processed = 0
        while processed < 10:
            try:
                event: InjuryEvent = self._injury_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await self._process_injury_event(event)
            except Exception as exc:
                logger.warning(
                    "SportsInjuryBot: event processing error",
                    player=event.player_raw,
                    sport=event.sport,
                    error=str(exc),
                )
            processed += 1

        if processed:
            logger.debug("SportsInjuryBot: processed events in scan", count=processed)

    async def _process_injury_event(self, event: InjuryEvent) -> None:
        """
        Full processing pipeline for one InjuryEvent:
          1. Look up edge from SPORT_IMPACT_TABLE.
          2. Check SPORTS_MIN_EDGE and SPORTS_MIN_CONFIDENCE gates.
          3. Find markets via SportsMarketScanner.
          4. Size via SportsBankrollManager.
          5. Place order via BaseBot.place_order().
          6. Mark bet triggered in injury_store.
        """
        sport = event.sport or "unknown"
        status = event.detected_status or "unknown"
        db = getattr(self.base_engine, "db", None)

        # Step 1: Edge from impact table
        sport_table = SPORT_IMPACT_TABLE.get(sport, SPORT_IMPACT_TABLE["unknown"])
        edge = sport_table.get(status, 0.0)

        if abs(edge) == 0.0:
            logger.debug(
                "SportsInjuryBot: no impact for status",
                sport=sport,
                status=status,
            )
            return

        # Step 2: Gate checks
        min_edge = float(getattr(settings, "SPORTS_MIN_EDGE", 0.05))
        min_conf = float(getattr(settings, "SPORTS_MIN_CONFIDENCE", 0.60))

        if abs(edge) < min_edge:
            logger.debug(
                "SportsInjuryBot: edge below minimum",
                edge=edge, min_edge=min_edge,
            )
            return

        if event.confidence < min_conf:
            logger.debug(
                "SportsInjuryBot: confidence below minimum",
                confidence=event.confidence, min_conf=min_conf,
            )
            return

        # Step 3: Find markets
        game_id = str(event.game_id) if event.game_id else "unknown"
        try:
            markets = await asyncio.wait_for(
                self._scanner.find_markets_for_game(
                    game_id=game_id,
                    sport=sport,
                    player_name=event.player_raw or None,
                    db=db,
                ),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("SportsInjuryBot: market scanner timed out", game_id=game_id)
            return

        if not markets:
            logger.debug(
                "SportsInjuryBot: no markets found",
                game_id=game_id, sport=sport,
                player=event.player_raw,
            )
            return

        # Step 4: Determine side
        # edge > 0 → news is bad for the team → bet NO on team winning (or YES on opponent)
        # Special case: tennis withdrawal → YES on the opponent market
        side = "NO" if edge > 0 else "YES"
        fair_prob = 0.5 + abs(edge)

        bet_placed = False
        for market in markets[:3]:   # max 3 markets per injury event
            # Step 5: Size
            market_price = market.current_price or 0.50
            try:
                size = await asyncio.wait_for(
                    self._bankroll_mgr.get_bet_size(
                        fair_prob=fair_prob,
                        market_price=market_price,
                        sport=sport,
                        market_type=market.market_type or "moneyline",
                        db=db,
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("SportsInjuryBot: bankroll manager timed out")
                continue

            if size <= 0.0:
                logger.debug(
                    "SportsInjuryBot: zero size from bankroll manager",
                    market_id=market.market_id,
                )
                continue

            # Step 6: Place order
            token_id = market.yes_token_id if side == "YES" else (market.no_token_id or market.yes_token_id or "")
            try:
                result = await asyncio.wait_for(
                    self.place_order(
                        market_id=market.market_id,
                        token_id=token_id or "",
                        side=side,
                        size=size,
                        price=market_price,
                        confidence=event.confidence,
                        prediction=fair_prob,
                        correlation_id=self._current_correlation_id,
                    ),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "SportsInjuryBot: place_order timed out",
                    market_id=market.market_id,
                )
                continue

            if result.get("success"):
                bet_placed = True
                logger.info(
                    "SportsInjuryBot: bet placed",
                    player=event.player_raw,
                    sport=sport,
                    status=status,
                    edge=round(edge, 4),
                    side=side,
                    size=size,
                    market_id=market.market_id,
                    source=event.source,
                    confidence=round(event.confidence, 3),
                )
                # Mark injury event as bet-triggered
                if event.id and db:
                    try:
                        from sports.data.injury_store import mark_bet_triggered
                        await asyncio.wait_for(
                            mark_bet_triggered(event.id, market.market_id, db=db),
                            timeout=5.0,
                        )
                    except Exception as e:
                        logger.debug("mark_bet_triggered failed (non-blocking): %s", e)

        if not bet_placed:
            logger.debug(
                "SportsInjuryBot: no bets placed for event",
                player=event.player_raw,
                sport=sport,
                status=status,
            )

    def enqueue_injury_event(self, event: InjuryEvent) -> None:
        """
        Directly enqueue an InjuryEvent (used by external callers or tests).

        Uses put_nowait() — never blocks. Logs warning if queue is full.
        """
        try:
            self._injury_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "SportsInjuryBot: injury queue full — event dropped",
                player=event.player_raw,
                sport=event.sport,
            )

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot ABC. Sports injury bot is event-driven, not market-scan-driven."""
        return None
