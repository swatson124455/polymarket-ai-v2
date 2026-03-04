"""
Sports Bankroll Manager — Kelly sizing + hard caps.

Phase 2: Full implementation.

Formula:
  1. Get kelly_fraction from adaptive_kelly.get_kelly_fraction(sport, market_type)
  2. edge = fair_prob - market_price
  3. kelly_bet = kelly_fraction * SPORTS_TOTAL_CAPITAL * (edge / fair_prob)
  4. Apply per-bet cap: min(kelly_bet, SPORTS_MAX_BET_USD)
  5. Check daily spent via OrderGateway._daily_exposure_usd["SportsInjuryBot"]
  6. Apply daily cap: min(capped_bet, max(0, SPORTS_MAX_DAILY_USD - daily_spent))
  7. Return final size (0.0 → skip trade)

Hard caps:
  Per-bet: SPORTS_MAX_BET_USD (default $100)
  Daily:   SPORTS_MAX_DAILY_USD (default $500)

Daily totals read from OrderGateway._daily_exposure_usd to avoid
double-counting with the main portfolio limits.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from structlog import get_logger

from config.settings import settings  # noqa: F401 — kept at module level for patching in tests

logger = get_logger()


class SportsBankrollManager:
    """
    Per-sport bankroll manager with Kelly sizing and hard USD caps.

    Usage::
        mgr = SportsBankrollManager(order_gateway)
        size = await mgr.get_bet_size(
            fair_prob=0.65, market_price=0.55, sport="nba"
        )
    """

    def __init__(self, order_gateway=None) -> None:
        self._gw = order_gateway   # base_engine/execution/order_gateway.py instance
        self._daily_lock = asyncio.Lock()

    async def get_bet_size(
        self,
        fair_prob: float,
        market_price: float,
        sport: str,
        market_type: str = "moneyline",
        db=None,
    ) -> float:
        """
        Compute Kelly-sized bet in USD, applying hard caps.

        Args:
            fair_prob:    Our estimated probability (0.5 + abs(edge) from impact table).
            market_price: Current market price (0–1).
            sport:        nba / nfl / mlb / nhl / soccer / tennis.
            market_type:  moneyline / futures / injury_prop / etc.
            db:           Database instance (injected by caller).

        Returns:
            Bet size in USD (0.0 means do not bet).
        """
        # Validate inputs
        edge = fair_prob - market_price
        if edge <= 0 or fair_prob <= 0 or fair_prob >= 1.0:
            return 0.0
        if market_price <= 0 or market_price >= 1.0:
            return 0.0

        # Step 1: Get Kelly fraction (adaptive per calibration)
        try:
            from sports.kelly.adaptive_kelly import get_kelly_fraction
            kelly_fraction = await asyncio.wait_for(
                get_kelly_fraction(sport, market_type, db=db),
                timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception):
            kelly_fraction = float(getattr(settings, "SPORTS_KELLY_DEFAULT_FRACTION", 0.25))  # uses module-level settings

        # Step 2: Kelly formula
        capital = float(getattr(settings, "SPORTS_TOTAL_CAPITAL", 10000.0))
        max_bet = float(getattr(settings, "SPORTS_MAX_BET_USD", 100.0))
        max_daily = float(getattr(settings, "SPORTS_MAX_DAILY_USD", 500.0))

        # Full Kelly: f* = (bp - q) / b where b = (1/market_price - 1)
        # Simplified for binary: f* = edge / fair_prob (fractional Kelly applied by kelly_fraction)
        kelly_bet = kelly_fraction * capital * (edge / fair_prob)

        # Step 3: Per-bet cap
        kelly_bet = min(kelly_bet, max_bet)

        # Step 4: Daily cap check — use lock-guarded accessor (I63)
        daily_spent = await self.get_daily_sports_exposure()
        remaining_daily = max(0.0, max_daily - daily_spent)
        final_size = min(kelly_bet, remaining_daily)

        # Step 5: Minimum meaningful bet
        if final_size < 1.0:
            return 0.0

        result = round(final_size, 2)

        logger.debug(
            "SportsBankrollManager.get_bet_size",
            sport=sport,
            fair_prob=round(fair_prob, 4),
            market_price=round(market_price, 4),
            edge=round(edge, 4),
            kelly_fraction=kelly_fraction,
            kelly_bet=round(kelly_bet, 2),
            daily_spent=round(daily_spent, 2),
            remaining_daily=round(remaining_daily, 2),
            final_size=result,
        )

        return result

    async def get_daily_sports_exposure(self) -> float:
        """
        I63: Lock-guarded read of today's sports exposure from OrderGateway._daily_exposure_usd.

        Always use this method instead of reading _daily_exposure_usd directly to prevent
        race conditions when multiple sports bots call get_bet_size() concurrently.

        Returns 0.0 if no gateway or no sports exposure tracked.
        """
        async with self._daily_lock:
            return self._get_daily_spent()

    def _get_daily_spent(self) -> float:
        """
        Raw (unlocked) read today's sports exposure from OrderGateway._daily_exposure_usd.
        Use get_daily_sports_exposure() for concurrent-safe access.
        """
        if self._gw is None:
            return 0.0
        daily_exposure = getattr(self._gw, "_daily_exposure_usd", {})
        # Sum all sports bot contributions
        total = 0.0
        for bot_name in ("SportsInjuryBot", "SportsLiveBot", "SportsArbBot"):
            total += float(daily_exposure.get(bot_name, 0.0))
        return total
