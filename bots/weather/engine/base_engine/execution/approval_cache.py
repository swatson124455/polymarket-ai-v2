"""
Approval Cache - Avoids redundant on-chain approval checks.
Caches infinite token approvals for spenders to reduce gas and latency.
"""
from typing import Dict, Optional
from structlog import get_logger
from bots.weather.engine.base_engine.cache.redis_manager import RedisManager

logger = get_logger()


class ApprovalCache:
    """Cache for token approval status to avoid redundant on-chain checks."""
    
    def __init__(self):
        self.redis = RedisManager.get_instance()
        self.memory_cache: Dict[str, bool] = {}
        self.ttl = 3600  # 1 hour - long enough to avoid frequent checks
    
    def _get_cache_key(self, token_address: str, spender: str) -> str:
        """Generate cache key for token-spender pair."""
        return f"approval:{token_address}:{spender}"
    
    async def is_approved(self, token_address: str, spender: str) -> Optional[bool]:
        """
        Check if approval is cached. Returns:
        - True if cached as approved
        - False if cached as not approved
        - None if not in cache (must check on-chain)
        """
        cache_key = self._get_cache_key(token_address, spender)
        
        # Check memory first (fastest)
        if cache_key in self.memory_cache:
            logger.debug(f"Approval cache HIT (memory): {token_address[:10]}...{spender[:10]}")
            return self.memory_cache[cache_key]
        
        # Check Redis (fast)
        cached = await self.redis.get(cache_key)
        if cached:
            is_approved = cached == "1"
            self.memory_cache[cache_key] = is_approved
            logger.debug(f"Approval cache HIT (redis): {token_address[:10]}...{spender[:10]}")
            return is_approved
        
        logger.debug(f"Approval cache MISS: {token_address[:10]}...{spender[:10]}")
        return None
    
    async def set_approved(self, token_address: str, spender: str, approved: bool):
        """Cache approval status. Only caches infinite approvals for safety."""
        cache_key = self._get_cache_key(token_address, spender)
        
        self.memory_cache[cache_key] = approved
        
        if approved:
            # Only cache infinite approvals (revoke invalidates cache)
            await self.redis.set(cache_key, "1", ex=self.ttl)
            logger.info(f"Cached approval: {token_address[:10]}...{spender[:10]}")
        else:
            # Remove from cache (not approved or revoked)
            await self.redis.delete(cache_key)
            logger.info(f"Removed approval from cache: {token_address[:10]}...{spender[:10]}")
    
    async def invalidate(self, token_address: str):
        """
        Invalidate all approvals for a token.
        Call this after revoking approval or detecting approval change.
        """
        pattern = f"approval:{token_address}:*"
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)
        
        # Clear memory cache
        self.memory_cache = {k: v for k, v in self.memory_cache.items()
                            if not k.startswith(f"approval:{token_address}:")}
        
        logger.info(f"Invalidated approval cache for {token_address[:10]}... ({len(keys)} entries)")
    
    async def clear_all(self):
        """Clear all approval caches. Use for testing or reset."""
        keys = await self.redis.keys("approval:*")
        if keys:
            await self.redis.delete(*keys)
        self.memory_cache.clear()
        logger.info(f"Cleared all approval caches ({len(keys)} entries)")
