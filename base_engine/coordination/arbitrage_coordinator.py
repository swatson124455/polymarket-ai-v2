"""
ArbitrageTransactionCoordinator: atomic multi-leg execution with reserve and rollback.
Used only by ArbitrageBot. Other bots place single orders via OrderGateway.
"""
import asyncio
import time
from typing import Any, Callable, Dict, Optional, Tuple
from structlog import get_logger
from config.settings import settings

logger = get_logger()


class ArbitrageTransactionCoordinator:
    """
    Reserves both legs atomically, re-validates price before execution,
    executes with rollback on second-leg failure (exit first leg).
    """

    def __init__(
        self,
        trade_coordinator: Any,
        place_order_fn: Callable[..., Any],
        reserving_bot_id: str,
    ):
        self.trade_coordinator = trade_coordinator
        self.place_order_fn = place_order_fn
        self.reserving_bot_id = reserving_bot_id

    async def _release_both(self, market_id: str) -> None:
        """Release YES and NO reservations for this market."""
        if self.trade_coordinator is None:
            return
        try:
            await self.trade_coordinator.release_reservation(
                market_id, "YES", bot_id=self.reserving_bot_id
            )
            await self.trade_coordinator.release_reservation(
                market_id, "NO", bot_id=self.reserving_bot_id
            )
        except Exception as e:
            logger.warning("Arb coordinator release failed: %s", e)

    async def execute_long_arbitrage(
        self,
        market_id: str,
        yes_token_id: str,
        no_token_id: str,
        yes_price: float,
        no_price: float,
        size: float,
        confidence: float,
        min_profit_threshold: float,
        price_fetched_at: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Reserve YES+NO, place BUY YES then BUY NO. On NO failure: exit YES and release.
        Re-validates price age; if stale, skips and releases.
        """
        max_age = getattr(settings, "ARB_MAX_PRICE_AGE_SECONDS", 5)
        if price_fetched_at is not None and (time.time() - price_fetched_at) > max_age:
            return False, "Price stale, skipping"
        if self.trade_coordinator is None:
            return await self._execute_legs_no_coordinator(
                market_id, yes_token_id, no_token_id, yes_price, no_price, size, confidence, "BUY", "BUY"
            )
        r1 = await self.trade_coordinator.reserve_position(
            market_id, "YES", token_id=yes_token_id, reserving_bot_id=self.reserving_bot_id
        )
        if not r1:
            return False, "Could not reserve YES"
        r2 = await self.trade_coordinator.reserve_position(
            market_id, "NO", token_id=no_token_id, reserving_bot_id=self.reserving_bot_id
        )
        if not r2:
            await self.trade_coordinator.release_reservation(
                market_id, "YES", bot_id=self.reserving_bot_id
            )
            return False, "Could not reserve NO"
        try:
            yes_order = await self.place_order_fn(
                market_id, yes_token_id, "BUY", size, yes_price, confidence
            )
            if not yes_order.get("success"):
                await self._release_both(market_id)
                return False, yes_order.get("error", "YES order failed")
            no_order = await self.place_order_fn(
                market_id, no_token_id, "BUY", size, no_price, confidence
            )
            if no_order.get("success"):
                return True, None
            no_retry = await self.place_order_fn(
                market_id, no_token_id, "BUY", size, no_price, confidence
            )
            if no_retry.get("success"):
                return True, None
            exit_order = await self.place_order_fn(
                market_id, yes_token_id, "SELL", size, yes_price, confidence
            )
            await self._release_both(market_id)
            if exit_order.get("success"):
                return False, "NO failed, exited YES"
            return False, "NO failed and exit YES failed"
        except Exception as e:
            await self._release_both(market_id)
            logger.exception("Long arb execution error")
            return False, str(e)

    async def _execute_legs_no_coordinator(
        self,
        market_id: str,
        token1: str,
        token2: str,
        price1: float,
        price2: float,
        size: float,
        confidence: float,
        side1: str,
        side2: str,
    ) -> Tuple[bool, Optional[str]]:
        """When no trade coordinator, execute legs sequentially (legacy behavior)."""
        o1 = await self.place_order_fn(
            market_id, token1, side1, size, price1, confidence
        )
        if not o1.get("success"):
            return False, o1.get("error", "First leg failed")
        o2 = await self.place_order_fn(
            market_id, token2, side2, size, price2, confidence
        )
        if o2.get("success"):
            return True, None
        exit_o = await self.place_order_fn(
            market_id,
            token1,
            "SELL" if side1 == "BUY" else "BUY",
            size,
            price1,
            confidence,
        )
        return False, "Second leg failed" + ("; exited first" if exit_o.get("success") else "")

    async def execute_short_arbitrage(
        self,
        market_id: str,
        yes_token_id: str,
        no_token_id: str,
        yes_price: float,
        no_price: float,
        size: float,
        confidence: float,
        price_fetched_at: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Reserve, place SELL YES then SELL NO; on NO failure exit YES (BUY back)."""
        max_age = getattr(settings, "ARB_MAX_PRICE_AGE_SECONDS", 5)
        if price_fetched_at is not None and (time.time() - price_fetched_at) > max_age:
            return False, "Price stale, skipping"
        if self.trade_coordinator is None:
            return await self._execute_legs_no_coordinator(
                market_id, yes_token_id, no_token_id, yes_price, no_price, size, confidence, "SELL", "SELL"
            )
        r1 = await self.trade_coordinator.reserve_position(
            market_id, "YES", token_id=yes_token_id, reserving_bot_id=self.reserving_bot_id
        )
        if not r1:
            return False, "Could not reserve YES"
        r2 = await self.trade_coordinator.reserve_position(
            market_id, "NO", token_id=no_token_id, reserving_bot_id=self.reserving_bot_id
        )
        if not r2:
            await self.trade_coordinator.release_reservation(
                market_id, "YES", bot_id=self.reserving_bot_id
            )
            return False, "Could not reserve NO"
        try:
            yes_order = await self.place_order_fn(
                market_id, yes_token_id, "SELL", size, yes_price, confidence
            )
            if not yes_order.get("success"):
                await self._release_both(market_id)
                return False, yes_order.get("error", "YES order failed")
            no_order = await self.place_order_fn(
                market_id, no_token_id, "SELL", size, no_price, confidence
            )
            if no_order.get("success"):
                return True, None
            no_retry = await self.place_order_fn(
                market_id, no_token_id, "SELL", size, no_price, confidence
            )
            if no_retry.get("success"):
                return True, None
            exit_order = await self.place_order_fn(
                market_id, yes_token_id, "BUY", size, yes_price, confidence
            )
            await self._release_both(market_id)
            if exit_order.get("success"):
                return False, "NO failed, covered YES"
            return False, "NO failed and cover YES failed"
        except Exception as e:
            await self._release_both(market_id)
            logger.exception("Short arb execution error")
            return False, str(e)
