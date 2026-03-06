"""
Esports Bankroll Manager — Kelly sizing + hard caps + drawdown compression.

Mirrors sports/kelly/bankroll_manager.py pattern exactly.
Separate capital pool from main risk_manager and sports bots.

Formula:
  1. Get kelly_fraction from esports_calibration table (per game, market_type)
  2. edge = fair_prob - market_price
  3. kelly_bet = kelly_fraction * ESPORTS_TOTAL_CAPITAL * (edge / fair_prob)
  4. Apply drawdown compression: kelly_bet *= drawdown_factor(consecutive_losses)
  5. Apply per-bet cap: min(kelly_bet, ESPORTS_MAX_BET_USD)
  6. Check daily spent via OrderGateway._daily_exposure_usd
  7. Apply daily cap: min(capped_bet, max(0, ESPORTS_MAX_DAILY_USD - daily_spent))
  8. Return final size (0.0 → skip trade)

Hard caps:
  Per-bet: ESPORTS_MAX_BET_USD (default $100)
  Daily:   ESPORTS_MAX_DAILY_USD (default $500)

Drawdown compression:
  0 consecutive losses: 1.00x (full Kelly)
  3 consecutive losses: 0.75x
  5 consecutive losses: 0.50x
  8+ consecutive losses: 0.25x
"""
from __future__ import annotations

import asyncio
from typing import Dict

from structlog import get_logger

from config.settings import settings  # noqa: F401 — kept at module level for patching in tests

logger = get_logger()

# Drawdown compression schedule: (min_losses, factor)
# Applied in order — first matching threshold wins
_DRAWDOWN_SCHEDULE = [
    (8, 0.25),   # 8+ losses: quarter Kelly
    (5, 0.50),   # 5-7 losses: half Kelly
    (3, 0.75),   # 3-4 losses: three-quarter Kelly
]


class EsportsBankrollManager:
    """
    Per-game bankroll manager with Kelly sizing and hard USD caps.

    Usage::
        mgr = EsportsBankrollManager(order_gateway)
        size = await mgr.get_bet_size(
            fair_prob=0.65, market_price=0.55, game="lol"
        )
    """

    def __init__(self, order_gateway=None) -> None:
        self._gw = order_gateway
        self._daily_lock = asyncio.Lock()
        self._consecutive_losses: Dict[str, int] = {}  # game → consecutive loss count

    async def get_bet_size(
        self,
        fair_prob: float,
        market_price: float,
        game: str,
        market_type: str = "match_winner",
        db=None,
    ) -> float:
        """
        Compute Kelly-sized bet in USD, applying hard caps.

        Args:
            fair_prob:    Our estimated probability.
            market_price: Current market price (0–1).
            game:         lol / cs2 / dota2 / valorant.
            market_type:  match_winner / map_winner / live_event / etc.
            db:           Database instance (injected by caller).

        Returns:
            Bet size in USD (0.0 means do not bet).
        """
        edge = fair_prob - market_price
        if edge <= 0 or fair_prob <= 0 or fair_prob >= 1.0:
            return 0.0
        if market_price <= 0 or market_price >= 1.0:
            return 0.0

        # Step 1: Get Kelly fraction from calibration table
        kelly_fraction = await self._get_kelly_fraction(game, market_type, db)

        # Step 2: Kelly formula
        capital = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 5000.0))
        max_bet = float(getattr(settings, "ESPORTS_MAX_BET_USD", 100.0))
        max_daily = float(getattr(settings, "ESPORTS_MAX_DAILY_USD", 500.0))

        kelly_bet = kelly_fraction * capital * (edge / fair_prob)

        # Step 3: Drawdown compression
        dd_factor = self._compute_drawdown_factor(game)
        kelly_bet *= dd_factor

        # Step 4: Per-bet cap
        kelly_bet = min(kelly_bet, max_bet)

        # Step 4: Daily cap check
        daily_spent = await self.get_daily_esports_exposure()
        remaining_daily = max(0.0, max_daily - daily_spent)
        final_size = min(kelly_bet, remaining_daily)

        # Step 5: Minimum meaningful bet
        if final_size < 1.0:
            return 0.0

        result = round(final_size, 2)

        logger.debug(
            "EsportsBankrollManager.get_bet_size",
            game=game,
            fair_prob=round(fair_prob, 4),
            market_price=round(market_price, 4),
            edge=round(edge, 4),
            kelly_fraction=kelly_fraction,
            drawdown_factor=dd_factor,
            kelly_bet=round(kelly_bet, 2),
            daily_spent=round(daily_spent, 2),
            final_size=result,
        )

        return result

    async def get_daily_esports_exposure(self) -> float:
        """Lock-guarded read of today's esports exposure from OrderGateway."""
        async with self._daily_lock:
            return self._get_daily_spent()

    def _get_daily_spent(self) -> float:
        """Raw read of today's esports exposure."""
        if self._gw is None:
            return 0.0
        daily_exposure = getattr(self._gw, "_daily_exposure_usd", {})
        total = 0.0
        for bot_name in ("EsportsBot", "EsportsLiveBot", "EsportsSeriesBot"):
            total += float(daily_exposure.get(bot_name, 0.0))
        return total

    async def _get_kelly_fraction(
        self, game: str, market_type: str, db=None
    ) -> float:
        """Get Kelly fraction from calibration table, or default."""
        default = float(getattr(settings, "ESPORTS_KELLY_DEFAULT_FRACTION", 0.25))

        if db is None:
            return default

        try:
            from esports.data.esports_db import get_calibration
            cal = await asyncio.wait_for(
                get_calibration(db, game=game, market_type=market_type),
                timeout=3.0,
            )
            if cal and cal.get("kelly_fraction"):
                return float(cal["kelly_fraction"])
        except (asyncio.TimeoutError, Exception):
            pass

        return default

    def _compute_drawdown_factor(self, game: str) -> float:
        """
        Compute drawdown compression factor based on consecutive losses.

        Reduces bet sizing during losing streaks to protect capital.
        Returns 1.0 (full Kelly) when no drawdown, down to 0.25 at 8+ losses.
        """
        losses = self._consecutive_losses.get(game, 0)
        if losses <= 0:
            return 1.0
        for threshold, factor in _DRAWDOWN_SCHEDULE:
            if losses >= threshold:
                return factor
        return 1.0

    def record_outcome(self, game: str, won: bool) -> None:
        """
        Record a bet outcome for drawdown tracking.

        Call after each bet resolves. Resets consecutive losses on win.
        """
        if won:
            prev = self._consecutive_losses.get(game, 0)
            self._consecutive_losses[game] = 0
            if prev > 0:
                logger.info(
                    "EsportsBankrollManager: drawdown reset",
                    game=game,
                    previous_streak=prev,
                )
        else:
            self._consecutive_losses[game] = self._consecutive_losses.get(game, 0) + 1
            streak = self._consecutive_losses[game]
            factor = self._compute_drawdown_factor(game)
            if streak >= 3:
                logger.warning(
                    "EsportsBankrollManager: losing streak",
                    game=game,
                    consecutive_losses=streak,
                    drawdown_factor=factor,
                )
