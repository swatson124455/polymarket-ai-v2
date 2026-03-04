"""
Dune Analytics Integration — Tier 3 #35

On-chain whale analysis via Dune Analytics API (free tier).
Queries pre-built Polymarket dashboards for:
  - Large position holders
  - Volume spikes by wallet
  - Cross-market wallet activity

Requires: DUNE_API_KEY env var (free tier: 2,500 credits/month).
"""
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from config.settings import settings

logger = get_logger()

DUNE_API_BASE = "https://api.dune.com/api/v1"

# Pre-built query IDs for Polymarket analysis (create on dune.com)
# Users should replace these with their own saved query IDs
DEFAULT_QUERIES = {
    "whale_positions": None,      # Large holders by market
    "volume_by_wallet": None,     # 24h volume leaders
    "recent_large_trades": None,  # Trades > $10K
}


class DuneAnalyticsClient:
    """
    Query Dune Analytics for on-chain Polymarket intelligence.

    Free tier: 2,500 credits/month (~50 medium queries/day).
    Set DUNE_API_KEY in .env to enable.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or getattr(settings, "DUNE_API_KEY", "")
        self._available = bool(self._api_key)
        self._http = None
        self._result_cache: Dict[str, Dict] = {}
        self._cache_ttl = 300  # 5 min cache

    @property
    def is_available(self) -> bool:
        return self._available

    async def _ensure_http(self):
        if self._http is None:
            try:
                import httpx
                self._http = httpx.AsyncClient(
                    timeout=60.0,
                    headers={
                        "X-DUNE-API-KEY": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
            except ImportError:
                logger.info("httpx not installed — Dune Analytics disabled")
                self._available = False

    async def execute_query(self, query_id: int, parameters: Optional[Dict] = None) -> Optional[Dict]:
        """
        Execute a Dune query and wait for results.

        Args:
            query_id: Dune saved query ID
            parameters: Optional query parameters

        Returns:
            Query result dict or None on failure
        """
        if not self._available:
            return None

        await self._ensure_http()
        if not self._http:
            return None

        # Check cache
        cache_key = f"{query_id}_{hash(str(parameters))}"
        cached = self._result_cache.get(cache_key)
        if cached:
            age = (datetime.now(timezone.utc) - cached["timestamp"]).total_seconds()
            if age < self._cache_ttl:
                return cached["data"]

        try:
            # Step 1: Execute query
            body = {}
            if parameters:
                body["query_parameters"] = parameters

            resp = await self._http.post(
                f"{DUNE_API_BASE}/query/{query_id}/execute",
                json=body if body else None,
            )
            if resp.status_code != 200:
                logger.warning("Dune execute failed: %d %s", resp.status_code, resp.text[:200])
                return None

            execution_id = resp.json().get("execution_id")
            if not execution_id:
                return None

            # Step 2: Poll for results (max 60s)
            for _ in range(12):
                await asyncio.sleep(5)
                status_resp = await self._http.get(
                    f"{DUNE_API_BASE}/execution/{execution_id}/status"
                )
                if status_resp.status_code != 200:
                    continue
                state = status_resp.json().get("state")
                if state == "QUERY_STATE_COMPLETED":
                    break
                if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                    logger.warning("Dune query %d failed: %s", query_id, state)
                    return None

            # Step 3: Get results
            result_resp = await self._http.get(
                f"{DUNE_API_BASE}/execution/{execution_id}/results"
            )
            if result_resp.status_code != 200:
                return None

            data = result_resp.json()
            self._result_cache[cache_key] = {
                "data": data,
                "timestamp": datetime.now(timezone.utc),
            }
            return data

        except Exception as e:
            logger.warning("Dune query %d error: %s", query_id, e)
            return None

    async def get_whale_positions(self, market_condition_id: str) -> List[Dict[str, Any]]:
        """
        Get large position holders for a specific market.

        Returns list of {"address": str, "size": float, "side": str}.
        Requires a saved Dune query for Polymarket positions.
        """
        query_id = DEFAULT_QUERIES.get("whale_positions")
        if not query_id:
            return []

        result = await self.execute_query(
            query_id,
            parameters={"condition_id": market_condition_id},
        )
        if not result:
            return []

        rows = result.get("result", {}).get("rows", [])
        return [
            {
                "address": row.get("address", ""),
                "size": float(row.get("size", 0)),
                "side": row.get("side", "unknown"),
            }
            for row in rows
        ]

    async def get_volume_leaders(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get top volume wallets in the last N hours.

        Returns list of {"address": str, "volume_usd": float, "trade_count": int}.
        """
        query_id = DEFAULT_QUERIES.get("volume_by_wallet")
        if not query_id:
            return []

        result = await self.execute_query(
            query_id,
            parameters={"hours": hours},
        )
        if not result:
            return []

        rows = result.get("result", {}).get("rows", [])
        return [
            {
                "address": row.get("address", ""),
                "volume_usd": float(row.get("volume_usd", 0)),
                "trade_count": int(row.get("trade_count", 0)),
            }
            for row in rows
        ]

    async def get_recent_large_trades(self, min_size_usd: float = 10000) -> List[Dict[str, Any]]:
        """
        Get recent large trades across all markets.

        Returns list of {"address", "market_id", "size_usd", "side", "timestamp"}.
        """
        query_id = DEFAULT_QUERIES.get("recent_large_trades")
        if not query_id:
            return []

        result = await self.execute_query(
            query_id,
            parameters={"min_size": min_size_usd},
        )
        if not result:
            return []

        rows = result.get("result", {}).get("rows", [])
        return [
            {
                "address": row.get("address", ""),
                "market_id": row.get("market_id", ""),
                "size_usd": float(row.get("size_usd", 0)),
                "side": row.get("side", "unknown"),
                "timestamp": row.get("timestamp", ""),
            }
            for row in rows
        ]

    async def close(self):
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
