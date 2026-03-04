"""
CLOB Adapter - Wraps py-clob-client for order placement and orderbook.
ExecutionEngine uses this when CLOB credentials are configured; otherwise falls back to PolymarketClient (httpx).
"""
import asyncio
from typing import Any, Dict, Optional
from structlog import get_logger
from config.settings import settings

logger = get_logger()

_CLOB_CLIENT = None


def _get_clob_client():
    """Build ClobClient once when creds and key are available (sync, used from executor)."""
    global _CLOB_CLIENT
    if _CLOB_CLIENT is not None:
        return _CLOB_CLIENT
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError:
        logger.debug("py-clob-client not installed; CLOB adapter disabled")
        return None
    host = (getattr(settings, "POLYMARKET_CLOB_API", None) or "").rstrip("/")
    key = (getattr(settings, "PRIVATE_KEY", None) or "").strip()
    if not key or not host:
        return None
    api_key = (getattr(settings, "CLOB_API_KEY", None) or "").strip()
    api_secret = (getattr(settings, "CLOB_SECRET", None) or "").strip()
    api_passphrase = (getattr(settings, "CLOB_PASSPHRASE", None) or "").strip()
    chain_id = getattr(settings, "POLYGON_CHAIN_ID", 137)
    if not api_key or not api_secret or not api_passphrase:
        logger.debug("CLOB_API_KEY/SECRET/PASSPHRASE not set; CLOB adapter disabled")
        return None
    try:
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        _CLOB_CLIENT = ClobClient(host=host, chain_id=chain_id, key=key, creds=creds)
        logger.info("CLOB adapter initialized with py-clob-client")
        return _CLOB_CLIENT
    except Exception as e:
        logger.warning("Failed to build ClobClient: %s", e)
        return None


def _place_order_sync(market_id: str, token_id: str, side: str, size: float, price: float) -> Dict[str, Any]:
    """Sync place order via py-clob-client (run in executor)."""
    client = _get_clob_client()
    if not client:
        return {"success": False, "error": "CLOB client not configured"}
    try:
        from py_clob_client.clob_types import OrderArgs
        side_upper = (side or "").upper()
        if side_upper not in ("BUY", "SELL"):
            if side_upper in ("YES", "NO"):
                side_upper = "BUY" if side_upper == "YES" else "SELL"
            else:
                return {"success": False, "error": f"Invalid side: {side}"}
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size),
            side=side_upper,
        )
        result = client.create_and_post_order(order_args)
        if result is None:
            return {"success": False, "error": "create_and_post_order returned None"}
        order_id = result.get("orderID") or result.get("id") or result.get("order_id")
        return {
            "success": True,
            "order_id": order_id,
            "market_id": market_id,
            "side": side_upper,
            "size": size,
            "price": price,
        }
    except Exception as e:
        logger.warning("py-clob-client place_order failed: %s", e)
        return {"success": False, "error": str(e)}


def _get_order_book_sync(token_id: str) -> Dict[str, Any]:
    """Sync get order book via py-clob-client (run in executor). Returns dict with bids/asks for compatibility."""
    client = _get_clob_client()
    if not client:
        return {}
    try:
        book = client.get_order_book(token_id)
        if book is None:
            return {}
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []

        def _level(level) -> Dict[str, Any]:
            if hasattr(level, "price") and hasattr(level, "size"):
                return {"price": getattr(level, "price"), "size": getattr(level, "size")}
            if isinstance(level, dict):
                return level
            return {"price": str(level), "size": ""}

        return {
            "bids": [_level(b) for b in bids],
            "asks": [_level(a) for a in asks],
        }
    except Exception as e:
        logger.debug("py-clob-client get_order_book failed: %s", e)
        return {}


class ClobAdapter:
    """
    Async CLOB adapter: uses AsyncClobClient (httpx, direct) when available,
    else falls back to py-clob-client in run_in_executor.
    """

    def __init__(self):
        self._async_client: Optional[Any] = None

    def _get_async_client(self) -> Optional[Any]:
        if self._async_client is not None:
            return self._async_client
        try:
            from base_engine.execution.async_clob_client import AsyncClobClient
            self._async_client = AsyncClobClient()
            if self._async_client.available:
                return self._async_client
        except Exception as e:
            logger.debug("AsyncClobClient not used: %s", e)
        self._async_client = None
        return None

    @property
    def available(self) -> bool:
        return _get_clob_client() is not None

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> Dict[str, Any]:
        """Place order via AsyncClobClient (async HTTP) or sync client in thread."""
        ac = self._get_async_client()
        if ac is not None:
            return await ac.place_order(market_id=market_id, token_id=token_id, side=side, size=size, price=price)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: _place_order_sync(market_id, token_id, side, size, price),
        )

    async def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """Get order book via AsyncClobClient or sync client in thread."""
        ac = self._get_async_client()
        if ac is not None:
            return await ac.get_order_book(token_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _get_order_book_sync(token_id))
