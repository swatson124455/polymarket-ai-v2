import json
from datetime import date, datetime
from typing import Optional, Any
import redis.asyncio as aioredis
from structlog import get_logger
from config.settings import settings

logger = get_logger()


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that converts datetime/date objects to ISO strings."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


class RedisCache:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
    
    async def init(self):
        # Check if Redis is disabled
        if not getattr(settings, 'REDIS_ENABLED', True):
            logger.info("Redis cache disabled via REDIS_ENABLED=false")
            self.redis = None
            return
        
        try:
            redis_url = getattr(settings, "REDIS_URL", None) or None
            if redis_url and str(redis_url).strip():
                redis_url = redis_url.strip()
            else:
                # Build URL from host/port/db and optional password
                password = getattr(settings, "REDIS_PASSWORD", None) or None
                if password:
                    redis_url = f"redis://:{password}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
                else:
                    redis_url = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
            timeout = getattr(settings, "REDIS_TIMEOUT_SECONDS", 5)
            self.redis = aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=100,
                socket_connect_timeout=timeout,
            )
            await self.redis.ping()
            
            # Redis memory/eviction configured server-side (not by application code)
            logger.info("Redis cache initialized")
        except Exception as e:
            logger.warning(f"Redis connection failed: {str(e)}")
            logger.info("Continuing without Redis cache - some features may be slower")
            self.redis = None
    
    async def get(self, key: str) -> Optional[Any]:
        if not self.redis:
            return None
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.warning(f"Redis get error for key {key}", error=str(e))
            return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        Set a value in Redis cache with optional TTL.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds. If None, uses default TTL based on key prefix:
                 - markets:* -> 300s (5 min)
                 - prices:* -> 60s (1 min)
                 - trades:* -> 600s (10 min)
                 - default -> 3600s (1 hour)
        """
        if not self.redis:
            return
        try:
            # Auto-set TTL based on key prefix if not provided
            if ttl is None:
                if key.startswith("markets:"):
                    ttl = 300  # 5 minutes for market data
                elif key.startswith("prices:") or key.startswith("price:"):
                    ttl = 60  # 1 minute for price data (changes frequently)
                elif key.startswith("trades:") or key.startswith("trade:"):
                    ttl = 600  # 10 minutes for trade data
                elif key.startswith("users:") or key.startswith("user:"):
                    ttl = 1800  # 30 minutes for user data
                else:
                    ttl = 3600  # 1 hour default
            
            serialized = json.dumps(value, cls=_SafeEncoder)
            await self.redis.set(key, serialized, ex=ttl)
        except Exception as e:
            logger.warning(f"Redis set error for key {key}", error=str(e))
    
    async def delete(self, key: str):
        if not self.redis:
            return
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.warning(f"Redis delete error for key {key}", error=str(e))
    
    async def publish(self, channel: str, message: Any):
        if not self.redis:
            return
        try:
            serialized = json.dumps(message)
            await self.redis.publish(channel, serialized)
        except Exception as e:
            logger.warning(f"Redis publish error for channel {channel}", error=str(e))
    
    async def subscribe(self, channel: str):
        if not self.redis:
            return None
        try:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe(channel)
            return pubsub
        except Exception as e:
            logger.warning("Redis subscribe failed for channel %s: %s", channel, e)
            return None
    
    async def close(self):
        if self.redis:
            await self.redis.aclose()
