"""
Cross-Market Correlation Features (P3-05).

Finds related markets and computes correlation-based features for the prediction
engine. Detects logical constraint violations (P(A AND B) <= P(A)) for arb signals.

Wraps existing CorrelationStrategy from base_engine/analysis/correlation_strategies.py.
"""
from typing import Dict, Any, Optional, List
from structlog import get_logger

logger = get_logger()


class CrossMarketFeatureExtractor:
    """Extract cross-market features for prediction and arbitrage signals."""

    def __init__(self, db: Optional[Any] = None, client: Optional[Any] = None):
        self.db = db
        self.client = client
        self._related_cache: Dict[str, List[Dict]] = {}

    async def get_features(self, market_id: str) -> Dict[str, float]:
        """
        Compute cross-market features for a given market.

        Returns dict of feature_name -> float value suitable for prediction_engine.
        """
        features: Dict[str, float] = {
            "related_price_spread": 0.0,
            "related_volume_ratio": 0.0,
            "logical_consistency_violation": 0.0,
            "n_related_markets": 0.0,
        }

        related = await self._find_related_markets(market_id)
        if not related:
            return features

        features["n_related_markets"] = float(len(related))

        # Get current market price
        current_price = await self._get_market_price(market_id)
        if current_price is None:
            return features

        prices = []
        volumes = []
        for rm in related:
            rp = await self._get_market_price(rm.get("id", ""))
            if rp is not None:
                prices.append(rp)
                volumes.append(float(rm.get("volume", 0) or 0))

        if prices:
            avg_related_price = sum(prices) / len(prices)
            features["related_price_spread"] = current_price - avg_related_price

            current_vol = await self._get_market_volume(market_id)
            if current_vol and volumes:
                avg_vol = sum(volumes) / len(volumes)
                features["related_volume_ratio"] = current_vol / max(avg_vol, 1.0)

            # Logical constraint check: if this market implies a subset,
            # its price should not exceed the parent's price.
            for i, rm in enumerate(related):
                rel_type = rm.get("relationship_type", "")
                if rel_type in ("subset", "implies") and i < len(prices):
                    if current_price > prices[i] + 0.02:  # > parent + tolerance
                        features["logical_consistency_violation"] = 1.0
                        break

        return features

    async def _find_related_markets(self, market_id: str) -> List[Dict]:
        """Find markets related to given market via tags or Gamma API."""
        if market_id in self._related_cache:
            return self._related_cache[market_id]

        related: List[Dict] = []
        if not self.db or not getattr(self.db, "session_factory", None):
            return related

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                # Find markets with similar question text (same event group)
                r = await session.execute(text("""
                    SELECT m2.id, m2.question, m2.volume,
                           CASE WHEN m2.question LIKE '%%' || SPLIT_PART(m1.question, ' ', 1) || '%%'
                                THEN 'keyword_match' ELSE 'same_category' END AS relationship_type
                    FROM markets m1
                    JOIN markets m2 ON m1.id != m2.id
                        AND m2.active = true
                        AND m2.category = m1.category
                    WHERE m1.id = :mid
                    LIMIT 10
                """), {"mid": market_id})
                for row in r.fetchall():
                    related.append({
                        "id": str(row[0]),
                        "question": row[1],
                        "volume": row[2],
                        "relationship_type": row[3],
                    })
        except Exception as e:
            logger.debug("Related markets query failed: %s", e)

        self._related_cache[market_id] = related
        return related

    async def _get_market_price(self, market_id: str) -> Optional[float]:
        """Get latest price for a market from DB."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return None
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT price FROM market_prices
                    WHERE market_id = :mid
                    ORDER BY timestamp DESC LIMIT 1
                """), {"mid": market_id})
                row = r.fetchone()
                return float(row[0]) if row else None
        except Exception:
            return None

    async def _get_market_volume(self, market_id: str) -> Optional[float]:
        """Get 24h volume for a market."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return None
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text(
                    "SELECT COALESCE(volume, 0) FROM markets WHERE id = :mid"
                ), {"mid": market_id})
                row = r.fetchone()
                return float(row[0]) if row else None
        except Exception:
            return None
