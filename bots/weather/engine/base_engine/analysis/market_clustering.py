"""
Market Clustering (#34) - group similar markets.

Group by category; optional text similarity for cross-category clusters.
"""
from typing import Any, Dict, List, Optional
from structlog import get_logger

logger = get_logger()


class MarketClustering:
    """
    Group markets by category or similarity for pattern finding.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    async def clusters_by_category(
        self,
        limit_per_category: int = 50,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group markets by category; return category -> list of market dicts."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return {}
        try:
            from sqlalchemy import select, func
            from bots.weather.engine.base_engine.data.database import Market
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(Market)
                    .where(Market.category.isnot(None), Market.category != "")
                    .order_by(Market.volume.desc().nullslast())
                )
                rows = result.scalars().all()
            by_cat: Dict[str, List[Dict[str, Any]]] = {}
            for m in rows:
                cat = m.category or "uncategorized"
                by_cat.setdefault(cat, [])
                if len(by_cat[cat]) < limit_per_category:
                    by_cat[cat].append({
                        "market_id": m.id,
                        "question": m.question,
                        "category": m.category,
                        "volume": float(m.volume) if m.volume else 0,
                        "liquidity": float(m.liquidity) if m.liquidity else 0,
                    })
            return by_cat
        except Exception as e:
            logger.debug("market_clustering clusters_by_category failed: %s", e)
            return {}

    async def get_cluster_summary(self) -> Dict[str, Any]:
        """Return cluster counts and top categories."""
        clusters = await self.clusters_by_category(limit_per_category=100)
        counts = {k: len(v) for k, v in clusters.items()}
        top = sorted(counts.items(), key=lambda x: -x[1])[:10]
        return {
            "total_categories": len(clusters),
            "category_counts": counts,
            "top_categories": top,
        }
