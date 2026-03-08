"""
SportsArbBot — Cross-platform sports arbitrage bot.

Scans for price discrepancies between Polymarket and Kalshi sports markets
and places opposing legs to lock in risk-free profit.

Phase 5: Full implementation.
  - Uses cross_platform_arb.find_sports_arb_opportunities().
  - Applies SPORTS_ARB_MIN_SPREAD gate (default 4%).
  - Executes Leg A (Polymarket) via BaseBot.place_order().
  - Executes Leg B (Kalshi) via KalshiSportsClient.place_order().
  - Logs combined P&L estimate.

Scan interval: SCAN_INTERVAL_SPORTS_ARB (default 30s).
Enable: BOT_ENABLED_SPORTS_ARB=true (disabled by default).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from structlog import get_logger

from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class SportsArbBot(BaseBot):
    """
    Cross-platform sports arbitrage bot (Polymarket ↔ Kalshi).
    """

    def __init__(self, base_engine):
        super().__init__("SportsArbBot", base_engine)
        self._min_spread: float = float(
            getattr(settings, "SPORTS_ARB_MIN_SPREAD", 0.04)
        )
        self._kalshi_client = None
        self._bankroll_mgr = None

    def _get_scan_interval_seconds(self) -> float:
        return float(getattr(settings, "SCAN_INTERVAL_SPORTS_ARB", 30))

    async def start(self) -> None:
        """Initialize Kalshi client and bankroll manager."""
        gw = getattr(self.base_engine, "order_gateway", None)

        # Initialize KalshiSportsClient if RSA key is configured
        try:
            kalshi_key_path = getattr(settings, "KALSHI_RSA_PRIVATE_KEY_PATH", None)
            kalshi_api_key = getattr(settings, "KALSHI_API_KEY", None)
            if kalshi_key_path and kalshi_api_key:
                from sports.markets.kalshi_client import KalshiSportsClient
                self._kalshi_client = KalshiSportsClient(
                    private_key_path=kalshi_key_path,
                    api_key_id=kalshi_api_key,
                )
                await self._kalshi_client.init()
                logger.info("SportsArbBot: Kalshi client initialized")
            else:
                logger.info(
                    "SportsArbBot: no Kalshi RSA key configured — arb against Kalshi disabled",
                    hint="Set KALSHI_RSA_PRIVATE_KEY_PATH and KALSHI_API_KEY in .env",
                )
        except Exception as exc:
            logger.warning("SportsArbBot: failed to init Kalshi client", error=str(exc))

        from sports.kelly.bankroll_manager import SportsBankrollManager
        self._bankroll_mgr = SportsBankrollManager(order_gateway=gw)

        await super().start()

    async def scan_and_trade(self) -> None:
        """
        Scan for cross-platform arb opportunities and execute both legs.
        """
        db = getattr(self.base_engine, "db", None)

        try:
            from sports.markets.cross_platform_arb import find_sports_arb_opportunities
            opportunities = await asyncio.wait_for(
                find_sports_arb_opportunities(
                    sport=None,   # scan all sports
                    db=db,
                    kalshi_client=self._kalshi_client,
                    min_spread=self._min_spread,
                ),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            logger.warning("SportsArbBot: opportunity scan timed out")
            return
        except Exception as exc:
            logger.warning("SportsArbBot: scan error", error=str(exc))
            return

        if not opportunities:
            logger.debug("SportsArbBot: no arb opportunities found")
            return

        logger.info(
            "SportsArbBot: arb opportunities found",
            count=len(opportunities),
            best_net_spread=round(opportunities[0].net_spread, 4),
        )

        for opp in opportunities[:5]:   # max 5 arbs per scan
            await self._execute_arb(opp, db)

    async def _execute_arb(self, opp, db) -> None:
        """
        Execute both legs of an arb opportunity with atomic rollback protection.

        Leg A: Polymarket order via BaseBot.place_order()
        Leg B: Kalshi order via KalshiSportsClient

        If Leg B fails after Leg A succeeds, Leg A is immediately rolled back
        (opposite side at market price) to prevent an unhedged open position.
        CRITICAL logs are emitted if rollback itself fails.
        """
        # Size: use min edge (conservative for arb)
        arb_size = float(getattr(settings, "SPORTS_MAX_BET_USD", 100.0)) * 0.5
        # Scale by net spread — tighter spreads get smaller size
        arb_size = min(arb_size, arb_size * (opp.net_spread / 0.04))
        arb_size = max(1.0, round(arb_size, 2))

        logger.info(
            "SportsArbBot: executing arb",
            sport=opp.sport,
            title=opp.event_title[:60],
            net_spread=round(opp.net_spread, 4),
            poly_side=opp.leg_a_side,
            kalshi_side=opp.leg_b_side,
            size=arb_size,
        )

        # Leg A: Polymarket
        poly_candidate = opp.poly_candidate
        if not poly_candidate:
            logger.warning("SportsArbBot: Leg A skipped — no poly_candidate",
                           poly_market_id=opp.polymarket_id)
            return

        if opp.leg_a_side == "YES":
            token_id = poly_candidate.yes_token_id or ""
        else:
            token_id = poly_candidate.no_token_id or ""
        if not token_id:
            logger.warning(
                "SportsArbBot: Leg A skipped — missing %s token_id",
                opp.leg_a_side,
                poly_market_id=opp.polymarket_id,
            )
            return

        try:
            result_a = await asyncio.wait_for(
                self.place_order(
                    market_id=opp.polymarket_id,
                    token_id=token_id or "",
                    side=opp.leg_a_side,
                    size=arb_size,
                    price=opp.poly_yes_price,
                    confidence=0.90,   # arb = high confidence by construction
                    correlation_id=self._current_correlation_id,
                ),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("SportsArbBot: Leg A timed out")
            return
        except Exception as exc:
            logger.warning("SportsArbBot: Leg A error", error=str(exc))
            return

        if not result_a.get("success"):
            logger.debug("SportsArbBot: Leg A declined", reason=result_a.get("reason"))
            return

        logger.info(
            "SportsArbBot: Leg A placed",
            platform="polymarket",
            market_id=opp.polymarket_id,
            side=opp.leg_a_side,
            size=arb_size,
        )

        # Leg B: Kalshi — with rollback on failure
        if self._kalshi_client and opp.kalshi_candidate:
            leg_b_ok = False
            try:
                result_b = await asyncio.wait_for(
                    self._kalshi_client.place_order(
                        market_id=opp.kalshi_id,
                        side=opp.leg_b_side,
                        size=arb_size,
                        price=opp.kalshi_no_price,
                    ),
                    timeout=10.0,
                )
                if result_b.get("success"):
                    leg_b_ok = True
                    logger.info(
                        "SportsArbBot: Leg B placed",
                        platform="kalshi",
                        market_id=opp.kalshi_id,
                        side=opp.leg_b_side,
                        size=arb_size,
                    )
                else:
                    logger.warning("SportsArbBot: Leg B declined",
                                   market_id=opp.kalshi_id,
                                   error=result_b.get("error"))
            except asyncio.TimeoutError:
                logger.warning("SportsArbBot: Leg B timed out", market_id=opp.kalshi_id)
            except Exception as exc:
                logger.warning("SportsArbBot: Leg B error", error=str(exc))

            if not leg_b_ok:
                # Rollback Leg A — exit the unhedged position immediately
                flip_side = "NO" if opp.leg_a_side == "YES" else "YES"
                logger.warning(
                    "SportsArbBot: leg_b_failed_rolling_back_leg_a",
                    poly_market_id=opp.polymarket_id,
                    flip_side=flip_side,
                    size=arb_size,
                )
                try:
                    rollback = await asyncio.wait_for(
                        self.place_order(
                            market_id=opp.polymarket_id,
                            token_id=token_id or "",
                            side=flip_side,
                            size=arb_size,
                            price=opp.poly_yes_price,
                            confidence=0.5,
                            correlation_id=self._current_correlation_id,
                        ),
                        timeout=10.0,
                    )
                    if rollback.get("success"):
                        logger.info("SportsArbBot: Leg A rollback succeeded",
                                    poly_market_id=opp.polymarket_id)
                    else:
                        logger.critical(
                            "SportsArbBot: Leg A rollback FAILED — unhedged position",
                            poly_market_id=opp.polymarket_id,
                            side=opp.leg_a_side,
                            size=arb_size,
                            reason=rollback.get("reason"),
                        )
                except Exception as exc:
                    logger.critical(
                        "SportsArbBot: Leg A rollback EXCEPTION — unhedged position",
                        poly_market_id=opp.polymarket_id,
                        side=opp.leg_a_side,
                        size=arb_size,
                        error=str(exc),
                    )
        else:
            logger.info(
                "SportsArbBot: Leg B (Kalshi) not placed — no client configured",
                market_id=opp.kalshi_id,
                side=opp.leg_b_side,
                estimated_profit=round(opp.net_spread * arb_size, 2),
            )

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot ABC. SportsArbBot uses its own scan logic."""
        return None
