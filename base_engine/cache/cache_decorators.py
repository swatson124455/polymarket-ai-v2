"""
Cache decorators for easy caching of function results.
"""
import functools
import hashlib
import json
from typing import Callable, Any
from structlog import get_logger
from base_engine.cache.redis_manager import RedisManager

logger = get_logger()


def cached(ttl: int = 300, key_prefix: str = "cache"):
    """
    Decorator to cache async function results in Redis.
    
    Args:
        ttl: Time to live in seconds
        key_prefix: Prefix for cache key
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            redis = RedisManager.get_instance()
            
            # Generate cache key from function name and args
            key_parts = [key_prefix, func.__name__]
            
            # Hash args and kwargs for key
            args_str = json.dumps([str(a) for a in args], sort_keys=True)
            kwargs_str = json.dumps({k: str(v) for k, v in kwargs.items()}, sort_keys=True)
            args_hash = hashlib.md5((args_str + kwargs_str).encode()).hexdigest()[:12]
            key_parts.append(args_hash)
            
            cache_key = ":".join(key_parts)
            
            # Try cache
            cached_result = await redis.get(cache_key)
            if cached_result:
                try:
                    return json.loads(cached_result)
                except Exception as _e:
                    # M2 FIX: Replace bare except:pass — it swallowed all exceptions silently.
                    # Cache corruption or malformed data now surfaces as debug log (cache miss fallback).
                    logger.debug("Cache JSON decode failed for %s (cache miss): %s", cache_key, _e)
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Cache result
            try:
                await redis.set(cache_key, json.dumps(result), ex=ttl)
            except Exception as e:
                logger.debug(f"Failed to cache result: {e}")
            
            return result
        
        return wrapper
    return decorator


def invalidate_cache(key_pattern: str):
    """
    Decorator to invalidate cache keys matching pattern after function execution.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            
            # Invalidate cache
            redis = RedisManager.get_instance()
            keys = await redis.keys(key_pattern)
            if keys:
                await redis.delete(*keys)
                logger.debug(f"Invalidated {len(keys)} cache keys: {key_pattern}")
            
            return result
        
        return wrapper
    return decorator
