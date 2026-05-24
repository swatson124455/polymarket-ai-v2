"""
Arbitrage detector (#24) - find cross-platform price differences.

Compares Polymarket (DB/API) with Kalshi and Manifold (optional APIs).
Opportunities: buy on one platform, sell on another when prices differ.
"""
from typing import Any, Dict, List, Optional
from structlog import get_logger
import httpx

logger = get_logger()

KALSHI_API = "https://trading-api.kalshi.com"
MANIFOLD_API = "https://api.manifold.markets"


class ArbitrageDetector:
    """
    Detect price differences across Polymarket, Kalshi, Manifold.

    Polymarket data from DB or passed in; Kalshi/Manifold fetched via public APIs.
    """

    def __init__(
        self,
        db: Optional[Any] = None,
        polymarket_yes_price_getter: Optional[Any] = None,
    ):
        self.db = db
        self._poly_getter = polymarket_yes_price_getter
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    def _normalize_question(self, q: str) -> str:
        if not q:
            return ""
        return q.lower().strip()[:200]

    def _is_same_event(self, q1: str, q2: str) -> bool:
        n1, n2 = self._normalize_question(q1), self._normalize_question(q2)
        if n1 == n2:
            return True
        if n1 in n2 or n2 in n1:
            return True
        return False

    def _get_strategy(self, poly_yes: float, other_yes: float) -> str:
        if poly_yes < other_yes:
            return f"Buy Polymarket YES @ {poly_yes:.2f}, Sell other YES @ {other_yes:.2f}"
        return f"Buy other YES @ {other_yes:.2f}, Sell Polymarket YES @ {poly_yes:.2f}"

    async def _get_kalshi_markets(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetch Kalshi markets (public API)."""
        try:
            client = await self._get_client()
            r = await client.get(f"{KALSHI_API}/trade-api/v2/markets", params={"limit": limit})
            if r.status_code != 200:
                return []
            data = r.json()
            markets = data.get("markets") or []
            out = []
            for m in markets:
                ticker = m.get("ticker") or ""
                yes_bid = None
                if "yes_bid" in m:
                    yes_bid = float(m["yes_bid"]) if m["yes_bid"] is not None else None
                if yes_bid is None and "last_price" in m:
                    yes_bid = float(m["last_price"]) if m["last_price"] is not None else None
                out.append({
                    "id": ticker,
                    "question": m.get("title") or m.get("question") or ticker,
                    "yes_bid": yes_bid,
                    "yes_ask": float(m["yes_ask"]) if m.get("yes_ask") is not None else None,
                })
            return out
        except Exception as e:
            logger.debug("Kalshi fetch failed: %s", e)
            return []

    async def _get_manifold_markets(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetch Manifold markets (public API)."""
        try:
            client = await self._get_client()
            r = await client.get(f"{MANIFOLD_API}/v0/markets", params={"limit": limit})
            if r.status_code != 200:
                return []
            data = r.json()
            markets = data if isinstance(data, list) else []
            out = []
            for m in markets[:limit]:
                prob = m.get("probability")
                if prob is None:
                    continue
                out.append({
                    "id": m.get("id") or "",
                    "question": m.get("question") or "",
                    "yes_bid": float(prob),
                    "yes_ask": float(prob),
                })
            return out
        except Exception as e:
            logger.debug("Manifold fetch failed: %s", e)
            return []

    async def get_polymarket_markets_for_arb(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get Polymarket markets with yes_price from DB or API."""
        out = []
        if self._poly_getter:
            try:
                rows = await self._poly_getter(limit)
                for r in rows:
                    q = r.get("question") or ""
                    yes = r.get("yes_price")
                    if yes is not None:
                        out.append({"id": r.get("id") or "", "question": q, "price_yes": float(yes)})
            except Exception as e:
                logger.debug("Polymarket getter failed: %s", e)
        if self.db and not out:
            try:
                rows = await self.db.get_active_markets_for_activity(limit=limit)
                for r in rows:
                    q = r.get("question") or ""
                    yes = r.get("yes_price")
                    if yes is not None:
                        out.append({"id": r.get("id") or "", "question": q, "price_yes": float(yes)})
            except Exception as e:
                logger.debug("DB get_active_markets_for_activity failed: %s", e)
        return out

    async def find_arbitrage(
        self,
        min_profit_pct: float = 2.0,
        include_kalshi: bool = True,
        include_manifold: bool = True,
        poly_limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Find arbitrage opportunities: same event, different YES price across platforms.

        Returns list of { event, polymarket_price, other_platform, other_price, profit_pct, strategy }.
        """
        opportunities: List[Dict[str, Any]] = []
        poly_markets = await self.get_polymarket_markets_for_arb(limit=poly_limit)
        if not poly_markets:
            return opportunities

        if include_kalshi:
            kalshi = await self._get_kalshi_markets(limit=200)
            for p in poly_markets:
                for k in kalshi:
                    if not self._is_same_event(p["question"], k["question"]):
                        continue
                    other_price = k.get("yes_bid")
                    if other_price is None:
                        continue
                    poly_yes = p["price_yes"]
                    profit_pct = abs(poly_yes - other_price) * 100
                    if profit_pct >= min_profit_pct:
                        opportunities.append({
                            "event": p["question"][:120],
                            "polymarket_price": poly_yes,
                            "other_platform": "kalshi",
                            "other_price": other_price,
                            "profit_pct": round(profit_pct, 2),
                            "strategy": self._get_strategy(poly_yes, other_price),
                        })

        if include_manifold:
            manifold = await self._get_manifold_markets(limit=200)
            for p in poly_markets:
                for m in manifold:
                    if not self._is_same_event(p["question"], m["question"]):
                        continue
                    other_price = m.get("yes_bid")
                    if other_price is None:
                        continue
                    poly_yes = p["price_yes"]
                    profit_pct = abs(poly_yes - other_price) * 100
                    if profit_pct >= min_profit_pct:
                        opportunities.append({
                            "event": p["question"][:120],
                            "polymarket_price": poly_yes,
                            "other_platform": "manifold",
                            "other_price": other_price,
                            "profit_pct": round(profit_pct, 2),
                            "strategy": self._get_strategy(poly_yes, other_price),
                        })

        opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
        return opportunities

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
