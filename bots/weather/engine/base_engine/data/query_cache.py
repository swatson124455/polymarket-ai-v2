"""
Query caching layer (#37) - Redis-backed cache for hot queries.

Use cache_query(ttl=...) decorator or cache.get/set with query keys.
"""
import hashlib
import json
from functools import wraps
from typing import Any, Callable, Optional, TypeVar
from structlog import get_logger

logger = get_logger()

F = TypeVar("F", bound=Callable[..., Any])


def cache_query(
    cache: Optional[Any],
    key_prefix: str = "query",
    ttl: int = 3600,
) -> Callable[[F], F]:
    """
    Decorate an async function to cache its return value in Redis.

    Args:
        cache: RedisCache instance (with .get / .set). If None, no caching.
        key_prefix: Prefix for cache key (e.g. "market_stats").
        ttl: Time to live in seconds.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if cache is None or getattr(cache, "redis", None) is None:
                return await func(*args, **kwargs)
            raw = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
            h = hashlib.md5(raw.encode()).hexdigest()
            cache_key = f"{key_prefix}:{func.__name__}:{h}"
            try:
                cached = await cache.get(cache_key)
                if cached is not None:
                    return cached
            except Exception as e:
                logger.debug("query_cache get failed: %s", e)
            result = await func(*args, **kwargs)
            try:
                await cache.set(cache_key, result, ttl=ttl)
            except Exception as e:
                logger.debug("query_cache set failed: %s", e)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
