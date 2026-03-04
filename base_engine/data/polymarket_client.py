import asyncio
import json
import time
from typing import Optional, Dict, List, Any, Tuple
import httpx
from structlog import get_logger
from config.settings import settings

logger = get_logger()

DEFAULT_GAMMA_API = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_API = "https://clob.polymarket.com"
DEFAULT_DATA_API = "https://data-api.polymarket.com"
DEFAULT_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _normalize_base_url(value: Optional[str], default: str) -> str:
    u = (value or "").strip().rstrip("/")
    if not u or not u.startswith(("http://", "https://")):
        return default.rstrip("/")
    return u



class TokenBucket:
    def __init__(self, rate: int, burst: int):
        self.rate = rate
        self.capacity = burst
        self.tokens = burst
        self.last_update = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> bool:
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    async def wait_for_token(self, tokens: int = 1):
        while not await self.acquire(tokens):
            wait_time = (tokens - self.tokens) / self.rate
            await asyncio.sleep(max(0.001, wait_time))


class MarketNotFoundError(Exception):
    """Custom exception for 404 errors when market doesn't exist or trades unavailable."""
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"
        self.lock = asyncio.Lock()
        self._last_open_log_time: float = 0  # Rate-limit OPEN warnings
        self._open_reject_count: int = 0  # Track rejected calls while OPEN

    async def reset(self):
        """Reset circuit breaker state with proper async lock to prevent race conditions."""
        async with self.lock:
            self.state = "CLOSED"
            self.failure_count = 0
            self.last_failure_time = None
            self._open_reject_count = 0

    async def call(self, func, *args, **kwargs):
        async with self.lock:
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.timeout:
                    self.state = "HALF_OPEN"
                    self.failure_count = 0
                    logger.info(
                        "Polymarket Gamma API: circuit breaker HALF_OPEN — testing recovery "
                        "(rejected %d calls while open)",
                        self._open_reject_count,
                    )
                    self._open_reject_count = 0
                else:
                    self._open_reject_count += 1
                    # Only log once per open period (not per call)
                    now = time.time()
                    if now - self._last_open_log_time > self.timeout:
                        self._last_open_log_time = now
                        remaining = self.timeout - (now - self.last_failure_time)
                        logger.warning(
                            "Polymarket Gamma API: circuit breaker OPEN (temporarily disabled after repeated failures). "
                            "Will retry in %.0fs. Suppressing further warnings until recovery.",
                            remaining,
                        )
                    raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            async with self.lock:
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                self.failure_count = 0
            return result
        except MarketNotFoundError:
            # 404 errors are expected for archived/closed markets, don't count as circuit breaker failure
            async with self.lock:
                logger.debug("404 error (expected for archived markets), not counting as circuit breaker failure")
            raise  # Re-raise so caller can handle it
        except Exception as e:
            # Only count SERVER errors (5xx) and connection errors as circuit breaker failures.
            # Client errors (4xx) mean the server is healthy but the request was bad — these
            # should NOT trip the circuit breaker (e.g. 422 from whale tracker passing condition_id).
            is_client_error = (
                isinstance(e, MarketNotFoundError) or
                (hasattr(e, 'response') and hasattr(e.response, 'status_code') and 400 <= e.response.status_code < 500)
            )

            async with self.lock:
                if not is_client_error:
                    # Server error or connection error — count as failure
                    self.failure_count += 1
                    self.last_failure_time = time.time()
                    if self.failure_count >= self.failure_threshold:
                        self.state = "OPEN"
                else:
                    # Client error (4xx) — server is fine, don't count as failure
                    logger.debug("Client error (4xx), not counting as circuit breaker failure")
            raise


class PolymarketClient:
    def __init__(self, private_key: Optional[str] = None, wallet_address: Optional[str] = None):
        self.gamma_api = _normalize_base_url(settings.POLYMARKET_GAMMA_API, DEFAULT_GAMMA_API)
        self.clob_api = _normalize_base_url(settings.POLYMARKET_CLOB_API, DEFAULT_CLOB_API)
        self.data_api = _normalize_base_url(settings.POLYMARKET_DATA_API, DEFAULT_DATA_API)
        self.ws_url = _normalize_base_url(settings.POLYMARKET_WS, DEFAULT_WS)

        self.rate_limiter = TokenBucket(
            rate=settings.RATE_LIMIT_REQUESTS_PER_SECOND,
            burst=settings.RATE_LIMIT_BURST
        )
        self.circuit_breaker = CircuitBreaker()
        # Adaptive rate-limit backoff: after 429, pause all requests until backoff_until
        self._backoff_until: Optional[float] = None
        self._consecutive_limits: int = 0
        self._adaptive_backoff_lock = asyncio.Lock()
        
        self.client: Optional[httpx.AsyncClient] = None
        self.request_cache: Dict[str, tuple] = {}
        self.cache_ttl = {
            "markets": settings.CACHE_TTL_MARKETS,
            "predictions": settings.CACHE_TTL_PREDICTIONS,
            "learning": settings.CACHE_TTL_LEARNING
        }
        
        self.private_key = (private_key or "").strip() or settings.PRIVATE_KEY
        self.wallet_address = (wallet_address or "").strip() or settings.WALLET_ADDRESS

    def get_circuit_breaker_state(self) -> dict:
        """Return circuit breaker state for diagnostics."""
        return {
            "state": self.circuit_breaker.state,
            "failure_count": self.circuit_breaker.failure_count,
            "rejected_calls": self.circuit_breaker._open_reject_count,
        }

    async def _ensure_client(self):
        if self.client is None:
            # Phase 6: colocated profile uses shorter timeouts
            if getattr(settings, "SPEED_PROFILE", "default") == "colocated":
                req = getattr(settings, "HTTP_TIMEOUT_COLOCATED", 10)
                timeout = httpx.Timeout(float(req), connect=min(5.0, req * 0.5))
            else:
                req = getattr(settings, "HTTP_TIMEOUT_SECONDS", 30)
                timeout = httpx.Timeout(float(req), connect=min(8.0, req * 0.4))
            limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
            headers = {"User-Agent": "PolymarketAI/1.0 (https://github.com; data)"}
            self.client = httpx.AsyncClient(
                timeout=timeout, limits=limits, headers=headers
            )
            # Session 46: Ensure circuit breaker starts clean on first connection
            await self.circuit_breaker.reset()
            logger.info("API client created (direct connection, circuit breaker reset)")
    
    async def __aenter__(self):
        await self._ensure_client()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            try:
                await self.client.aclose()
            except Exception as e:
                logger.warning(f"Error closing httpx client: {str(e)}")
            finally:
                self.client = None

    async def reset_http_client_for_loop(self) -> None:
        """
        Clear the httpx client reference so it is recreated in the current event loop on next use.
        Do NOT call aclose() from here when the client was created in another loop - that would
        raise 'Future attached to a different loop'. Just drop the reference.
        """
        self.client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        base_url: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        use_cache: bool = True,
        cache_key: Optional[str] = None
    ) -> Any:
        await self._ensure_client()
        # Honor adaptive backoff from previous 429
        while True:
            async with self._adaptive_backoff_lock:
                if self._backoff_until is None or time.time() >= self._backoff_until:
                    break
                wait_s = min(self._backoff_until - time.time(), 1.0)
            if wait_s > 0:
                logger.warning("In rate-limit backoff, waiting %.1fs", wait_s)
                await asyncio.sleep(wait_s)
        await self.rate_limiter.wait_for_token()
        
        if use_cache and method == "GET" and cache_key:
            if cache_key in self.request_cache:
                data, timestamp = self.request_cache[cache_key]
                cache_key_parts = cache_key.split(":", 1)
                if len(cache_key_parts) > 0:
                    ttl = self.cache_ttl.get(cache_key_parts[0], 60)
                else:
                    ttl = 60
                if time.time() - timestamp < ttl:
                    return data
        
        base = (base_url or "").rstrip("/")
        path = (endpoint or "").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        url = f"{base}{path}"
        
        async def _execute():
            for attempt in range(settings.MAX_RETRIES):
                try:
                    response = await self.client.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_data
                    )
                    response.raise_for_status()
                    try:
                        data = response.json()
                    except json.JSONDecodeError as je:
                        logger.error(
                            "Polymarket API returned non-JSON",
                            url=url,
                            status=response.status_code,
                            content_type=response.headers.get("content-type", ""),
                            body_preview=(response.text or "")[:400],
                        )
                        raise ValueError(
                            f"Polymarket API non-JSON response (status={response.status_code}): {response.text[:200]!r}"
                        ) from je
                    
                    if use_cache and method == "GET" and cache_key:
                        if isinstance(data, dict) and ("error" in data or "message" in data):
                            logger.debug("Not caching error response", cache_key=cache_key)
                        else:
                            self.request_cache[cache_key] = (data, time.time())
                            # BUG FIX: Remove multiple oldest entries to prevent memory leak
                            # Root cause: Cache cleanup only removes one item when threshold exceeded,
                            # but many requests can happen quickly, causing unbounded growth
                            # Impact: Memory usage grows unbounded, performance degrades, potential OOM
                            # Fix: Remove oldest 10% of entries when threshold exceeded
                            if len(self.request_cache) > 1000:
                                def _cache_ts(k):
                                    v = self.request_cache.get(k)
                                    return v[1] if isinstance(v, (tuple, list)) and len(v) >= 2 else 0.0
                                # Sort by timestamp and remove oldest 10%
                                sorted_keys = sorted(self.request_cache.keys(), key=_cache_ts)
                                remove_count = max(1, len(sorted_keys) // 10)  # Remove 10%
                                for key_to_remove in sorted_keys[:remove_count]:
                                    if key_to_remove in self.request_cache:
                                        del self.request_cache[key_to_remove]
                                logger.debug(f"Cleaned {remove_count} oldest cache entries")
                    
                    # Success: reset adaptive backoff so next request is not blocked
                    async with self._adaptive_backoff_lock:
                        if self._backoff_until is not None:
                            self._backoff_until = None
                            self._consecutive_limits = 0
                    return data
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    
                    # Handle rate limiting with adaptive backoff
                    if status_code == 429:
                        retry_after_header = e.response.headers.get("Retry-After")
                        retry_after = int(retry_after_header) if retry_after_header else min(
                            int(settings.RETRY_BACKOFF_BASE ** (attempt + 1)), 300
                        )
                        async with self._adaptive_backoff_lock:
                            self._consecutive_limits += 1
                            self._backoff_until = time.time() + retry_after
                        logger.warning(
                            "Rate limited (429); backoff %.0fs, consecutive_limits=%s",
                            retry_after, self._consecutive_limits,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    
                    # Handle 403 Forbidden
                    if status_code == 403:
                        logger.warning(
                            "Polymarket API 403 Forbidden (geographic restrictions or IP block). "
                            "Verify VPS IP is not in a restricted region.",
                            url=url,
                            status=403,
                        )
                        raise
                    
                    # Handle 404 Not Found - raise custom exception so caller can distinguish from empty results
                    # 404s are permanent (market doesn't exist), so don't retry or count as circuit breaker failure
                    if status_code == 404:
                        # For trades endpoint, raise MarketNotFoundError so caller knows it's a 404
                        if "/markets/" in endpoint and "/trades" in endpoint:
                            market_id = endpoint.split("/markets/")[1].split("/")[0] if "/markets/" in endpoint else None
                            logger.debug(
                                f"Market trades endpoint returned 404 (market may be archived/closed)",
                                endpoint=endpoint,
                                market_id=market_id
                            )
                            raise MarketNotFoundError(f"Market {market_id} trades not found (404)")
                        
                        # For other endpoints, log and return None
                        logger.debug(
                            f"Endpoint returned 404 Not Found",
                            endpoint=endpoint,
                            url=url
                        )
                        return None  # Return None for other 404s, don't raise exception
                    
                    # Handle 422 Unprocessable Entity — bad input (e.g. condition_id instead of numeric ID).
                    # Permanent error, don't retry.
                    if status_code == 422:
                        logger.debug(
                            "Endpoint returned 422 Unprocessable Entity (bad request format)",
                            endpoint=endpoint,
                            url=url,
                        )
                        return None

                    # For other HTTP errors (5xx), raise exception (will trigger retry/circuit breaker)
                    raise
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                    logger.warning(
                        "Polymarket API connection failed",
                        url=url,
                        error=type(e).__name__,
                        msg=str(e)[:200],
                    )
                    if attempt < settings.MAX_RETRIES - 1:
                        await asyncio.sleep(settings.RETRY_BACKOFF_BASE ** attempt)
                        continue
                    raise
                except Exception as e:
                    if attempt < settings.MAX_RETRIES - 1:
                        await asyncio.sleep(settings.RETRY_BACKOFF_BASE ** attempt)
                        continue
                    raise
        
        return await self.circuit_breaker.call(_execute)
    
    def reset_rate_limit_backoff(self) -> None:
        """Reset adaptive backoff after successful requests (e.g. after a 429 recovery)."""
        self._backoff_until = None
        self._consecutive_limits = 0
    
    def get_rate_limit_stats(self) -> Dict[str, Any]:
        """Return current rate-limit backoff state for monitoring."""
        return {
            "in_backoff": self._backoff_until is not None and time.time() < (self._backoff_until or 0),
            "backoff_until": self._backoff_until,
            "consecutive_limits": self._consecutive_limits,
        }
    
    async def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch markets from Polymarket Gamma API.
        
        Tries multiple parameter formats to ensure compatibility:
        1. First tries 'closed' parameter (closed=false for active markets)
        2. Falls back to 'active' parameter if closed doesn't work
        
        Args:
            active: If True, fetch active markets; if False, fetch closed markets
            limit: Maximum number of markets to return (1-500)
            offset: Number of markets to skip
            category: Optional category filter
            
        Returns:
            List of market dictionaries
            
        Raises:
            RuntimeError: If API call fails or returns unexpected format
        """
        # Try 'closed' parameter first (original format)
        params_closed: Dict[str, Any] = {
            "limit": max(1, min(limit, 500)),
            "offset": max(0, offset),
            "closed": str(not active).lower(),
        }
        if category:
            params_closed["category"] = str(category)
        
        # BUG FIX: Include offset in cache key to prevent collisions
        # Root cause: Cache keys didn't include offset, so different offset requests collided
        # Impact: Wrong market data returned from cache, pagination breaks, users see incorrect markets
        # Fix: Include offset in cache_key to ensure unique keys per request
        cache_key = f"markets:{active}:{limit}:{offset}:{category or ''}"
        
        try:
            # First attempt: Use 'closed' parameter (original approach)
            result = await self._request(
                "GET",
                "/markets",
                self.gamma_api,
                params=params_closed,
                cache_key=cache_key
            )
            
            # If we get a valid result, return it
            if result is not None:
                if isinstance(result, list) and len(result) > 0:
                    logger.debug(f"Successfully fetched {len(result)} markets using 'closed' parameter")
                    return result
                elif isinstance(result, dict):
                    # Check if it's an error response
                    if "error" in result or "message" in result:
                        error_msg = result.get("error") or result.get("message", "Unknown error")
                        logger.warning(f"API returned error with 'closed' parameter: {error_msg}")
                        # Fall through to try 'active' parameter
                    elif "data" in result and isinstance(result["data"], list):
                        logger.debug("Found markets in 'data' key using 'closed' parameter")
                        return result["data"]
                    elif "markets" in result and isinstance(result["markets"], list):
                        logger.debug("Found markets in 'markets' key using 'closed' parameter")
                        return result["markets"]
            
            # If we got None or empty result, try 'active' parameter as fallback
            logger.info(f"Trying 'active' parameter as fallback (closed parameter returned empty/None)")
            params_active: Dict[str, Any] = {
                "limit": max(1, min(limit, 500)),
                "offset": max(0, offset),
                "active": str(active).lower(),
            }
            if category:
                params_active["category"] = str(category)
            
            result = await self._request(
                "GET",
                "/markets",
                self.gamma_api,
                params=params_active,
                cache_key=None  # Don't cache fallback attempts
            )
            
        except Exception as e:
            # If first attempt failed, try 'active' parameter before giving up
            if "closed" in str(params_closed):
                try:
                    logger.info(f"First attempt failed, trying 'active' parameter: {str(e)}")
                    params_active: Dict[str, Any] = {
                        "limit": max(1, min(limit, 500)),
                        "offset": max(0, offset),
                        "active": str(active).lower(),
                    }
                    if category:
                        params_active["category"] = str(category)
                    
                    result = await self._request(
                        "GET",
                        "/markets",
                        self.gamma_api,
                        params=params_active,
                        cache_key=None
                    )
                except Exception as e2:
                    logger.error(
                        "Both parameter formats failed",
                        closed_error=str(e),
                        active_error=str(e2),
                        active=active,
                        limit=limit,
                        offset=offset,
                        exc_info=True
                    )
                    raise RuntimeError(
                        f"Polymarket API get_markets() failed with both parameter formats. "
                        f"Closed param error: {str(e)}. Active param error: {str(e2)}"
                    ) from e2
            else:
                raise
        
        # Process the result (from either attempt)
        if result is None:
            logger.warning("Polymarket API returned None for get_markets")
            return []
        
        if isinstance(result, list):
            return result
        
        if isinstance(result, dict):
            if "error" in result or "message" in result:
                error_msg = result.get("error") or result.get("message", "Unknown error")
                logger.error(
                    "Polymarket API returned error dict",
                    error=error_msg,
                    result_keys=list(result.keys())[:10]
                )
                raise RuntimeError(f"Polymarket API error: {error_msg}")
            
            if "data" in result and isinstance(result["data"], list):
                logger.debug("Found markets in 'data' key")
                return result["data"]
            
            if "markets" in result and isinstance(result["markets"], list):
                logger.debug("Found markets in 'markets' key")
                return result["markets"]
            
            logger.error(
                "Polymarket API returned unexpected dict structure",
                result_keys=list(result.keys())[:10],
                result_preview=str(result)[:300]
            )
            raise RuntimeError(
                f"Polymarket API returned unexpected dict structure. Keys: {list(result.keys())[:10]}"
            )
        
        logger.error(
            "Polymarket API returned unexpected type",
            result_type=type(result).__name__,
            result_preview=str(result)[:300]
        )
        raise RuntimeError(
            f"Polymarket API returned unexpected type: {type(result).__name__}"
        )
    
    async def get_events(
        self,
        active: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch events from Gamma API (recommended for market discovery).
        Events contain nested markets with clobTokenIds - better token ID coverage than /markets.
        Returns flattened list of markets (same shape as get_markets) for compatibility.
        """
        params: Dict[str, Any] = {
            "limit": max(1, min(limit, 100)),
            "offset": max(0, offset),
        }
        if active:
            params["active"] = "true"
            params["closed"] = "false"
        else:
            params["closed"] = "true"

        cache_key = f"events:{active}:{limit}:{offset}"
        result = await self._request(
            "GET",
            "/events",
            self.gamma_api,
            params=params,
            cache_key=cache_key,
        )
        if result is None:
            return []
        if isinstance(result, dict) and ("error" in result or "message" in result):
            raise RuntimeError(result.get("error") or result.get("message", "Events API error"))
        events = result if isinstance(result, list) else (result.get("data") or result.get("events") or [])
        if not isinstance(events, list):
            return []

        markets: List[Dict[str, Any]] = []
        for evt in events:
            if not isinstance(evt, dict):
                continue
            evt_markets = evt.get("markets") or []
            if isinstance(evt_markets, str):
                try:
                    import json
                    evt_markets = json.loads(evt_markets) if evt_markets.strip() else []
                except json.JSONDecodeError:
                    evt_markets = []
            if not isinstance(evt_markets, list):
                continue
            for m in evt_markets:
                if isinstance(m, dict) and m.get("id"):
                    m = dict(m)
                    if evt.get("id") and "event_id" not in m:
                        m["event_id"] = str(evt["id"])
                    if evt.get("slug") and "event_slug" not in m:
                        m["event_slug"] = str(evt.get("slug", ""))
                    markets.append(m)
        logger.debug(f"get_events: {len(events)} events -> {len(markets)} flattened markets")
        return markets

    async def check_gamma_connectivity(self) -> Tuple[bool, str]:
        try:
            r = await self.get_events(active=True, limit=1, offset=0)
            if not r:
                r = await self.get_markets(active=True, limit=1, offset=0)
            if isinstance(r, list):
                if len(r) > 0:
                    return (True, f"OK - Received {len(r)} market(s)")
                else:
                    return (True, "OK - API responded but returned empty list")
            return (False, f"Unexpected response type: {type(r).__name__}")
        except Exception as e:
            logger.error("Connectivity check failed", error=str(e), exc_info=True)
            return (False, str(e))

    async def get_polymarket_health(self) -> Dict[str, str]:
        ok, msg = await self.check_gamma_connectivity()
        return {"gamma": "ok" if ok else "error", "message": msg}

    async def reset_circuit_breaker(self) -> None:
        await self.circuit_breaker.reset()

    async def get_market(self, market_id: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Fetch a single market by ID from Polymarket Gamma API.
        
        CRITICAL FIX: Handle wrapped API responses (e.g., {"data": {...}})
        Root cause: API sometimes wraps single market responses in {"data": {...}} structure,
        but we were only checking top-level keys, causing "No tokens found" errors
        Impact: Historical price ingestion returns 0 prices because tokens can't be extracted
        Fix: Unwrap nested responses similar to get_markets() handling
        
        Args:
            market_id: The market ID to fetch
            use_cache: If False, bypass cache (use when enriching for token IDs - ensures fresh clobTokenIds)
            
        Returns:
            Dict with market data, unwrapped from any API response wrapper
        """
        cache_key = f"market:{market_id}" if use_cache else None
        result = await self._request(
            "GET",
            f"/markets/{market_id}",
            self.gamma_api,
            cache_key=cache_key,
            use_cache=use_cache
        )
        
        # Handle wrapped responses (similar to get_markets())
        if isinstance(result, dict):
            # Check if wrapped in "data" key
            if "data" in result and isinstance(result["data"], dict):
                logger.debug(f"Unwrapped market {market_id} from 'data' key")
                return result["data"]
            # Check if wrapped in "market" key
            if "market" in result and isinstance(result["market"], dict):
                logger.debug(f"Unwrapped market {market_id} from 'market' key")
                return result["market"]
            # If it's already a dict with expected keys, return as-is
            if "tokens" in result or "id" in result or "question" in result:
                return result
            # Log unexpected structure for debugging
            logger.warning(
                f"Market {market_id} response has unexpected structure",
                keys=list(result.keys())[:10],
                preview=str(result)[:500]
            )
        
        # If not a dict, log and return as-is (caller will handle error)
        if not isinstance(result, dict):
            logger.debug(
                f"Market {market_id} returned non-dict response",
                result_type=type(result).__name__,
            )
        
        return result
    
    async def get_market_trades(
        self,
        market_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        params = {"limit": limit, "offset": offset}
        cache_key = f"trades:{market_id}:{limit}:{offset}"
        return await self._request(
            "GET",
            f"/markets/{market_id}/trades",
            self.gamma_api,
            params=params,
            cache_key=cache_key
        )
    
    async def get_user_activity(
        self,
        user_address: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Fetch user activity (trades). Tries Gamma API first, falls back to Data API on 404/401.
        Returns normalized list with: type, marketId, tokenId, side, size, price, timestamp, id.
        """
        try:
            params = {"limit": limit, "offset": offset}
            cache_key = f"activity:{user_address}:{limit}:{offset}"
            raw = await self._request(
                "GET",
                f"/users/{user_address}/activity",
                self.gamma_api,
                params=params,
                cache_key=cache_key
            )
            if raw and isinstance(raw, list) and len(raw) > 0:
                return raw
            # Gamma returned None or empty (e.g. 404 returns None) - try Data API
        except Exception as e:
            err_str = str(e).lower()
            if "404" in err_str or "401" in err_str or "not found" in err_str or "unauthorized" in err_str:
                logger.debug("Gamma /users/activity failed, trying Data API fallback: %s", e)
            else:
                raise
        # Fallback: Data API /activity (user param, type=TRADE)
        try:
            params = {"user": user_address, "limit": min(limit, 500), "offset": offset, "type": "TRADE"}
            raw = await self._request(
                "GET",
                "/activity",
                self.data_api,
                params=params,
                use_cache=False,
                cache_key=f"data_activity:{user_address}:{offset}:{limit}"
            )
            if not raw or not isinstance(raw, list):
                return []
            # Normalize Data API format to Gamma-like: marketId, tokenId, type, side, size, price, timestamp
            out = []
            for act in raw:
                if not isinstance(act, dict) or act.get("type") != "TRADE":
                    continue
                cond = (act.get("conditionId") or "").strip()
                slug = (act.get("slug") or "").strip()
                market_id = cond or slug
                if not market_id:
                    continue
                asset = (act.get("asset") or "").strip()
                ts = act.get("timestamp")
                tx = (act.get("transactionHash") or "").strip()
                tid = tx if tx else f"trade_{cond}_{ts}_{asset}"
                out.append({
                    "id": tid,
                    "marketId": market_id,
                    "tokenId": asset,
                    "type": "trade",
                    "side": (act.get("side") or "BUY").upper(),
                    "size": float(act.get("size", 0) or 0),
                    "price": float(act.get("price", 0) or 0),
                    "timestamp": ts,
                })
            return out
        except Exception as e:
            logger.warning("Data API /activity fallback failed: %s", e)
            return []
    
    async def get_top_users(self, limit: int = 200) -> List[Dict]:
        """
        Fetch top traders by profit. Tries Gamma API first, falls back to Data API.
        Returns normalized list with keys: address, totalProfit, totalVolume, winRate, totalTrades, roi.
        """
        # Try Gamma API first (address, totalProfit, totalVolume, winRate, totalTrades, roi)
        try:
            params = {"limit": limit, "sort": "profit", "order": "desc"}
            cache_key = f"top_users:{limit}"
            raw = await self._request(
                "GET",
                "/users",
                self.gamma_api,
                params=params,
                cache_key=cache_key
            )
            if raw and isinstance(raw, list) and len(raw) > 0 and not isinstance(raw[0], str):
                # Gamma may use address or proxyWallet; normalize to address
                out = []
                for u in raw:
                    if not isinstance(u, dict):
                        continue
                    addr = u.get("address") or u.get("proxyWallet")
                    if addr and isinstance(addr, str) and addr.startswith("0x"):
                        out.append({
                            "address": addr,
                            "totalProfit": u.get("totalProfit", u.get("pnl", 0.0)),
                            "totalVolume": u.get("totalVolume", u.get("vol", 0.0)),
                            "winRate": u.get("winRate", 0.5),
                            "totalTrades": u.get("totalTrades", 0),
                            "roi": u.get("roi", 0.0),
                        })
                if out:
                    return out
        except Exception as e:
            logger.warning("Gamma /users failed, trying Data API fallback: %s", e)
        # Fallback: Data API leaderboard (proxyWallet, pnl, vol). API caps at 50/request; paginate when limit > 50.
        try:
            out: List[Dict] = []
            seen: set = set()
            page_size = 50
            offset = 0
            while len(out) < limit:
                params = {"limit": page_size, "offset": offset, "orderBy": "PNL"}
                raw = await self._request(
                    "GET",
                    "/v1/leaderboard",
                    self.data_api,
                    params=params,
                    use_cache=False,
                    cache_key=f"leaderboard:{offset}:{page_size}"
                )
                if not raw or not isinstance(raw, list) or len(raw) == 0:
                    break
                for u in raw:
                    if not isinstance(u, dict):
                        continue
                    addr = u.get("proxyWallet") or u.get("address")
                    if not addr or addr in seen:
                        continue
                    seen.add(addr)
                    out.append({
                        "address": addr,
                        "totalProfit": float(u.get("pnl", 0.0) or 0.0),
                        "totalVolume": float(u.get("vol", 0.0) or 0.0),
                        "winRate": 0.5,
                        "totalTrades": 0,
                        "roi": 0.0,
                    })
                    if len(out) >= limit:
                        break
                if len(raw) < page_size:
                    break
                offset += page_size
                await asyncio.sleep(0.2)
            return out[:limit]
        except Exception as e:
            logger.warning("Data API leaderboard fallback failed: %s", e)
        return []
    
    async def get_price_history(
        self,
        token_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        interval: Optional[str] = None,
        fidelity: Optional[int] = None
    ) -> Dict:
        """
        Fetch historical price data for a CLOB token with comprehensive validation.
        
        CRITICAL FIX: Validates API parameters and handles errors properly.
        
        Args:
            token_id: The CLOB token ID (from market.tokens[].tokenId)
            start_ts: Start time as Unix timestamp in UTC (optional)
            end_ts: End time as Unix timestamp in UTC (optional, use yesterday to avoid today's date issues)
            interval: Duration string - "1m", "1w", "1d", "6h", "1h", "max" (optional, mutually exclusive with startTs/endTs)
            fidelity: Resolution in minutes (optional)
            
        Returns:
            Dict with "history" key containing list of {"t": timestamp, "p": price} objects
            Returns {"history": []} if no data found or API error
        """
        import httpx
        
        # CRITICAL FIX: Validate token_id before making API call
        if not token_id or not isinstance(token_id, str):
            logger.warning(f"Invalid token_id for price history: {token_id}")
            return {"history": []}
        
        # V2 FIX: CLOB API uses "market" parameter with token_id value (not "token")
        # This is the correct format for Polymarket V2 CLOB API
        params = {"market": str(token_id)}
        
        # Add time range if provided
        if start_ts is not None:
            params["startTs"] = int(start_ts)
        if end_ts is not None:
            params["endTs"] = int(end_ts)
        if interval:
            params["interval"] = interval
        if fidelity is not None:
            params["fidelity"] = int(fidelity)
        
        try:
            # Don't cache price history - it's historical data that changes over time
            response = await self._request(
                "GET",
                "/prices-history",
                self.clob_api,
                params=params,
                use_cache=False
            )
            
            # CRITICAL FIX: Validate response structure before returning
            if not isinstance(response, dict):
                logger.warning(f"Price history API returned non-dict: {type(response).__name__}")
                return {"history": []}
            
            # Check for error in response
            if "error" in response:
                logger.warning(f"Price history API error for token {token_id}: {response['error']}")
                return {"history": []}
            
            # Ensure "history" key exists
            if "history" not in response:
                logger.warning(f"Price history API response missing 'history' key for token {token_id}. Keys: {list(response.keys())}")
                return {"history": []}
            
            # Validate history is a list
            history = response.get("history", [])
            if not isinstance(history, list):
                logger.warning(f"Price history 'history' is not a list for token {token_id}: {type(history).__name__}")
                return {"history": []}
            
            logger.debug(f"Price history API returned {len(history)} data points for token {token_id}")
            return response
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Price history not found for token {token_id} (404) - token may be invalid")
                return {"history": []}
            elif e.response.status_code == 400:
                logger.warning(f"Price history API bad request for token {token_id}: {e.response.text}")
                return {"history": []}
            else:
                logger.error(f"Price history API error {e.response.status_code} for token {token_id}: {e}")
                return {"history": []}
        except Exception as e:
            logger.error(f"Price history API call failed for token {token_id}: {e}", exc_info=True)
            return {"history": []}
    
    async def get_orderbook(self, market_id: str, token_id: str) -> Dict:
        params = {"token_id": token_id}
        cache_key = f"orderbook:{market_id}:{token_id}"
        return await self._request(
            "GET",
            "/book",
            self.clob_api,
            params=params,
            cache_key=cache_key,
            use_cache=False
        )
    
    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float
    ) -> Dict:
        if not self.private_key:
            raise ValueError("Private key required for order placement")

        order_data = {
            "market": market_id,
            "token": token_id,
            "side": side,
            "size": str(size),
            "price": str(price)
        }
        
        return await self._request(
            "POST",
            "/orders",
            self.clob_api,
            json_data=order_data,
            use_cache=False
        )
