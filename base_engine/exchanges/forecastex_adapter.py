"""
ForecastEx (Interactive Brokers) exchange adapter.

Uses ib_insync to connect to TWS/IB Gateway for ForecastEx event contracts.
Fee schedule: 0% (ForecastEx charges no fees on event contracts).

Requires IB TWS or Gateway running locally/remotely.
"""
from __future__ import annotations
from typing import List, Optional
from structlog import get_logger

from base_engine.exchanges.base_adapter import ExchangeAdapter
from base_engine.exchanges.models import (
    FeeSchedule, MarketSnapshot, OrderBook, OrderBookLevel, OrderResult, PositionSnapshot,
)

logger = get_logger()


class ForecastExAdapter(ExchangeAdapter):
    """
    Adapter for Interactive Brokers ForecastEx event contracts.

    Requires ib_insync and a running TWS/IB Gateway instance.
    Set FORECASTEX_IB_HOST and FORECASTEX_IB_PORT in settings.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 10):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib = None
        self._connected = False

    async def init(self) -> None:
        try:
            from ib_insync import IB
            self._ib = IB()
            await self._ib.connectAsync(self._host, self._port, clientId=self._client_id)
            self._connected = True
            logger.info("ForecastExAdapter connected to IB Gateway at %s:%d", self._host, self._port)
        except ImportError:
            logger.info("ib_insync not installed — ForecastEx adapter disabled")
            self._connected = False
        except Exception as e:
            logger.debug("ForecastExAdapter connection failed: %s", e)
            self._connected = False

    # ── Market data ─────────────────────────────────────────────────────

    async def get_markets(self, limit: int = 200) -> List[MarketSnapshot]:
        if not self._connected or not self._ib:
            return []
        try:
            from ib_insync import Contract
            # ForecastEx contracts are "IOPT" secType with "FORECASTEX" exchange
            contract = Contract(secType="IOPT", exchange="FORECASTEX")
            contracts = await self._ib.reqContractDetailsAsync(contract)
            out: List[MarketSnapshot] = []
            for cd in (contracts or [])[:limit]:
                c = cd.contract
                snap = MarketSnapshot(
                    market_id=str(c.conId),
                    platform="forecastex",
                    question=cd.longName or c.localSymbol or "",
                    yes_price=None,  # Need to request market data
                    category="",
                    extra={"symbol": c.localSymbol, "conId": c.conId},
                )
                out.append(snap)
            return out
        except Exception as e:
            logger.warning("ForecastExAdapter.get_markets failed: %s", e)
            return []

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Fetch order book via IB market depth for a ForecastEx contract."""
        if not self._connected or not self._ib:
            return None
        try:
            from ib_insync import Contract
            contract = Contract(conId=int(market_id), exchange="FORECASTEX")
            # qualifyContracts ensures IB recognises the contract
            contracts = await self._ib.qualifyContractsAsync(contract)
            if not contracts:
                return None
            ticker = self._ib.reqMktDepth(contracts[0], numRows=10)
            # Give IB a moment to populate depth data
            import asyncio
            await asyncio.sleep(0.5)
            bids = [
                OrderBookLevel(float(d.price), float(d.size))
                for d in (ticker.domBids or [])
                if d.price > 0
            ]
            asks = [
                OrderBookLevel(float(d.price), float(d.size))
                for d in (ticker.domAsks or [])
                if d.price > 0
            ]
            self._ib.cancelMktDepth(contracts[0])
            return OrderBook(market_id=market_id, platform="forecastex", bids=bids, asks=asks)
        except ImportError:
            return None
        except Exception as e:
            logger.debug("ForecastExAdapter.get_orderbook failed: %s", e)
            return None

    # ── Trading ─────────────────────────────────────────────────────────

    async def place_order(self, market_id: str, side: str, size: float, price: float) -> OrderResult:
        if not self._connected or not self._ib:
            return OrderResult(success=False, platform="forecastex", error="IB not connected")
        try:
            from ib_insync import Contract, LimitOrder
            contract = Contract(conId=int(market_id), exchange="FORECASTEX")
            action = "BUY" if side.upper() in ("BUY", "YES") else "SELL"
            order = LimitOrder(action=action, totalQuantity=size, lmtPrice=price)
            trade = self._ib.placeOrder(contract, order)
            return OrderResult(
                success=True,
                order_id=str(trade.order.orderId),
                platform="forecastex",
                market_id=market_id,
                side=side, size=size, price=price,
            )
        except Exception as e:
            return OrderResult(success=False, platform="forecastex", error=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        if not self._connected or not self._ib:
            return False
        try:
            for trade in self._ib.openTrades():
                if str(trade.order.orderId) == order_id:
                    self._ib.cancelOrder(trade.order)
                    return True
        except Exception:
            pass
        return False

    # ── Account ─────────────────────────────────────────────────────────

    async def get_positions(self) -> List[PositionSnapshot]:
        if not self._connected or not self._ib:
            return []
        try:
            positions = self._ib.positions()
            return [
                PositionSnapshot(
                    market_id=str(p.contract.conId),
                    platform="forecastex",
                    side="YES" if p.position > 0 else "NO",
                    size=abs(p.position),
                    entry_price=float(p.avgCost),
                )
                for p in positions
                if p.contract.exchange == "FORECASTEX"
            ]
        except Exception:
            return []

    async def get_balance(self) -> float:
        if not self._connected or not self._ib:
            return 0.0
        try:
            account_values = self._ib.accountValues()
            for av in account_values:
                if av.tag == "AvailableFunds" and av.currency == "USD":
                    return float(av.value)
        except Exception:
            pass
        return 0.0

    # ── Platform info ───────────────────────────────────────────────────

    def fee_schedule(self) -> FeeSchedule:
        return FeeSchedule(taker_fee=0.0, maker_fee=0.0, settlement_fee=0.0, platform="forecastex")

    def platform_name(self) -> str:
        return "forecastex"

    def is_enabled(self) -> bool:
        return self._connected

    async def close(self) -> None:
        if self._ib and self._connected:
            self._ib.disconnect()
            self._connected = False
