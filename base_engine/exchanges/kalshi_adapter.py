"""
Kalshi exchange adapter — wraps existing KalshiClient.

Fee schedule: variable taker (typically 7% of profit or ~1c/contract), 0% maker, 0% settlement.
"""
from __future__ import annotations
from typing import List, Optional
from structlog import get_logger

from base_engine.exchanges.base_adapter import ExchangeAdapter
from base_engine.exchanges.models import (
    FeeSchedule, MarketSnapshot, OrderBook, OrderBookLevel, OrderResult, PositionSnapshot,
)

logger = get_logger()


class KalshiAdapter(ExchangeAdapter):
    """Adapter for Kalshi REST/FIX API (custodial, regulated US exchange)."""

    def __init__(self, kalshi_client=None):
        self._client = kalshi_client

    async def init(self) -> None:
        if self._client is None:
            try:
                from base_engine.data.kalshi_client import KalshiClient
                self._client = KalshiClient()
                await self._client.init()
            except Exception as e:
                logger.debug("KalshiAdapter init failed (expected without credentials): %s", e)

    # ── Market data ─────────────────────────────────────────────────────

    async def get_markets(self, limit: int = 200) -> List[MarketSnapshot]:
        if not self._client:
            return []
        try:
            raw = await self._client.get_markets()
            out: List[MarketSnapshot] = []
            for m in (raw or []):
                if not isinstance(m, dict):
                    continue
                # Kalshi uses cents for prices (0-100 scale) in some endpoints, dollar in others
                yes_bid = m.get("yes_bid") or m.get("last_price")
                try:
                    yes_price = float(yes_bid) if yes_bid is not None else None
                    # Normalize to 0-1 scale if Kalshi returns cents
                    if yes_price is not None and yes_price > 1.0:
                        yes_price = yes_price / 100.0
                except (ValueError, TypeError):
                    yes_price = None
                no_price = (1.0 - yes_price) if yes_price is not None else None
                snap = MarketSnapshot(
                    market_id=str(m.get("ticker") or m.get("id", "")),
                    platform="kalshi",
                    question=m.get("title") or m.get("question", ""),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume=float(m.get("volume", 0) or 0),
                    category=(m.get("category") or "").lower(),
                    resolved=bool(m.get("result")),
                )
                if snap.is_valid:
                    out.append(snap)
            return out
        except Exception as e:
            logger.warning("KalshiAdapter.get_markets failed: %s", e)
            return []

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        if not self._client:
            return None
        try:
            raw = await self._client.get_orderbook(market_id)
            bids = [OrderBookLevel(float(b[0]) / 100, float(b[1])) for b in (raw.get("yes") or raw.get("bids") or [])]
            asks = [OrderBookLevel(float(a[0]) / 100, float(a[1])) for a in (raw.get("no") or raw.get("asks") or [])]
            return OrderBook(market_id=market_id, platform="kalshi", bids=bids, asks=asks)
        except Exception as e:
            logger.debug("KalshiAdapter.get_orderbook failed: %s", e)
            return None

    # ── Trading ─────────────────────────────────────────────────────────

    async def place_order(self, market_id: str, side: str, size: float, price: float) -> OrderResult:
        if not self._client:
            return OrderResult(success=False, platform="kalshi", error="Kalshi client not configured")
        try:
            result = await self._client.place_order(
                market_id=market_id, side=side, size=size, price=price
            )
            return OrderResult(
                success=result.get("success", False),
                order_id=str(result.get("order_id", "")),
                platform="kalshi",
                market_id=market_id,
                side=side, size=size, price=price,
                error=result.get("error", ""),
            )
        except Exception as e:
            return OrderResult(success=False, platform="kalshi", error=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        if not self._client:
            return False
        try:
            result = await self._client.cancel_order(order_id)
            return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)
        except Exception as e:
            logger.debug("KalshiAdapter.cancel_order failed: %s", e)
            return False

    # ── Account ─────────────────────────────────────────────────────────

    async def get_positions(self) -> List[PositionSnapshot]:
        if not self._client:
            return []
        try:
            raw = await self._client.get_positions() if hasattr(self._client, "get_positions") else []
            out: List[PositionSnapshot] = []
            for p in (raw or []):
                if not isinstance(p, dict):
                    continue
                out.append(PositionSnapshot(
                    market_id=str(p.get("ticker") or p.get("market_id", "")),
                    platform="kalshi",
                    side="YES" if (p.get("position", 0) or 0) > 0 else "NO",
                    size=abs(float(p.get("position", 0) or 0)),
                    entry_price=float(p.get("average_price", 0) or 0) / 100.0,
                    unrealized_pnl=float(p.get("pnl", 0) or 0),
                ))
            return out
        except Exception as e:
            logger.debug("KalshiAdapter.get_positions failed: %s", e)
            return []

    async def get_balance(self) -> float:
        if not self._client:
            return 0.0
        try:
            balance = await self._client.get_balance() if hasattr(self._client, "get_balance") else None
            if isinstance(balance, dict):
                return float(balance.get("balance", 0) or 0) / 100.0  # Kalshi uses cents
            return float(balance) if balance is not None else 0.0
        except Exception as e:
            logger.debug("KalshiAdapter.get_balance failed: %s", e)
            return 0.0

    # ── Platform info ───────────────────────────────────────────────────

    def fee_schedule(self) -> FeeSchedule:
        # Kalshi charges ~7% of profit or ~$0.01/contract, whichever is greater
        return FeeSchedule(taker_fee=0.01, maker_fee=0.0, settlement_fee=0.0, platform="kalshi")

    def platform_name(self) -> str:
        return "kalshi"

    def is_enabled(self) -> bool:
        return self._client is not None

    async def close(self) -> None:
        pass
