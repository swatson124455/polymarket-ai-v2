"""
Async CLOB client - places orders via httpx (async).
Uses py_clob_client for order creation and signing; only the HTTP POST is async (Phase 2).
"""
import asyncio
from typing import Dict, Any, Optional

import httpx
from structlog import get_logger
from config.settings import settings

logger = get_logger()

# Lazy ref to sync client (for create_order + signing)
_sync_client_ref: Optional[Any] = None


def _get_sync_client():
    """Reuse clob_adapter's client builder."""
    global _sync_client_ref
    if _sync_client_ref is not None:
        return _sync_client_ref
    from base_engine.execution.clob_adapter import _get_clob_client
    _sync_client_ref = _get_clob_client()
    return _sync_client_ref


def _build_post_order_request(token_id: str, side: str, size: float, price: float) -> Optional[Dict[str, Any]]:
    """
    Build (url, headers, body) for POST /order. Runs in executor (sync).
    Returns dict with keys: url, headers, body; or None on failure.
    """
    try:
        from py_clob_client.client import ClobClient, POST_ORDER, RequestArgs, order_to_json, create_level_2_headers
        from py_clob_client.clob_types import OrderArgs, OrderType
    except ImportError:
        return None
    client = _get_sync_client()
    if not client or not isinstance(client, ClobClient):
        return None
    side_upper = (side or "").upper()
    if side_upper not in ("BUY", "SELL"):
        if side_upper in ("YES", "NO"):
            side_upper = "BUY" if side_upper == "YES" else "SELL"
        else:
            return None
    try:
        order_args = OrderArgs(token_id=token_id, price=float(price), size=float(size), side=side_upper)
        order = client.create_order(order_args)
        if order is None:
            return None
        body = order_to_json(order, client.creds.api_key, OrderType.GTC, False)
        request_args = RequestArgs(
            method="POST",
            request_path=POST_ORDER,
            body=body,
            serialized_body=__import__("json").dumps(body, separators=(",", ":"), ensure_ascii=False),
        )
        headers = create_level_2_headers(client.signer, client.creds, request_args)
        host = (getattr(settings, "POLYMARKET_CLOB_API", None) or "").rstrip("/")
        url = f"{host}{POST_ORDER}"
        return {"url": url, "headers": headers, "body": request_args.serialized_body}
    except Exception as e:
        logger.warning("Build post order request failed: %s", e)
        return None


class AsyncClobClient:
    """
    Async CLOB order placement using httpx (direct connection).
    Order creation/signing still uses py_clob_client in a thread; HTTP POST is async.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _client_sync(self) -> Optional[httpx.AsyncClient]:
        if self._client is None:
            timeout = httpx.Timeout(15.0, connect=5.0)
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client

    @property
    def available(self) -> bool:
        return _get_sync_client() is not None

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> Dict[str, Any]:
        """Place order: build request in executor, POST with httpx async."""
        loop = asyncio.get_running_loop()
        req = await loop.run_in_executor(
            None,
            lambda: _build_post_order_request(token_id, side, size, price),
        )
        if req is None:
            return {"success": False, "error": "CLOB client or request build failed"}
        client = self._client_sync()
        if client is None:
            return {"success": False, "error": "httpx client not available"}
        try:
            resp = await client.post(
                req["url"],
                headers=req["headers"],
                content=req["body"],
            )
            resp.raise_for_status()
            data = resp.json()
            order_id = data.get("orderID") or data.get("id") or data.get("order_id")
            return {
                "success": True,
                "order_id": order_id,
                "market_id": market_id,
                "side": (side or "").upper(),
                "size": size,
                "price": price,
            }
        except httpx.HTTPStatusError as e:
            logger.warning("CLOB POST order failed: %s %s", e.response.status_code, e.response.text)
            # S150: Mark retryable HTTP errors so execution_engine can retry.
            # 425 = matching engine maintenance, 429 = rate limit, 502/503 = upstream.
            result: Dict[str, Any] = {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
            if e.response.status_code in (425, 429, 502, 503):
                result["retryable"] = True
            return result
        except Exception as e:
            logger.warning("CLOB place_order failed: %s", e)
            return {"success": False, "error": str(e)}

    async def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """Get order book (delegate to sync client in executor for now)."""
        from base_engine.execution.clob_adapter import _get_order_book_sync
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _get_order_book_sync(token_id))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
