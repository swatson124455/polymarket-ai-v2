"""
Kalshi REST API Client (P5-05).

Connects to Kalshi's trading API for cross-platform arbitrage.
Requires: KALSHI_API_KEY and KALSHI_EMAIL env vars.
Hardwired dependency shell — gracefully returns empty when credentials missing.
"""
import os
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    """Kalshi REST API client for cross-platform arbitrage."""

    def __init__(self):
        self._api_key = os.getenv("KALSHI_API_KEY", "")
        self._email = os.getenv("KALSHI_EMAIL", "")
        self._token: Optional[str] = None
        self._initialized = False

    @property
    def is_available(self) -> bool:
        return bool(self._api_key and self._email)

    async def init(self) -> None:
        """Authenticate with Kalshi API. No-op if credentials missing."""
        if not self.is_available:
            logger.info("Kalshi client: no credentials, running in stub mode")
            return
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{KALSHI_BASE_URL}/login",
                    json={"email": self._email, "password": self._api_key},
                    timeout=15,
                )
                if r.status_code == 200:
                    self._token = r.json().get("token")
                    self._initialized = True
                    logger.info("Kalshi client authenticated")
                else:
                    logger.warning("Kalshi auth failed: %d", r.status_code)
        except Exception as e:
            logger.debug("Kalshi init failed: %s", e)

    async def get_markets(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetch active markets from Kalshi."""
        if not self._initialized:
            return []
        try:
            import httpx
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{KALSHI_BASE_URL}/markets",
                    headers=headers,
                    params={"limit": limit, "status": "open"},
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
                return data.get("markets", [])
        except Exception as e:
            logger.debug("Kalshi get_markets failed: %s", e)
            return []

    async def get_orderbook(self, market_id: str) -> Dict[str, Any]:
        """Fetch orderbook for a Kalshi market."""
        if not self._initialized:
            return {}
        try:
            import httpx
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{KALSHI_BASE_URL}/markets/{market_id}/orderbook",
                    headers=headers,
                    timeout=15,
                )
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.debug("Kalshi get_orderbook failed: %s", e)
            return {}

    async def place_order(
        self, market_id: str, side: str, size: int, price: float
    ) -> Dict[str, Any]:
        """Place order on Kalshi. Returns order result."""
        if not self._initialized:
            return {"success": False, "error": "Not authenticated"}
        try:
            import httpx
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{KALSHI_BASE_URL}/portfolio/orders",
                    headers=headers,
                    json={
                        "ticker": market_id,
                        "action": "buy" if side.upper() in ("YES", "BUY") else "sell",
                        "count": size,
                        "type": "limit",
                        "yes_price": int(price * 100),  # Kalshi uses cents
                    },
                    timeout=15,
                )
                r.raise_for_status()
                return {"success": True, "order": r.json()}
        except Exception as e:
            logger.debug("Kalshi place_order failed: %s", e)
            return {"success": False, "error": str(e)}
