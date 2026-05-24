"""
S168: Persistent blacklist for tokens that will never have price data.

Stores failed token IDs in Redis with no expiry. Load on startup before first scan.
After N consecutive failures (default 5), blacklist the token. Remove only if the
market reappears in ingestion with fresh price data.
"""
from typing import Optional, Set
from structlog import get_logger

logger = get_logger()

REDIS_KEY_PREFIX = "unpriced_blacklist:"
REDIS_FAILURES_PREFIX = "unpriced_failures:"
DEFAULT_FAILURE_THRESHOLD = 5


class UnpricedTokenBlacklist:
    def __init__(self, redis_client, threshold: int = DEFAULT_FAILURE_THRESHOLD):
        self._redis = redis_client
        self._threshold = threshold
        self._cache: Set[str] = set()
        self._loaded = False

    async def load(self) -> int:
        if not self._redis:
            self._loaded = True
            return 0
        try:
            cursor = 0
            count = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{REDIS_KEY_PREFIX}*", count=100
                )
                for key in keys:
                    token_id = key.decode() if isinstance(key, bytes) else key
                    token_id = token_id.replace(REDIS_KEY_PREFIX, "", 1)
                    self._cache.add(token_id)
                    count += 1
                if cursor == 0:
                    break
            self._loaded = True
            if count > 0:
                logger.info("unpriced_blacklist_loaded", count=count)
            return count
        except Exception as e:
            logger.warning("unpriced_blacklist_load_failed", error=str(e))
            self._loaded = True
            return 0

    def is_blacklisted(self, token_id: str) -> bool:
        return token_id in self._cache

    async def record_failure(self, token_id: str) -> bool:
        if not self._redis or token_id in self._cache:
            return False
        try:
            fail_key = f"{REDIS_FAILURES_PREFIX}{token_id}"
            count = await self._redis.incr(fail_key)
            if count >= self._threshold:
                await self._redis.set(f"{REDIS_KEY_PREFIX}{token_id}", "1")
                self._cache.add(token_id)
                await self._redis.delete(fail_key)
                logger.warning(
                    "token_permanently_blacklisted",
                    token_id=token_id,
                    after_failures=count,
                )
                return True
            return False
        except Exception as e:
            logger.debug("unpriced_blacklist_record_failed", token_id=token_id, error=str(e))
            return False

    async def clear(self, token_id: str) -> None:
        self._cache.discard(token_id)
        if not self._redis:
            return
        try:
            await self._redis.delete(
                f"{REDIS_KEY_PREFIX}{token_id}",
                f"{REDIS_FAILURES_PREFIX}{token_id}",
            )
            logger.info("unpriced_blacklist_cleared", token_id=token_id)
        except Exception as e:
            logger.debug("unpriced_blacklist_clear_failed", token_id=token_id, error=str(e))

    async def get_all_blacklisted(self) -> Set[str]:
        if not self._loaded:
            await self.load()
        return set(self._cache)
