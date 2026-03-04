"""
Shared data models for the cross-platform exchange adapter system.

All adapters normalize their platform-specific responses into these models
so downstream consumers (ArbScanner, CrossPlatformArbBot) work uniformly.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(frozen=True)
class OrderBookLevel:
    """Single price/size level on the order book."""
    price: float
    size: float


@dataclass
class OrderBook:
    """Normalized order book for a single market/outcome."""
    market_id: str
    platform: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    timestamp: Optional[datetime] = None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


@dataclass
class MarketSnapshot:
    """Normalized snapshot of a prediction market from any platform."""
    market_id: str
    platform: str
    question: str
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    volume: float = 0.0
    liquidity: float = 0.0
    end_date: Optional[datetime] = None
    category: str = ""
    resolved: bool = False
    # Platform-specific metadata
    extra: Dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return bool(self.market_id and self.question and self.yes_price is not None)


@dataclass(frozen=True)
class FeeSchedule:
    """Fee structure for a platform. All values in decimal (e.g. 0.015 = 1.5%)."""
    taker_fee: float = 0.0
    maker_fee: float = 0.0
    settlement_fee: float = 0.0
    platform: str = ""

    def total_round_trip(self) -> float:
        """Total cost of entering + exiting a position (worst case: taker both sides)."""
        return self.taker_fee * 2 + self.settlement_fee

    def net_price_after_fees(self, gross_price: float, side: str = "BUY") -> float:
        """Effective price after fees. For BUY: price goes up. For SELL: proceeds go down."""
        if side.upper() == "BUY":
            return gross_price * (1.0 + self.taker_fee)
        return gross_price * (1.0 - self.taker_fee)


@dataclass
class OrderResult:
    """Result of placing an order on any platform."""
    success: bool
    order_id: str = ""
    platform: str = ""
    market_id: str = ""
    side: str = ""
    size: float = 0.0
    price: float = 0.0
    filled_size: float = 0.0
    filled_price: float = 0.0
    error: str = ""
    # Platform-specific metadata
    extra: Dict = field(default_factory=dict)


@dataclass
class PositionSnapshot:
    """Normalized position on any platform."""
    market_id: str
    platform: str
    side: str  # "YES" or "NO"
    size: float
    entry_price: float
    current_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    extra: Dict = field(default_factory=dict)
