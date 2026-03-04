"""
Counterparty Classifier (P5-02).

Classify trading counterparties by address into behavioral types:
NOISE, INFORMED, MARKET_MAKER, ARBITRAGEUR.
Uses trade frequency, win rate, timing patterns, and position duration.

Requires: Sufficient trade data per address (30+ resolved trades).
"""
from typing import Dict, Any, Optional, List
from enum import Enum
from structlog import get_logger

logger = get_logger()

MIN_TRADES_FOR_CLASSIFICATION = 30


class CounterpartyType(Enum):
    NOISE = "noise"
    INFORMED = "informed"
    MARKET_MAKER = "market_maker"
    ARBITRAGEUR = "arbitrageur"
    UNKNOWN = "unknown"


class CounterpartyClassifier:
    """Classify counterparty addresses by trading behavior."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._cache: Dict[str, CounterpartyType] = {}

    async def classify(self, address: str) -> Dict[str, Any]:
        """
        Classify a counterparty address.

        Returns:
            type: CounterpartyType
            confidence: 0.0-1.0
            metrics: dict of underlying metrics
        """
        if address in self._cache:
            return {"type": self._cache[address].value, "confidence": 0.8, "metrics": {}}

        if not self.db or not getattr(self.db, "session_factory", None):
            return {"type": CounterpartyType.UNKNOWN.value, "confidence": 0.0, "metrics": {}}

        try:
            metrics = await self._compute_metrics(address)
            if metrics["trade_count"] < MIN_TRADES_FOR_CLASSIFICATION:
                return {"type": CounterpartyType.UNKNOWN.value, "confidence": 0.0, "metrics": metrics}

            ctype = self._classify_from_metrics(metrics)
            self._cache[address] = ctype
            return {"type": ctype.value, "confidence": 0.7, "metrics": metrics}

        except Exception as e:
            logger.debug("Counterparty classification failed for %s: %s", address[:10], e)
            return {"type": CounterpartyType.UNKNOWN.value, "confidence": 0.0, "metrics": {}}

    async def _compute_metrics(self, address: str) -> Dict[str, Any]:
        """Compute behavioral metrics for an address."""
        from sqlalchemy import text
        async with self.db.get_session() as session:
            r = await session.execute(text("""
                SELECT
                    COUNT(*) AS trade_count,
                    COUNT(DISTINCT market_id) AS unique_markets,
                    AVG(EXTRACT(EPOCH FROM (timestamp - LAG(timestamp) OVER (ORDER BY timestamp)))) AS avg_interval_seconds
                FROM trades
                WHERE user_address = :addr
            """), {"addr": address})
            row = r.fetchone()

        trade_count = int(row[0]) if row else 0
        unique_markets = int(row[1]) if row else 0
        avg_interval = float(row[2]) if row and row[2] else 3600.0

        # Both-sides ratio: does address trade both YES and NO on same market?
        both_sides_ratio = 0.0
        if trade_count > 0 and self.db.session_factory:
            try:
                async with self.db.get_session() as session:
                    r2 = await session.execute(text("""
                        SELECT COUNT(DISTINCT market_id) AS both_count
                        FROM (
                            SELECT market_id
                            FROM trades
                            WHERE user_address = :addr
                            GROUP BY market_id
                            HAVING COUNT(DISTINCT side) > 1
                        ) sub
                    """), {"addr": address})
                    both_count = int(r2.scalar() or 0)
                    both_sides_ratio = both_count / max(unique_markets, 1)
            except Exception:
                pass

        return {
            "trade_count": trade_count,
            "unique_markets": unique_markets,
            "avg_interval_seconds": avg_interval,
            "both_sides_ratio": both_sides_ratio,
            "trades_per_market": trade_count / max(unique_markets, 1),
        }

    def _classify_from_metrics(self, m: Dict[str, Any]) -> CounterpartyType:
        """Rule-based classification from metrics."""
        # Market makers: trade both sides frequently, many markets
        if m["both_sides_ratio"] > 0.5 and m["trades_per_market"] > 3:
            return CounterpartyType.MARKET_MAKER

        # Arbitrageurs: very fast trading, many markets, short intervals
        if m["avg_interval_seconds"] < 60 and m["unique_markets"] > 20:
            return CounterpartyType.ARBITRAGEUR

        # Informed: few markets, high concentration
        if m["trades_per_market"] > 5 and m["unique_markets"] < 10:
            return CounterpartyType.INFORMED

        # Default: noise
        return CounterpartyType.NOISE

    async def get_market_composition(self, market_id: str, limit: int = 50) -> Dict[str, float]:
        """
        Get counterparty type distribution for recent traders in a market.
        Returns: {type: fraction} e.g. {'noise': 0.6, 'informed': 0.3, 'market_maker': 0.1}
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return {}

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT DISTINCT user_address
                    FROM trades
                    WHERE market_id = :mid
                    ORDER BY timestamp DESC
                    LIMIT :lim
                """), {"mid": market_id, "lim": limit})
                addresses = [row[0] for row in r.fetchall()]

            if not addresses:
                return {}

            counts: Dict[str, int] = {}
            for addr in addresses:
                result = await self.classify(addr)
                ctype = result["type"]
                counts[ctype] = counts.get(ctype, 0) + 1

            total = sum(counts.values())
            return {k: v / total for k, v in counts.items()} if total > 0 else {}

        except Exception as e:
            logger.debug("Market composition query failed: %s", e)
            return {}
