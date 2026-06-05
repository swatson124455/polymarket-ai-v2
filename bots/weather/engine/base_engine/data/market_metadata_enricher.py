"""
Market Metadata Enrichment
==========================
Adds computed fields to markets (days to resolution, etc.)
"""
from typing import Dict, Optional
from datetime import datetime, timezone
from structlog import get_logger
from bots.weather.engine.base_engine.data.database import Database, Market

logger = get_logger()


class MarketMetadataEnricher:
    """
    Enriches market metadata with computed fields.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
    
    async def enrich_market(self, market: Market) -> Dict:
        """
        Enrich market with computed metadata.
        
        Returns:
            Dict with enriched fields:
                - days_to_resolution
                - hours_to_resolution
                - is_short_term
                - is_long_term
                - liquidity_tier
                - volume_tier
                - price_momentum
        """
        enriched = {
            "market_id": market.id,
            "base": {
                "question": market.question,
                "category": market.category,
                "liquidity": float(market.liquidity or 0),
                "volume": float(market.volume or 0),
                "active": market.active
            }
        }
        
        # Days to resolution
        if market.end_date_iso:
            now = datetime.now(timezone.utc)
            end_dt = market.end_date_iso if getattr(market.end_date_iso, "tzinfo", None) else market.end_date_iso.replace(tzinfo=timezone.utc)
            delta = end_dt - now
            days = delta.total_seconds() / 86400
            hours = delta.total_seconds() / 3600
            
            enriched["days_to_resolution"] = max(0, days)
            enriched["hours_to_resolution"] = max(0, hours)
            enriched["is_short_term"] = days < 7
            enriched["is_long_term"] = days > 30
        else:
            enriched["days_to_resolution"] = None
            enriched["hours_to_resolution"] = None
            enriched["is_short_term"] = None
            enriched["is_long_term"] = None
        
        # Liquidity tier
        liquidity = float(market.liquidity or 0)
        if liquidity > 100000:
            enriched["liquidity_tier"] = "deep"
        elif liquidity > 10000:
            enriched["liquidity_tier"] = "moderate"
        elif liquidity > 1000:
            enriched["liquidity_tier"] = "thin"
        else:
            enriched["liquidity_tier"] = "very_thin"
        
        # Volume tier
        volume = float(market.volume or 0)
        if volume > 50000:
            enriched["volume_tier"] = "high"
        elif volume > 5000:
            enriched["volume_tier"] = "medium"
        else:
            enriched["volume_tier"] = "low"
        
        # Price momentum (would need price history)
        enriched["price_momentum"] = None  # Would calculate from price history
        
        return enriched
    
    async def enrich_markets(self, market_ids: Optional[list] = None, limit: int = 100) -> Dict[str, Dict]:
        """
        Enrich multiple markets.
        
        Returns:
            Dict mapping market_id to enriched data
        """
        if not self.db or not self.db.session_factory:
            return {}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select
            
            if market_ids:
                result = await session.execute(
                    select(Market).where(Market.id.in_(market_ids))
                )
            else:
                result = await session.execute(
                    select(Market)
                    .where(Market.active == True)
                    .order_by(Market.liquidity.desc())
                    .limit(limit)
                )
            
            markets = result.scalars().all()
            
            enriched = {}
            for market in markets:
                try:
                    enriched[market.id] = await self.enrich_market(market)
                except Exception as e:
                    logger.warning(f"Failed to enrich market {market.id}: {str(e)}")
                    continue
            
            return enriched
