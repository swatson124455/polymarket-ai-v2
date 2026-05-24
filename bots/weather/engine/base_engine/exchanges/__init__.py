"""
Unified exchange adapter system for cross-platform prediction market trading.

Provides a consistent interface for interacting with multiple prediction market
venues: Polymarket, Kalshi, ForecastEx (Interactive Brokers), and Coinbase.
"""
from bots.weather.engine.base_engine.exchanges.base_adapter import ExchangeAdapter
from bots.weather.engine.base_engine.exchanges.models import (
    MarketSnapshot,
    OrderBook,
    OrderBookLevel,
    FeeSchedule,
    OrderResult,
    PositionSnapshot,
)

__all__ = [
    "ExchangeAdapter",
    "MarketSnapshot",
    "OrderBook",
    "OrderBookLevel",
    "FeeSchedule",
    "OrderResult",
    "PositionSnapshot",
]
