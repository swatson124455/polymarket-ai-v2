"""
Unified Market Data Service

Provides a single interface for fetching market data from multiple sources:
- Polymarket API (primary)
- Blockchain (via TheGraph)
- Cache (Redis + in-memory)
- Database (persistent storage)

Handles rate limiting, caching, and aggregation automatically.
"""

import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta, timezone
from collections import OrderedDict
from structlog import get_logger
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.data.thegraph_client import TheGraphClient
from base_engine.data.redis_cache import RedisCache
from base_engine.data.database import Database
from config.settings import settings

logger = get_logger()


class UnifiedMarketService:
    """
    Unified service for fetching market data from all available sources.
    
    Priority order:
    1. L1 Cache (in-memory, fastest)
    2. L2 Cache (Redis, fast)
    3. L3 Cache (Database, persistent)
    4. API (Polymarket, primary source)
    5. Blockchain (TheGraph, fallback)
    
    Automatically handles:
    - Rate limiting
    - Caching at all levels
    - Data aggregation
    - Error recovery
    """
    
    def __init__(
        self,
        client: PolymarketClient,
        thegraph_client: Optional[TheGraphClient] = None,
        cache: Optional[RedisCache] = None,
        db: Optional[Database] = None
    ):
        self.client = client
        self.thegraph_client = thegraph_client
        self.cache = cache
        self.db = db
        
        # L1 Cache: In-memory (fastest, limited size)
        self._l1_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._l1_cache_max_size = 1000  # Keep top 1000 markets in memory
        self._l1_cache_ttl = 60  # 1 minute TTL for L1 cache
        
        # Cache timestamps
        self._cache_timestamps: Dict[str, datetime] = {}
        
        # Rate limiting
        self._last_api_call = datetime.now(timezone.utc)
        self._min_api_interval = 0.1  # 100ms between API calls (10 req/sec max)

        # L3 store failure visibility (set by _store_in_database on error)
        self.last_store_error: Optional[str] = None

    async def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
        category: Optional[str] = None,
        use_cache: bool = True,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get markets from unified data service.
        
        Args:
            active: If True, fetch active markets
            limit: Maximum number of markets to return
            offset: Number of markets to skip
            category: Optional category filter
            use_cache: If True, use cache layers
            force_refresh: If True, bypass all caches
            
        Returns:
            List of market dictionaries
        """
        cache_key = f"markets:{active}:{limit}:{offset}:{category or ''}"
        
        # L1 Cache (in-memory) - fastest
        if use_cache and not force_refresh:
            l1_result = self._get_from_l1_cache(cache_key)
            if l1_result is not None:
                logger.debug(f"Returning {len(l1_result)} markets from L1 cache")
                return l1_result
        
        # L2 Cache (Redis) - fast
        if use_cache and not force_refresh and self.cache:
            l2_result = await self._get_from_l2_cache(cache_key)
            if l2_result is not None:
                logger.debug(f"Returning {len(l2_result)} markets from L2 cache")
                # Promote to L1 cache
                self._set_l1_cache(cache_key, l2_result)
                return l2_result
        
        # L3 Cache (Database) - persistent
        if use_cache and not force_refresh and self.db:
            l3_result = await self._get_from_l3_cache(active, limit, offset, category)
            if l3_result is not None and len(l3_result) > 0:
                logger.debug(f"Returning {len(l3_result)} markets from L3 cache (database)")
                # Promote to L2 and L1 caches
                if self.cache:
                    await self._set_l2_cache(cache_key, l3_result, ttl=300)
                self._set_l1_cache(cache_key, l3_result)
                return l3_result
        
        # Primary source: Polymarket API
        try:
            await self._rate_limit()
            api_markets = await self.client.get_markets(
                active=active,
                limit=limit,
                offset=offset,
                category=category
            )
            
            if api_markets:
                # Store in all cache layers
                if self.cache:
                    await self._set_l2_cache(cache_key, api_markets, ttl=300)
                self._set_l1_cache(cache_key, api_markets)
                
                # Store in database (L3 cache)
                if self.db and self.db.session_factory:
                    await self._store_in_database(api_markets)
                
                logger.debug(f"Fetched {len(api_markets)} markets from API")
                return api_markets
        except Exception as e:
            logger.warning(f"API fetch failed: {str(e)}, trying fallback")
        
        # Fallback: TheGraph (blockchain) - Only if enabled
        if self.thegraph_client and getattr(settings, 'USE_THEGRAPH_QUERIES', False):
            try:
                graph_markets = await self._get_from_thegraph(active, limit, offset)
                if graph_markets:
                    logger.debug(f"Fetched {len(graph_markets)} markets from TheGraph")
                    # Store in caches
                    if self.cache:
                        await self._set_l2_cache(cache_key, graph_markets, ttl=300)
                    self._set_l1_cache(cache_key, graph_markets)
                    return graph_markets
            except Exception as e:
                logger.warning(f"TheGraph fetch failed: {str(e)}")
        
        # Return empty list if all sources fail
        logger.warning("All market data sources failed, returning empty list")
        return []
    
    async def get_market(self, market_id: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get a single market by ID.
        
        Args:
            market_id: Market identifier
            use_cache: If True, use cache layers
            
        Returns:
            Market dictionary or None if not found
        """
        cache_key = f"market:{market_id}"
        
        # L1 Cache
        if use_cache:
            l1_result = self._get_from_l1_cache(cache_key)
            if l1_result is not None:
                return l1_result
        
        # L2 Cache
        if use_cache and self.cache:
            l2_result = await self._get_from_l2_cache(cache_key)
            if l2_result is not None:
                self._set_l1_cache(cache_key, l2_result)
                return l2_result
        
        # L3 Cache (Database)
        if use_cache and self.db:
            l3_result = await self._get_market_from_db(market_id)
            if l3_result is not None:
                if self.cache:
                    await self._set_l2_cache(cache_key, l3_result, ttl=600)
                self._set_l1_cache(cache_key, l3_result)
                return l3_result
        
        # API
        try:
            await self._rate_limit()
            market = await self.client.get_market(market_id)
            if market:
                if self.cache:
                    await self._set_l2_cache(cache_key, market, ttl=600)
                self._set_l1_cache(cache_key, market)
                return market
        except Exception as e:
            logger.warning(f"API fetch for market {market_id} failed: {str(e)}")
        
        return None
    
    def _get_from_l1_cache(self, key: str) -> Optional[List[Dict[str, Any]]]:
        """Get from L1 (in-memory) cache."""
        if key not in self._l1_cache:
            return None
        
        # Check TTL
        if key in self._cache_timestamps:
            age = (datetime.now(timezone.utc) - self._cache_timestamps[key]).total_seconds()
            if age > self._l1_cache_ttl:
                # Expired, remove from cache
                if key in self._l1_cache:
                    del self._l1_cache[key]
                if key in self._cache_timestamps:
                    del self._cache_timestamps[key]
                return None
        
        # Move to end (LRU)
        result = self._l1_cache.pop(key)
        self._l1_cache[key] = result
        return result if isinstance(result, list) else [result]
    
    def _set_l1_cache(self, key: str, value: Any):
        """Set L1 (in-memory) cache."""
        # Remove oldest if at capacity
        if len(self._l1_cache) >= self._l1_cache_max_size:
            self._l1_cache.popitem(last=False)  # Remove oldest
        
        self._l1_cache[key] = value
        self._cache_timestamps[key] = datetime.now(timezone.utc)
    
    async def _get_from_l2_cache(self, key: str) -> Optional[List[Dict[str, Any]]]:
        """Get from L2 (Redis) cache."""
        if not self.cache:
            return None
        try:
            result = await self.cache.get(key)
            if result:
                return result if isinstance(result, list) else [result]
        except Exception as e:
            logger.debug(f"L2 cache get error: {str(e)}")
        return None
    
    async def _set_l2_cache(self, key: str, value: Any, ttl: int = 300):
        """Set L2 (Redis) cache."""
        if not self.cache:
            return
        try:
            await self.cache.set(key, value, ttl=ttl)
        except Exception as e:
            logger.debug(f"L2 cache set error: {str(e)}")
    
    async def _get_from_l3_cache(
        self,
        active: bool,
        limit: int,
        offset: int,
        category: Optional[str]
    ) -> Optional[List[Dict[str, Any]]]:
        """Get from L3 (Database) cache."""
        if not self.db or not self.db.session_factory:
            return None
        
        try:
            from sqlalchemy import select
            from base_engine.data.database import Market
            
            async with self.db.get_session() as session:
                query = select(Market).where(Market.active == active)
                if category:
                    query = query.where(Market.category == category)
                query = query.offset(offset).limit(limit)
                
                result = await session.execute(query)
                markets = result.scalars().all()
                
                if markets:
                    return [self._market_to_dict(m) for m in markets]
        except Exception as e:
            logger.debug(f"L3 cache get error: {str(e)}")
        
        return None
    
    async def _get_market_from_db(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Get single market from database."""
        if not self.db or not self.db.session_factory:
            return None
        
        try:
            from sqlalchemy import select
            from base_engine.data.database import Market
            
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(Market).where(Market.id == market_id)
                )
                market = result.scalar_one_or_none()
                if market:
                    return self._market_to_dict(market)
        except Exception as e:
            logger.debug(f"Database get market error: {str(e)}")
        
        return None
    
    async def _store_in_database(self, markets: List[Dict[str, Any]]):
        """Store markets in database (L3 cache)."""
        if not self.db or not self.db.session_factory:
            return
        
        try:
            await self.db.bulk_insert_markets(markets)
            self.last_store_error = None
        except Exception as e:
            logger.warning("L3 cache (database) store failed: %s", e, market_count=len(markets) if markets else 0)
            self.last_store_error = str(e)
    
    async def _get_from_thegraph(
        self,
        active: bool,
        limit: int,
        offset: int
    ) -> List[Dict[str, Any]]:
        """Get markets from TheGraph (blockchain fallback)."""
        if not self.thegraph_client:
            return []
        
        try:
            markets = await self.thegraph_client.get_markets(
                first=limit,
                skip=offset
            )
            # Filter by active if needed (TheGraph may not support this filter)
            if active:
                markets = [m for m in markets if m.get("active", True)]
            return markets
        except Exception as e:
            logger.warning(f"TheGraph fetch error: {str(e)}")
            return []
    
    def _market_to_dict(self, market) -> Dict[str, Any]:
        """Convert database Market model to dictionary. Includes tokens/yes_token_id/no_token_id for bot compatibility."""
        yes_price = float(market.yes_price) if market.yes_price is not None else 0.5
        no_price = float(market.no_price) if market.no_price is not None else 0.5
        tokens = []
        if market.yes_token_id:
            tokens.append({"tokenId": market.yes_token_id, "outcomePrice": yes_price})
        if market.no_token_id:
            tokens.append({"tokenId": market.no_token_id, "outcomePrice": no_price})
        if not tokens:
            tokens = [{"tokenId": market.yes_token_id or market.no_token_id or "", "outcomePrice": yes_price or no_price or 0.5}]

        return {
            "id": market.id,
            "condition_id": market.condition_id,
            "question": market.question,
            "slug": market.slug,
            "category": market.category,
            "resolution_source": market.resolution_source,
            "end_date_iso": market.end_date_iso.isoformat() if market.end_date_iso else None,
            "image": market.image,
            "active": market.active,
            "liquidity": market.liquidity,
            "volume": market.volume,
            "resolved": market.resolved,
            "resolution": market.resolution,
            "yes_token_id": market.yes_token_id,
            "no_token_id": market.no_token_id,
            "tokens": tokens,
        }
    
    async def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        now = datetime.now(timezone.utc)
        time_since_last = (now - self._last_api_call).total_seconds()
        
        if time_since_last < self._min_api_interval:
            sleep_time = self._min_api_interval - time_since_last
            await asyncio.sleep(sleep_time)
        
        self._last_api_call = datetime.now(timezone.utc)
    
    def clear_cache(self, level: Optional[str] = None):
        """
        Clear cache at specified level.
        
        Args:
            level: 'l1', 'l2', 'l3', or None for all
        """
        if level is None or level == "l1":
            self._l1_cache.clear()
            self._cache_timestamps.clear()
            logger.info("L1 cache cleared")
        
        if level is None or level == "l2":
            # L2 cache (Redis) is managed by RedisCache, can't clear here
            logger.info("L2 cache clear requested (managed by RedisCache)")
        
        if level is None or level == "l3":
            # L3 cache (Database) is persistent, typically not cleared
            logger.info("L3 cache clear requested (database is persistent)")
