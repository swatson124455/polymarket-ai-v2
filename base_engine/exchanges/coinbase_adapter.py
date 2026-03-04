"""
Coinbase prediction market adapter.

Coinbase launched nationwide event contracts on Jan 28, 2026.
REST API for market data and order placement.
Fee schedule: ~2% taker, 0% maker (estimated from public fee page).
Uses HMAC-SHA256 signing per Coinbase Advanced Trade API specification.
"""
from __future__ import annotations
import hashlib
import hmac
import time
from typing import List, Optional
import httpx
from structlog import get_logger

from base_engine.exchanges.base_adapter import ExchangeAdapter
from base_engine.exchanges.models import (
    FeeSchedule, MarketSnapshot, OrderBook, OrderBookLevel, OrderResult, PositionSnapshot,
)

logger = get_logger()

COINBASE_PRED_API = "https://api.coinbase.com/api/v3/brokerage"


class _CoinbaseAuth(httpx.Auth):
    """
    HMAC-SHA256 request signing for Coinbase Advanced Trade API.

    Signs each request with CB-ACCESS-KEY, CB-ACCESS-SIGN, CB-ACCESS-TIMESTAMP headers.
    Signature = HMAC-SHA256(secret, timestamp + method + path + body).
    """

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret

    def auth_flow(self, request: httpx.Request):
        timestamp = str(int(time.time()))
        method = request.method.upper()
        # Extract path from full URL (remove base)
        path = request.url.raw_path.decode("utf-8") if request.url.raw_path else str(request.url.path)
        body = request.content.decode("utf-8") if request.content else ""

        message = timestamp + method + path + body
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        request.headers["CB-ACCESS-KEY"] = self._api_key
        request.headers["CB-ACCESS-SIGN"] = signature
        request.headers["CB-ACCESS-TIMESTAMP"] = timestamp
        yield request


class CoinbaseAdapter(ExchangeAdapter):
    """
    Adapter for Coinbase prediction market / event contracts.

    Requires COINBASE_PRED_API_KEY and COINBASE_PRED_API_SECRET env vars.
    All authenticated requests are signed with HMAC-SHA256.
    """

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self._api_key = api_key
        self._api_secret = api_secret
        self._client: Optional[httpx.AsyncClient] = None

    async def init(self) -> None:
        auth = None
        if self._api_key and self._api_secret:
            auth = _CoinbaseAuth(self._api_key, self._api_secret)
        self._client = httpx.AsyncClient(
            base_url=COINBASE_PRED_API,
            auth=auth,
            timeout=15.0,
        )

    # ── Market data ─────────────────────────────────────────────────────

    async def get_markets(self, limit: int = 200) -> List[MarketSnapshot]:
        if not self._client:
            return []
        try:
            r = await self._client.get(
                "/events",
                params={"limit": limit, "product_type": "EVENT_CONTRACT"},
            )
            if r.status_code != 200:
                logger.debug("Coinbase markets fetch returned %d", r.status_code)
                return []
            data = r.json()
            events = data.get("events") or data.get("products") or []
            out: List[MarketSnapshot] = []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                yes_price = None
                try:
                    yes_price = float(ev.get("yes_price") or ev.get("price") or 0)
                    if yes_price <= 0 or yes_price > 1:
                        # Coinbase may use cents
                        if yes_price > 1:
                            yes_price = yes_price / 100.0
                except (ValueError, TypeError):
                    pass
                no_price = (1.0 - yes_price) if yes_price else None
                snap = MarketSnapshot(
                    market_id=str(ev.get("product_id") or ev.get("id", "")),
                    platform="coinbase",
                    question=ev.get("title") or ev.get("description", ""),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume=float(ev.get("volume_24h", 0) or 0),
                    category=(ev.get("category") or "").lower(),
                )
                if snap.is_valid:
                    out.append(snap)
            return out
        except Exception as e:
            logger.warning("CoinbaseAdapter.get_markets failed: %s", e)
            return []

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Fetch order book via Coinbase Advanced Trade product book endpoint."""
        if not self._client:
            return None
        try:
            r = await self._client.get(
                f"/product_book",
                params={"product_id": market_id, "limit": 20},
            )
            if r.status_code != 200:
                return None
            data = r.json().get("pricebook", {})
            bids = [
                OrderBookLevel(float(b.get("price", 0)), float(b.get("size", 0)))
                for b in (data.get("bids") or [])
                if b.get("price")
            ]
            asks = [
                OrderBookLevel(float(a.get("price", 0)), float(a.get("size", 0)))
                for a in (data.get("asks") or [])
                if a.get("price")
            ]
            return OrderBook(market_id=market_id, platform="coinbase", bids=bids, asks=asks)
        except Exception as e:
            logger.debug("CoinbaseAdapter.get_orderbook failed: %s", e)
            return None

    # ── Trading ─────────────────────────────────────────────────────────

    async def place_order(self, market_id: str, side: str, size: float, price: float) -> OrderResult:
        if not self._client or not self._api_key:
            return OrderResult(success=False, platform="coinbase", error="Coinbase not configured")
        try:
            payload = {
                "product_id": market_id,
                "side": side.upper(),
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": str(size),
                        "limit_price": str(price),
                    }
                },
            }
            r = await self._client.post("/orders", json=payload)
            data = r.json()
            success = r.status_code in (200, 201) and data.get("success", False)
            return OrderResult(
                success=success,
                order_id=str(data.get("order_id", "")),
                platform="coinbase",
                market_id=market_id,
                side=side, size=size, price=price,
                error=data.get("error_response", {}).get("message", "") if not success else "",
            )
        except Exception as e:
            return OrderResult(success=False, platform="coinbase", error=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        if not self._client:
            return False
        try:
            r = await self._client.post("/orders/batch_cancel", json={"order_ids": [order_id]})
            return r.status_code == 200
        except Exception:
            return False

    # ── Account ─────────────────────────────────────────────────────────

    async def get_positions(self) -> List[PositionSnapshot]:
        """Fetch open event contract positions from Coinbase accounts."""
        if not self._client or not self._api_key:
            return []
        try:
            r = await self._client.get("/accounts", params={"limit": 250})
            if r.status_code != 200:
                return []
            out: List[PositionSnapshot] = []
            for acct in (r.json().get("accounts") or []):
                if not isinstance(acct, dict):
                    continue
                # Event contract accounts have a product_id and non-zero balance
                currency = acct.get("currency", "")
                if currency == "USD":
                    continue  # Skip cash accounts
                available = float(acct.get("available_balance", {}).get("value", 0) or 0)
                hold = float(acct.get("hold", {}).get("value", 0) or 0)
                total = available + hold
                if total <= 0:
                    continue
                out.append(PositionSnapshot(
                    market_id=acct.get("name", currency),
                    platform="coinbase",
                    side="YES",  # Coinbase positions are directional by product
                    size=total,
                    entry_price=0.0,  # Coinbase doesn't expose avg entry in accounts
                    extra={"currency": currency, "account_id": acct.get("uuid", "")},
                ))
            return out
        except Exception as e:
            logger.debug("CoinbaseAdapter.get_positions failed: %s", e)
            return []

    async def get_balance(self) -> float:
        if not self._client:
            return 0.0
        try:
            r = await self._client.get("/accounts")
            for acct in (r.json().get("accounts") or []):
                if acct.get("currency") == "USD":
                    return float(acct.get("available_balance", {}).get("value", 0))
        except Exception:
            pass
        return 0.0

    # ── Platform info ───────────────────────────────────────────────────

    def fee_schedule(self) -> FeeSchedule:
        return FeeSchedule(taker_fee=0.02, maker_fee=0.0, settlement_fee=0.0, platform="coinbase")

    def platform_name(self) -> str:
        return "coinbase"

    def is_enabled(self) -> bool:
        return bool(self._api_key)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
