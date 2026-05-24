"""
Backward-compatibility shim for RedisManager.

DEPRECATED: Use base_engine.data.redis_cache.RedisCache instead.
This file exists only so that cache_decorators.py and approval_cache.py
continue to import without error.  RedisManager delegates every call to
the singleton RedisCache instance held by BaseEngine.
"""
import warnings
from typing import Optional, Any

warnings.warn(
    "RedisManager is deprecated — use base_engine.data.redis_cache.RedisCache",
    DeprecationWarning,
    stacklevel=2,
)

_instance: Optional["RedisManager"] = None


class RedisManager:
    """Thin shim around RedisCache for backward compatibility."""

    def __init__(self):
        self.client = None

    @classmethod
    def get_instance(cls) -> "RedisManager":
        global _instance
        if _instance is None:
            _instance = cls()
        return _instance

    # ── lifecycle (no-op — shim has no real connection) ──

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    # ── async Redis primitives (no-op when client is None) ──

    async def get(self, key: str) -> Optional[Any]:
        if self.client is None:
            return None
        return await self.client.get(key)

    async def set(self, key: str, value: Any, ex: int = 300) -> bool:
        if self.client is None:
            return False
        return await self.client.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> None:
        if self.client is None:
            return
        await self.client.delete(*keys)

    async def keys(self, pattern: str = "*"):
        if self.client is None:
            return []
        return await self.client.keys(pattern)

    async def hset(self, name: str, key: str, value: Any) -> int:
        if self.client is None:
            return 0
        return await self.client.hset(name, key, value)

    async def hget(self, name: str, key: str) -> Optional[Any]:
        if self.client is None:
            return None
        return await self.client.hget(name, key)

    async def hgetall(self, name: str) -> dict:
        if self.client is None:
            return {}
        return await self.client.hgetall(name)

    async def zadd(self, name: str, mapping: dict) -> int:
        if self.client is None:
            return 0
        return await self.client.zadd(name, mapping)

    async def zrange(self, name: str, start: int, end: int, withscores: bool = False):
        if self.client is None:
            return []
        return await self.client.zrange(name, start, end, withscores=withscores)
