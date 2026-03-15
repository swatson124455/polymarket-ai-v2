"""
Redis-backed shared rate limiter for API calls across all bots.

Uses a token-bucket algorithm stored in Redis so that all 14 bots share
a single rate-limit budget per endpoint.  Falls back to in-memory token
buckets when Redis is unavailable.

Redis key layout:
    ratelimit:{endpoint}:tokens      — current token count (float)
    ratelimit:{endpoint}:last_refill — last refill timestamp (float, unix epoch)
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import redis.asyncio as aioredis
from structlog import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Priority constants
# ---------------------------------------------------------------------------
PRIORITY_CRITICAL = 1  # kill switch, position reconciliation — always allowed
PRIORITY_HIGH = 2      # live match data, order submission — 60% of budget
PRIORITY_NORMAL = 3    # pre-match data, market scanning  — 30% of budget
PRIORITY_LOW = 4       # diagnostics, CLV backfill         — 10% of budget

# Fraction of burst capacity reserved for each priority tier.
# CRITICAL is uncapped (uses full burst).
_PRIORITY_BUDGET: Dict[int, float] = {
    PRIORITY_CRITICAL: 1.0,
    PRIORITY_HIGH: 0.60,
    PRIORITY_NORMAL: 0.30,
    PRIORITY_LOW: 0.10,
}


@dataclass
class _EndpointConfig:
    """Per-endpoint rate and burst configuration."""
    rate: float       # tokens added per second
    burst: int        # max tokens (bucket capacity)
    # In-memory fallback state (used only when Redis is down).
    _mem_tokens: float = 0.0
    _mem_last_refill: float = field(default_factory=time.monotonic)


@dataclass
class _EndpointStats:
    requests_allowed: int = 0
    requests_denied: int = 0


class SharedRateLimiter:
    """Redis-backed token-bucket rate limiter shared across all bots.

    Parameters
    ----------
    redis_url : str
        Redis connection URL (e.g. ``redis://localhost:6379/0``).
    default_rate : float
        Default refill rate in tokens/second for unconfigured endpoints.
    default_burst : int
        Default bucket capacity for unconfigured endpoints.
    """

    def __init__(
        self,
        redis_url: str,
        default_rate: float = 10.0,
        default_burst: int = 20,
    ) -> None:
        self._redis_url = redis_url
        self._default_rate = default_rate
        self._default_burst = default_burst

        self._redis: Optional[aioredis.Redis] = None
        self._redis_available: bool = False

        self._endpoints: Dict[str, _EndpointConfig] = {}
        self._stats: Dict[str, _EndpointStats] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Connect to Redis.  Safe to call multiple times."""
        try:
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=3,
            )
            await self._redis.ping()
            self._redis_available = True
            logger.info("shared_rate_limiter.redis_connected")
        except Exception as exc:
            logger.warning(
                "shared_rate_limiter.redis_unavailable",
                error=str(exc),
                fallback="in-memory",
            )
            self._redis = None
            self._redis_available = False

    async def close(self) -> None:
        """Shut down the Redis connection."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._redis_available = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure_endpoint(self, name: str, rate: float, burst: int) -> None:
        """Register or update rate-limit parameters for *name*.

        Parameters
        ----------
        name : str
            Logical endpoint name (e.g. ``polymarket_clob``).
        rate : float
            Sustained refill rate in requests per second.
        burst : int
            Maximum tokens (bucket capacity).
        """
        self._endpoints[name] = _EndpointConfig(
            rate=rate,
            burst=burst,
            _mem_tokens=float(burst),
            _mem_last_refill=time.monotonic(),
        )
        if name not in self._stats:
            self._stats[name] = _EndpointStats()

    def _get_config(self, endpoint: str) -> _EndpointConfig:
        """Return config for *endpoint*, creating a default if missing."""
        if endpoint not in self._endpoints:
            self.configure_endpoint(
                endpoint, self._default_rate, self._default_burst
            )
        return self._endpoints[endpoint]

    # ------------------------------------------------------------------
    # Acquire — single attempt
    # ------------------------------------------------------------------

    async def acquire(self, endpoint: str, priority: int = 1) -> bool:
        """Try to consume one token for *endpoint*.

        Parameters
        ----------
        endpoint : str
            Logical endpoint name.
        priority : int
            1=CRITICAL … 4=LOW.  Higher-priority callers see more of the
            bucket capacity; CRITICAL callers are never refused.

        Returns
        -------
        bool
            ``True`` if a token was consumed, ``False`` if rate-limited.
        """
        # CRITICAL priority is always allowed.
        if priority <= PRIORITY_CRITICAL:
            self._record(endpoint, allowed=True)
            return True

        cfg = self._get_config(endpoint)

        if self._redis_available:
            try:
                allowed = await self._redis_acquire(endpoint, cfg, priority)
            except Exception as exc:
                logger.warning(
                    "shared_rate_limiter.redis_error",
                    endpoint=endpoint,
                    error=str(exc),
                    fallback="in-memory",
                )
                self._redis_available = False
                allowed = self._mem_acquire(cfg, priority)
        else:
            allowed = self._mem_acquire(cfg, priority)

        self._record(endpoint, allowed)
        return allowed

    # ------------------------------------------------------------------
    # Wait-and-acquire — blocks up to *timeout* seconds
    # ------------------------------------------------------------------

    async def wait_and_acquire(
        self,
        endpoint: str,
        priority: int = 1,
        timeout: float = 30.0,
    ) -> bool:
        """Block until a token is available or *timeout* elapses.

        Parameters
        ----------
        endpoint : str
            Logical endpoint name.
        priority : int
            1=CRITICAL … 4=LOW.
        timeout : float
            Maximum seconds to wait.

        Returns
        -------
        bool
            ``True`` if a token was eventually consumed, ``False`` on timeout.
        """
        if priority <= PRIORITY_CRITICAL:
            self._record(endpoint, allowed=True)
            return True

        deadline = time.monotonic() + timeout
        cfg = self._get_config(endpoint)
        wait_interval = min(1.0 / max(cfg.rate, 0.01), 1.0)

        while time.monotonic() < deadline:
            ok = await self.acquire(endpoint, priority)
            if ok:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(wait_interval, remaining))

        self._record(endpoint, allowed=False)
        return False

    # ------------------------------------------------------------------
    # Startup stagger
    # ------------------------------------------------------------------

    @staticmethod
    async def startup_stagger(bot_name: str) -> None:
        """Wait 0-30 s based on a hash of *bot_name*.

        Prevents all 14 bots from issuing their first API burst at the
        same instant after a coordinated restart.
        """
        digest = hashlib.sha256(bot_name.encode()).hexdigest()
        delay = (int(digest[:8], 16) % 3000) / 100.0  # 0.00–30.00 s
        logger.info(
            "shared_rate_limiter.startup_stagger",
            bot=bot_name,
            delay_s=round(delay, 2),
        )
        await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """Return per-endpoint request counts.

        Returns
        -------
        dict
            ``{endpoint: {"allowed": N, "denied": M}}``
        """
        return {
            ep: {
                "allowed": s.requests_allowed,
                "denied": s.requests_denied,
            }
            for ep, s in self._stats.items()
        }

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_default(cls, redis_url: str) -> "SharedRateLimiter":
        """Create a limiter pre-configured with production endpoint budgets.

        Endpoints
        ---------
        polymarket_positions : 15 req/s, burst 150
        polymarket_clob      : 350 req/s, burst 3500
        polymarket_general   : 1500 req/s, burst 15000
        pandascore           : 0.28 req/s (~1000/hr), burst 5
        oddspapi             : 0.0001 req/s (~250/month), burst 1
        """
        limiter = cls(redis_url=redis_url, default_rate=10.0, default_burst=20)
        limiter.configure_endpoint("polymarket_positions", rate=15.0, burst=150)
        limiter.configure_endpoint("polymarket_clob", rate=350.0, burst=3500)
        limiter.configure_endpoint("polymarket_general", rate=1500.0, burst=15000)
        limiter.configure_endpoint("pandascore", rate=0.28, burst=5)
        limiter.configure_endpoint("oddspapi", rate=0.0001, burst=1)
        return limiter

    # ------------------------------------------------------------------
    # Internal — Redis token bucket
    # ------------------------------------------------------------------

    async def _redis_acquire(
        self,
        endpoint: str,
        cfg: _EndpointConfig,
        priority: int,
    ) -> bool:
        """Atomically refill and try to consume one token via Redis."""
        assert self._redis is not None

        tokens_key = f"ratelimit:{endpoint}:tokens"
        refill_key = f"ratelimit:{endpoint}:last_refill"
        now = time.time()

        effective_cap = self._effective_capacity(cfg.burst, priority)

        # Lua script executed atomically inside Redis.
        # Returns 1 (allowed) or 0 (denied).
        lua = """
        local tokens_key  = KEYS[1]
        local refill_key  = KEYS[2]
        local rate         = tonumber(ARGV[1])
        local cap          = tonumber(ARGV[2])
        local now          = tonumber(ARGV[3])
        local full_cap     = tonumber(ARGV[4])

        local last = tonumber(redis.call('GET', refill_key) or now)
        if last == nil then last = now end
        local tokens = tonumber(redis.call('GET', tokens_key) or full_cap)
        if tokens == nil then tokens = full_cap end

        -- refill
        local elapsed = math.max(now - last, 0)
        tokens = math.min(tokens + elapsed * rate, full_cap)

        if tokens >= 1 then
            tokens = tokens - 1
            redis.call('SET', tokens_key, tostring(tokens))
            redis.call('SET', refill_key, tostring(now))
            -- Expire keys after 5 minutes of inactivity
            redis.call('EXPIRE', tokens_key, 300)
            redis.call('EXPIRE', refill_key, 300)
            return 1
        else
            redis.call('SET', tokens_key, tostring(tokens))
            redis.call('SET', refill_key, tostring(now))
            redis.call('EXPIRE', tokens_key, 300)
            redis.call('EXPIRE', refill_key, 300)
            return 0
        end
        """

        result = await self._redis.eval(
            lua,
            2,
            tokens_key,
            refill_key,
            str(cfg.rate),
            str(effective_cap),
            str(now),
            str(cfg.burst),
        )
        return int(result) == 1

    # ------------------------------------------------------------------
    # Internal — in-memory fallback
    # ------------------------------------------------------------------

    def _mem_acquire(self, cfg: _EndpointConfig, priority: int) -> bool:
        """In-memory token bucket (per-process, no cross-bot sharing)."""
        now = time.monotonic()
        elapsed = max(now - cfg._mem_last_refill, 0.0)
        cfg._mem_tokens = min(
            cfg._mem_tokens + elapsed * cfg.rate, float(cfg.burst)
        )
        cfg._mem_last_refill = now

        effective_cap = self._effective_capacity(cfg.burst, priority)
        if cfg._mem_tokens > (cfg.burst - effective_cap):
            # Not enough headroom for this priority tier.
            if cfg._mem_tokens < 1.0:
                return False

        if cfg._mem_tokens >= 1.0:
            cfg._mem_tokens -= 1.0
            return True
        return False

    # ------------------------------------------------------------------
    # Internal — helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_capacity(burst: int, priority: int) -> float:
        """Return the portion of *burst* available to *priority*."""
        fraction = _PRIORITY_BUDGET.get(priority, _PRIORITY_BUDGET[PRIORITY_LOW])
        return max(burst * fraction, 1.0)

    def _record(self, endpoint: str, allowed: bool) -> None:
        stats = self._stats.setdefault(endpoint, _EndpointStats())
        if allowed:
            stats.requests_allowed += 1
        else:
            stats.requests_denied += 1
