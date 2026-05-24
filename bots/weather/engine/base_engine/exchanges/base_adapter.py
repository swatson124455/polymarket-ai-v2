"""
Abstract base class for all exchange/platform adapters.

Each prediction market venue (Polymarket, Kalshi, ForecastEx, Coinbase)
implements this interface so CrossPlatformArbBot and ArbScanner can
interact with all of them uniformly.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional

from bots.weather.engine.base_engine.exchanges.models import (
    FeeSchedule,
    MarketSnapshot,
    OrderBook,
    OrderResult,
    PositionSnapshot,
)


class ExchangeAdapter(ABC):
    """
    Unified interface for prediction market platforms.

    Implementations must be async-safe and handle their own rate limiting,
    authentication, and error mapping.
    """

    # ── Market data ─────────────────────────────────────────────────────

    @abstractmethod
    async def get_markets(self, limit: int = 200) -> List[MarketSnapshot]:
        """Fetch active markets from the platform."""
        ...

    @abstractmethod
    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Fetch order book for a specific market."""
        ...

    # ── Trading ─────────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(
        self,
        market_id: str,
        side: str,
        size: float,
        price: float,
    ) -> OrderResult:
        """Place a limit order. Returns OrderResult with success/failure."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled successfully."""
        ...

    # ── Account ─────────────────────────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> List[PositionSnapshot]:
        """Fetch all open positions on the platform."""
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        """Get available trading balance (in USD or equivalent)."""
        ...

    # ── Platform info ───────────────────────────────────────────────────

    @abstractmethod
    def fee_schedule(self) -> FeeSchedule:
        """Return the platform's fee schedule."""
        ...

    @abstractmethod
    def platform_name(self) -> str:
        """Canonical platform name (e.g. 'polymarket', 'kalshi')."""
        ...

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def init(self) -> None:
        """Optional async initialization (auth, session creation)."""
        pass

    async def close(self) -> None:
        """Clean up resources (HTTP clients, WebSocket connections)."""
        pass

    def is_enabled(self) -> bool:
        """Return True if this adapter is configured and ready."""
        return True
