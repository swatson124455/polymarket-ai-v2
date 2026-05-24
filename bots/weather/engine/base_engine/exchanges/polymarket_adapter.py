"""
Polymarket exchange adapter — wraps existing PolymarketClient + CLOB.

Fee schedule: 0% maker, 0-3% taker (dynamic), 0% settlement.
"""
from __future__ import annotations
from typing import List, Optional
from structlog import get_logger

from bots.weather.engine.base_engine.exchanges.base_adapter import ExchangeAdapter
from bots.weather.engine.base_engine.exchanges.models import (
    FeeSchedule, MarketSnapshot, OrderBook, OrderBookLevel, OrderResult, PositionSnapshot,
)

logger = get_logger()


class PolymarketAdapter(ExchangeAdapter):
    """Adapter for Polymarket CLOB API (on-chain settlement via Polygon)."""

    def __init__(self, polymarket_client=None, clob_client=None, db=None):
        self._poly = polymarket_client
        self._clob = clob_client
        self._db = db

    # ── Market data ─────────────────────────────────────────────────────

    async def get_markets(self, limit: int = 200) -> List[MarketSnapshot]:
        if not self._poly:
            return []
        try:
            raw = await self._poly.get_markets(limit=limit)
            out: List[MarketSnapshot] = []
            for m in (raw or []):
                if not isinstance(m, dict):
                    continue
                tokens = m.get("tokens", [])
                yes_price = None
                no_price = None
                if isinstance(tokens, list) and len(tokens) >= 2:
                    try:
                        yes_price = float(tokens[0].get("outcomePrice") or 0)
                        no_price = float(tokens[1].get("outcomePrice") or 0)
                    except (ValueError, TypeError):
                        pass
                snap = MarketSnapshot(
                    market_id=str(m.get("id", "")),
                    platform="polymarket",
                    question=m.get("question", ""),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume=float(m.get("volume", 0) or 0),
                    liquidity=float(m.get("liquidity", 0) or 0),
                    category=(m.get("category") or "").lower(),
                    resolved=bool(m.get("resolved")),
                    extra={"tokens": tokens},
                )
                if snap.is_valid:
                    out.append(snap)
            return out
        except Exception as e:
            logger.warning("PolymarketAdapter.get_markets failed: %s", e)
            return []

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Fetch orderbook from CLOB if available."""
        if not self._clob:
            return None
        try:
            raw = await self._clob.get_order_book(market_id)
            bids = [OrderBookLevel(float(b["price"]), float(b["size"])) for b in (raw.get("bids") or [])]
            asks = [OrderBookLevel(float(a["price"]), float(a["size"])) for a in (raw.get("asks") or [])]
            return OrderBook(market_id=market_id, platform="polymarket", bids=bids, asks=asks)
        except Exception as e:
            logger.debug("PolymarketAdapter.get_orderbook failed: %s", e)
            return None

    # ── Trading ─────────────────────────────────────────────────────────

    async def place_order(self, market_id: str, side: str, size: float, price: float) -> OrderResult:
        if not self._clob:
            return OrderResult(success=False, platform="polymarket", error="CLOB client not configured")
        try:
            result = await self._clob.place_order(
                market_id=market_id, side=side, size=size, price=price
            )
            return OrderResult(
                success=result.get("success", False),
                order_id=str(result.get("order_id", "")),
                platform="polymarket",
                market_id=market_id,
                side=side, size=size, price=price,
                error=result.get("error", ""),
            )
        except Exception as e:
            return OrderResult(success=False, platform="polymarket", error=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        if not self._clob:
            return False
        try:
            return await self._clob.cancel_order(order_id)
        except Exception:
            return False

    # ── Account ─────────────────────────────────────────────────────────

    async def get_positions(self) -> List[PositionSnapshot]:
        # Positions are in the database, not directly from API
        if not self._db:
            return []
        try:
            from sqlalchemy import select
            from bots.weather.engine.base_engine.data.database import Position
            async with self._db.get_session() as session:
                r = await session.execute(
                    select(Position).where(Position.status == "open")
                )
                rows = r.scalars().all()
                return [
                    PositionSnapshot(
                        market_id=str(p.market_id), platform="polymarket",
                        side=p.side or "YES", size=float(p.size or 0),
                        entry_price=float(p.entry_price or 0),
                    )
                    for p in rows
                ]
        except Exception as e:
            logger.debug("PolymarketAdapter.get_positions failed: %s", e)
            return []

    async def get_balance(self) -> float:
        # Would query on-chain USDC balance or CLOB balance endpoint
        return 0.0

    # ── Platform info ───────────────────────────────────────────────────

    def fee_schedule(self) -> FeeSchedule:
        return FeeSchedule(taker_fee=0.015, maker_fee=0.0, settlement_fee=0.0, platform="polymarket")

    def platform_name(self) -> str:
        return "polymarket"

    def is_enabled(self) -> bool:
        return self._poly is not None
