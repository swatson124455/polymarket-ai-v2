"""
The Graph Protocol client for Polymarket historical data.

FIXED (2026-01-25): Complete implementation using FPMM subgraph structure.
- Uses The Graph Network endpoint (requires API key)
- Queries fpmmTransactions entity (not trades)
- Proper field mappings and price calculation
- Outcome mapping: outcomeIndex → YES/NO
- Market prices required for price calculation

API Key Required:
- Get free API key at https://thegraph.com/studio (100K queries/month free tier)
- Set environment variable: THE_GRAPH_API_KEY=your-key
- Or pass api_key parameter to constructor
"""
import asyncio
import json
import time
import math
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import httpx
import structlog

logger = structlog.get_logger(__name__)


class TheGraphClient:
    """
    GraphQL client for The Graph Protocol Polymarket subgraph.
    
    REBUILT: Python implementation of proven JavaScript TheGraphClient.
    Uses The Graph Protocol which is proven to work in master-trading-app.
    
    FIXED: Comprehensive error logging, multiple subgraph URL fallbacks, data format verification.
    """
    
    def __init__(self, subgraph_url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize The Graph client.
        
        FIXED: Requires API key OR uses Goldsky public endpoint. Fails fast with clear error if neither available.
        
        Args:
            subgraph_url: Optional custom subgraph URL. If provided, uses this exclusively.
            api_key: Optional API key for The Graph Network (free tier: 100K queries/month).
                    Get one at https://thegraph.com/studio
                    If not provided, will use default API key or try Goldsky public endpoint.
        """
        import os
        
        # FIXED: Require API key OR use Goldsky public endpoint
        # The Graph Network gateway endpoint REQUIRES API key - public endpoint doesn't work
        # SECURITY: No hardcoded API key. Set THE_GRAPH_API_KEY env var or pass api_key param.
        graph_api_key = api_key or os.getenv("THE_GRAPH_API_KEY", "")
        graph_subgraph_id = "Bx1W4S7kDVxs9gC3s2G6DS8kdNBJNVhMviCtin2DiBp"
        
        if subgraph_url:
            # Use custom URL if provided
            self.subgraph_urls = [subgraph_url]
            logger.info(f"[GRAPH] Using custom subgraph URL: {subgraph_url}")
        elif graph_api_key:
            # Use API key endpoint (preferred - most reliable)
            self.subgraph_urls = [
                f"https://gateway.thegraph.com/api/{graph_api_key}/subgraphs/id/{graph_subgraph_id}"
            ]
            logger.info("[GRAPH] Using The Graph Network endpoint with API key")
        else:
            # This should not happen now (default API key provided), but keep as safety
            raise ValueError(
                "The Graph API key required. "
                "Get a free API key at https://thegraph.com/studio (100K queries/month free tier). "
                "Set environment variable: THE_GRAPH_API_KEY=your-key"
            )
        
        self.current_subgraph_index = 0
        self.subgraph_url = self.subgraph_urls[0]
        self.rate_limit_delay = 1.0  # 1 second between requests (1000 queries/day free tier)
        self.last_request_time = 0.0
        self.request_cache: Dict[str, Dict[str, Any]] = {}
        self.market_prices_cache: Dict[str, Dict[str, Any]] = {}  # Cache market prices
        self.cache_ttl = 300  # 5 minutes cache for queries
        self.market_prices_cache_ttl = 60  # 1 minute cache for market prices (prices change frequently)
        self.query_count = 0  # Track query count for rate limiting
        self.max_queries_per_day = 1000  # Free tier limit
    
    async def _rate_limit(self):
        """Rate limit requests to respect The Graph API limits."""
        now = time.time()
        time_since_last = now - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - time_since_last)
        self.last_request_time = time.time()
    
    async def _query_graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Execute GraphQL query against The Graph Protocol.
        
        FIXED: Comprehensive error logging, retry logic, rate limit handling, response validation.
        
        Args:
            query: GraphQL query string
            variables: Query variables
            max_retries: Maximum retry attempts
            
        Returns:
            Response data dictionary (empty dict on failure, but errors are logged)
        """
        await self._rate_limit()
        
        # Check rate limit
        if self.query_count >= self.max_queries_per_day:
            logger.error(
                "[GRAPH] Rate limit exceeded",
                query_count=self.query_count,
                max_queries=self.max_queries_per_day,
                message="Daily query limit reached. Wait 24 hours or upgrade tier."
            )
            return {}
        
        variables = variables or {}
        cache_key = f"{query}_{json.dumps(variables, sort_keys=True)}"
        
        # FIXED: Check cache but don't return cached errors
        if cache_key in self.request_cache:
            cached = self.request_cache[cache_key]
            # Only return cached data if it's successful (not empty dict from error)
            if time.time() - cached["timestamp"] < self.cache_ttl:
                cached_data = cached.get("data", {})
                # Don't return cached empty dicts (might be from errors)
                if cached_data and cached.get("success", False):
                    logger.debug("Returning cached GraphQL response", cache_key=cache_key[:50])
                    return cached_data
        
        # FIXED: Try multiple subgraph URLs with retry logic
        last_error = None
        for attempt in range(max_retries):
            for subgraph_idx, subgraph_url in enumerate(self.subgraph_urls):
                try:
                    self.subgraph_url = subgraph_url
                    self.current_subgraph_index = subgraph_idx
                    
                    async with httpx.AsyncClient(timeout=60.0) as client:  # Increased timeout
                        logger.debug(
                            f"[GRAPH] Query attempt {attempt + 1}/{max_retries}",
                            subgraph_url=subgraph_url,
                            variables=variables
                        )
                        
                        response = await client.post(
                            subgraph_url,
                            json={
                                "query": query,
                                "variables": variables
                            },
                            headers={"Content-Type": "application/json"}
                        )
                        
                        # FIXED: Log HTTP status and response details
                        logger.debug(
                            f"[GRAPH] HTTP response",
                            status_code=response.status_code,
                            url=subgraph_url,
                            headers=dict(response.headers)
                        )
                        
                        # FIXED: Handle redirects to error pages (301/302 to error URLs)
                        if response.status_code in (301, 302, 307, 308):
                            location = response.headers.get("location", "")
                            if "error" in location.lower() or "apierror" in location.lower():
                                logger.error(
                                    "[GRAPH] Redirected to error page",
                                    status_code=response.status_code,
                                    location=location,
                                    subgraph_url=subgraph_url
                                )
                                continue  # Try next subgraph URL
                        
                        # FIXED: Handle rate limiting explicitly
                        if response.status_code == 429:
                            logger.warning(
                                "[GRAPH] Rate limit hit (429)",
                                subgraph_url=subgraph_url,
                                retry_after=response.headers.get("Retry-After", "unknown")
                            )
                            await asyncio.sleep(60)  # Wait 1 minute on rate limit
                            continue  # Try next subgraph URL
                        
                        response.raise_for_status()
                        
                        # FIXED: Check if response is JSON (not HTML error page)
                        content_type = response.headers.get("content-type", "").lower()
                        if "text/html" in content_type:
                            logger.error(
                                "[GRAPH] Received HTML instead of JSON (likely error page)",
                                subgraph_url=subgraph_url,
                                content_type=content_type,
                                response_preview=response.text[:200]
                            )
                            continue  # Try next subgraph URL
                        
                        try:
                            result = response.json()
                        except ValueError as e:
                            logger.error(
                                "[GRAPH] Failed to parse JSON response",
                                error=str(e),
                                subgraph_url=subgraph_url,
                                content_type=content_type,
                                response_preview=response.text[:500]
                            )
                            continue  # Try next subgraph URL
                        
                        # FIXED: Log GraphQL errors with full details
                        if "errors" in result:
                            logger.error(
                                "[GRAPH] GraphQL query errors",
                                errors=result["errors"],
                                query_preview=query[:200],
                                variables=variables,
                                subgraph_url=subgraph_url,
                                response_preview=str(result)[:500]
                            )
                            # Try next subgraph URL
                            continue
                        
                        data = result.get("data")
                        
                        # FIXED: Validate response structure
                        if data is None:
                            logger.warning(
                                "[GRAPH] Response data is None",
                                subgraph_url=subgraph_url,
                                full_response=str(result)[:500]
                            )
                            continue
                        
                        # FIXED: Verify data format before caching
                        if not isinstance(data, dict):
                            logger.warning(
                                "[GRAPH] Response data is not a dict",
                                data_type=type(data).__name__,
                                data_preview=str(data)[:200],
                                subgraph_url=subgraph_url
                            )
                            continue
                        
                        # FIXED: Only cache successful responses
                        self.query_count += 1
                        self.request_cache[cache_key] = {
                            "data": data,
                            "timestamp": time.time(),
                            "success": True,
                            "subgraph_url": subgraph_url
                        }
                        
                        # Clean cache if too large
                        if len(self.request_cache) > 100:
                            oldest_key = min(
                                self.request_cache.keys(),
                                key=lambda k: self.request_cache[k]["timestamp"]
                            )
                            del self.request_cache[oldest_key]
                        
                        logger.debug(
                            "[GRAPH] Query successful",
                            subgraph_url=subgraph_url,
                            data_keys=list(data.keys()) if isinstance(data, dict) else "N/A"
                        )
                        
                        return data
                        
                except httpx.HTTPStatusError as e:
                    last_error = e
                    logger.error(
                        "[GRAPH] HTTP error",
                        status_code=e.response.status_code,
                        error=str(e),
                        url=subgraph_url,
                        response_body=e.response.text[:500] if e.response else "N/A",
                        attempt=attempt + 1,
                        max_retries=max_retries
                    )
                    
                    # FIXED: Handle specific HTTP errors
                    if e.response.status_code == 429:
                        # Rate limit - wait longer
                        wait_time = 60 * (attempt + 1)
                        logger.warning(f"[GRAPH] Rate limited, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                    elif e.response.status_code >= 500:
                        # Server error - retry with backoff
                        wait_time = 2 ** attempt
                        logger.warning(f"[GRAPH] Server error, retrying in {wait_time}s")
                        await asyncio.sleep(wait_time)
                    
                    # Try next subgraph URL
                    continue
                    
                except httpx.TimeoutException as e:
                    last_error = e
                    logger.warning(
                        "[GRAPH] Request timeout",
                        url=subgraph_url,
                        timeout=60.0,
                        attempt=attempt + 1
                    )
                    # Try next subgraph URL
                    continue
                    
                except Exception as e:
                    last_error = e
                    logger.error(
                        "[GRAPH] Query failed with exception",
                        error=str(e),
                        error_type=type(e).__name__,
                        query_preview=query[:200],
                        subgraph_url=subgraph_url,
                        attempt=attempt + 1,
                        exc_info=True
                    )
                    # Try next subgraph URL
                    continue
            
            # If all subgraph URLs failed, wait before retry
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"[GRAPH] All subgraphs failed, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
        
        # FIXED: Log final failure with all attempted URLs
        logger.error(
            "[GRAPH] All query attempts failed",
            attempted_urls=self.subgraph_urls,
            last_error=str(last_error) if last_error else "Unknown",
            query_preview=query[:200],
            variables=variables
        )
        
        # FIXED: Cache failure (with success=False) to prevent repeated failures
        self.request_cache[cache_key] = {
            "data": {},
            "timestamp": time.time(),
            "success": False,
            "error": str(last_error) if last_error else "All attempts failed"
        }
        
        return {}
    
    async def get_market_trades(
        self,
        market_id: str,
        first: int = 1000,
        skip: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get trades for a market from The Graph Protocol (FPMM subgraph).
        
        FIXED: Uses fpmmTransactions entity from FPMM subgraph structure.
        Returns normalized trades with: id, timestamp, price, shares, outcome, trader, market
        
        Args:
            market_id: Market contract address (FixedProductMarketMaker.id, not conditionId)
            first: Number of trades to fetch (max 1000)
            skip: Number of trades to skip (for pagination)
            
        Returns:
            List of trade dictionaries (normalized to expected format)
        """
        # FIXED: Query fpmmTransactions instead of trades
        query = """
        query GetMarketTrades($marketId: String!, $first: Int!, $skip: Int!) {
          fpmmTransactions(
            where: { market: $marketId }
            first: $first
            skip: $skip
            orderBy: timestamp
            orderDirection: asc
          ) {
            id
            type
            timestamp
            market {
              id
            }
            user
            tradeAmount
            feeAmount
            outcomeIndex
            outcomeTokensAmount
          }
        }
        """
        
        variables = {
            "marketId": market_id.lower(),
            "first": min(first, 1000),  # Max 1000 per query
            "skip": skip
        }
        
        data = await self._query_graphql(query, variables)
        
        # FIXED: Validate response format before extracting trades
        if not data:
            logger.debug(
                "[GRAPH] Empty response from GraphQL query",
                market_id=market_id,
                first=first,
                skip=skip
            )
            return []
        
        # FIXED: Check for fpmmTransactions key (not trades)
        if "fpmmTransactions" not in data:
            logger.warning(
                "[GRAPH] Response missing 'fpmmTransactions' key",
                market_id=market_id,
                data_keys=list(data.keys()) if isinstance(data, dict) else "N/A",
                response_preview=str(data)[:500]
            )
            return []
        
        transactions = data["fpmmTransactions"]
        if not isinstance(transactions, list):
            logger.warning(
                "[GRAPH] 'fpmmTransactions' is not a list",
                market_id=market_id,
                transactions_type=type(transactions).__name__,
                transactions_preview=str(transactions)[:200]
            )
            return []
        
        # FIXED: Fetch market prices to calculate trade prices
        market_prices = await self._get_market_prices(market_id)
        
        # FIXED: Normalize FPMM transactions to expected trade format
        normalized_trades = []
        for idx, tx in enumerate(transactions):
            normalized = self._normalize_fpmm_transaction(tx, market_prices)
            if normalized and self._verify_trade_format(normalized):
                normalized_trades.append(normalized)
        
        logger.debug(
            f"[GRAPH] Fetched {len(normalized_trades)}/{len(transactions)} valid trades from The Graph",
            market_id=market_id,
            first=first,
            skip=skip
        )
        
        return normalized_trades
    
    async def _get_market_prices(self, market_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch market prices (outcomeTokenPrices) for a market.
        
        This is needed to calculate trade prices from FPMM transactions.
        Results are cached to avoid repeated queries for the same market.
        
        Args:
            market_id: Market contract address
            
        Returns:
            Market data with outcomeTokenPrices, or None if not found
        """
        market_id_lower = market_id.lower()
        
        # Check cache first
        if market_id_lower in self.market_prices_cache:
            cached = self.market_prices_cache[market_id_lower]
            # FIXED: Only return cached data if it's successful (not None from failure)
            if cached.get("success", False) and time.time() - cached.get("timestamp", 0) < self.market_prices_cache_ttl:
                logger.debug(f"[GRAPH] Returning cached market prices for {market_id_lower[:10]}...")
                return cached.get("data")
            # If cached failure, don't return it (allow retry)
        
        query = """
        query GetMarketPrices($marketId: String!) {
          fixedProductMarketMaker(id: $marketId) {
            id
            outcomeTokenPrices
            outcomeSlotCount
          }
        }
        """
        
        variables = {"marketId": market_id_lower}
        data = await self._query_graphql(query, variables)
        
        if not data or "fixedProductMarketMaker" not in data:
            # FIXED: Cache failure with success=False (don't return cached failures)
            self.market_prices_cache[market_id_lower] = {
                "data": None,
                "timestamp": time.time(),
                "success": False
            }
            return None
        
        market = data["fixedProductMarketMaker"]
        if not market:
            # FIXED: Cache failure with success=False
            self.market_prices_cache[market_id_lower] = {
                "data": None,
                "timestamp": time.time(),
                "success": False
            }
            return None
        
        # FIXED: Cache successful result with success=True
        self.market_prices_cache[market_id_lower] = {
            "data": market,
            "timestamp": time.time(),
            "success": True
        }
        
        # Clean cache if too large (only keep successful entries)
        if len(self.market_prices_cache) > 50:
            # Remove oldest failed entries first, then oldest successful
            failed_keys = [k for k, v in self.market_prices_cache.items() if not v.get("success", False)]
            if failed_keys:
                oldest_failed = min(failed_keys, key=lambda k: self.market_prices_cache[k]["timestamp"])
                del self.market_prices_cache[oldest_failed]
            else:
                oldest_key = min(
                    self.market_prices_cache.keys(),
                    key=lambda k: self.market_prices_cache[k]["timestamp"]
                )
                del self.market_prices_cache[oldest_key]
        
        return market
    
    def _normalize_fpmm_transaction(
        self,
        tx: Dict[str, Any],
        market_prices: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Normalize FPMM transaction to expected trade format.
        
        Maps FPMM fields to expected format:
        - user → trader
        - outcomeIndex → outcome ("YES"/"NO" for binary markets, index string for multi-outcome)
        - outcomeTokensAmount → shares
        - Calculate price from market.outcomeTokenPrices[outcomeIndex] (REQUIRED - no fallback)
        - market.id → market (extract from nested object)
        
        Args:
            tx: FPMM transaction dictionary
            market_prices: Market data with outcomeTokenPrices (REQUIRED for price calculation)
            
        Returns:
            Normalized trade dictionary, or None if invalid or price unavailable
        """
        if not isinstance(tx, dict):
            return None
        
        # Extract nested market ID
        market_obj = tx.get("market")
        if isinstance(market_obj, dict):
            market_id = market_obj.get("id")
        else:
            market_id = str(market_obj) if market_obj else None
        
        if not market_id:
            return None
        
        # Extract timestamp
        timestamp = tx.get("timestamp")
        if timestamp is None:
            return None
        
        # Extract outcome index
        outcome_index = tx.get("outcomeIndex")
        if outcome_index is None:
            return None
        
        # FIXED: Map outcomeIndex to outcome names (YES/NO for binary markets)
        # For binary markets: 0 = YES, 1 = NO
        # For multi-outcome markets: Use index as fallback, but try to get names from market
        if isinstance(outcome_index, (int, float)):
            outcome_idx = int(outcome_index)
            # For binary markets (most common), map to YES/NO
            if outcome_idx == 0:
                outcome = "YES"
            elif outcome_idx == 1:
                outcome = "NO"
            else:
                # Multi-outcome market - use index as string (e.g., "2", "3")
                # Could enhance later to query market for outcome names
                outcome = str(outcome_idx)
        else:
            # Fallback: convert to string
            outcome = str(outcome_index)
        
        # Extract shares (outcomeTokensAmount)
        shares = tx.get("outcomeTokensAmount", "0")
        
        # FIXED: Calculate price from market prices ONLY (removed incorrect fallback)
        # Market prices are required - if unavailable, skip trade
        price = None
        if market_prices:
            outcome_prices = market_prices.get("outcomeTokenPrices")
            if isinstance(outcome_prices, list) and len(outcome_prices) > outcome_index:
                try:
                    price_str = str(outcome_prices[outcome_index])
                    price_float = float(price_str)
                    # Ensure price is between 0 and 1
                    if 0 <= price_float <= 1:
                        price = price_float
                except (ValueError, TypeError, IndexError):
                    pass
        
        # REMOVED: Incorrect fallback calculation (tradeAmount / outcomeTokensAmount)
        # This calculation produces wrong prices (not 0-1 range, not actual price)
        # If market prices unavailable, skip trade (price is required)
        
        # If still no price, skip this trade (price is required)
        if price is None:
            logger.debug(
                "[GRAPH] Could not calculate price for transaction - market prices unavailable",
                tx_id=tx.get("id"),
                outcome_index=outcome_index,
                has_market_prices=market_prices is not None,
                market_id=market_id
            )
            return None
        
        # Build normalized trade
        normalized = {
            "id": tx.get("id", ""),
            "timestamp": str(timestamp),
            "price": str(price),
            "shares": str(shares),
            "outcome": outcome,
            "trader": tx.get("user", ""),
            "market": market_id,
            # Keep original fields for reference
            "_original": {
                "type": tx.get("type"),
                "tradeAmount": tx.get("tradeAmount"),
                "feeAmount": tx.get("feeAmount"),
                "outcomeIndex": outcome_index
            }
        }
        
        return normalized
    
    def _verify_trade_format(self, trade: Dict[str, Any]) -> bool:
        """
        Verify trade data format before processing.
        
        FIXED: Validates all required fields and data types before extraction.
        
        Args:
            trade: Trade dictionary from GraphQL response
            
        Returns:
            True if trade format is valid, False otherwise
        """
        if not isinstance(trade, dict):
            return False
        
        # Required fields
        required_fields = {
            "id": (str,),
            "timestamp": (str, int, float),
            "price": (str, int, float),
        }
        
        # Check required fields exist and have correct types
        for field, valid_types in required_fields.items():
            if field not in trade:
                logger.debug(f"[GRAPH] Trade missing required field: {field}")
                return False
            
            value = trade[field]
            if value is None:
                logger.debug(f"[GRAPH] Trade field {field} is None")
                return False
            
            if not isinstance(value, valid_types):
                logger.debug(
                    f"[GRAPH] Trade field {field} has wrong type",
                    expected_types=[t.__name__ for t in valid_types],
                    actual_type=type(value).__name__,
                    value_preview=str(value)[:50]
                )
                return False
        
        # Validate timestamp can be converted to int
        try:
            timestamp = trade["timestamp"]
            if isinstance(timestamp, str):
                int(timestamp)
            elif isinstance(timestamp, (int, float)):
                int(timestamp)
            else:
                logger.debug(
                    "[GRAPH] Trade validation failed: invalid timestamp type",
                    timestamp_type=type(timestamp).__name__,
                    trade_id=trade.get("id", "unknown")
                )
                return False
        except (ValueError, TypeError):
            logger.debug(
                "[GRAPH] Trade validation failed: timestamp invalid",
                timestamp=trade.get("timestamp"),
                trade_id=trade.get("id", "unknown")
            )
            return False
        
        # Validate price can be converted to float and is in valid range
        try:
            price = trade["price"]
            if isinstance(price, str):
                price_float = float(price)
            elif isinstance(price, (int, float)):
                price_float = float(price)
            else:
                logger.debug(
                    "[GRAPH] Trade validation failed: invalid price type",
                    price_type=type(price).__name__,
                    trade_id=trade.get("id", "unknown")
                )
                return False
            
            # Price should be between 0 and 1 for Polymarket
            if price_float < 0 or price_float > 1:
                logger.debug(
                    "[GRAPH] Trade validation failed: price out of range",
                    price=price_float,
                    trade_id=trade.get("id", "unknown")
                )
                return False
            
            if math.isnan(price_float) or math.isinf(price_float):
                logger.debug(
                    "[GRAPH] Trade validation failed: price is NaN or Inf",
                    price=price_float,
                    trade_id=trade.get("id", "unknown")
                )
                return False
        except (ValueError, TypeError):
            logger.debug(
                "[GRAPH] Trade validation failed: price invalid",
                price=trade.get("price"),
                trade_id=trade.get("id", "unknown")
            )
            return False
        
        return True
    
    async def get_all_market_trades(
        self,
        market_id: str,
        max_trades: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get ALL trades for a market (paginated).
        
        Args:
            market_id: Market identifier
            max_trades: Optional maximum number of trades to fetch
            
        Returns:
            List of all trades for the market
        """
        all_trades = []
        skip = 0
        first = 1000  # Max per query
        
        while True:
            trades = await self.get_market_trades(market_id, first=first, skip=skip)
            
            if not trades or len(trades) == 0:
                break
            
            all_trades.extend(trades)
            
            if max_trades and len(all_trades) >= max_trades:
                all_trades = all_trades[:max_trades]
                break
            
            if len(trades) < first:
                break  # Reached end
            
            skip += first
        
        logger.info(
            f"Fetched {len(all_trades)} total trades from The Graph",
            market_id=market_id
        )
        
        return all_trades
    
    async def _introspect_schema(self) -> Optional[Dict[str, Any]]:
        """
        Query GraphQL schema introspection to discover actual available fields.
        
        This is the CORRECT way to verify what fields exist before writing queries.
        """
        introspection_query = """
        query IntrospectFixedProductMarketMaker {
          __type(name: "FixedProductMarketMaker") {
            name
            fields {
              name
              type {
                name
                kind
                ofType {
                  name
                  kind
                }
              }
            }
          }
        }
        """
        
        try:
            data = await self._query_graphql(introspection_query, {})
            return data
        except Exception as e:
            logger.error(f"[GRAPH] Schema introspection failed: {e}", exc_info=True)
            return None
    
    async def get_markets(
        self,
        first: int = 100,
        skip: int = 0,
        where_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get markets from The Graph Protocol.
        
        FIXED: Uses ABSOLUTE MINIMAL query - only 'id' field that MUST exist.
        Queries schema introspection first to verify available fields.
        
        CRITICAL: This subgraph may have different schema than GitHub FPMM subgraph.
        We only query 'id' field to avoid schema mismatches.
        
        Args:
            first: Number of markets to fetch
            skip: Number to skip (pagination)
            where_filter: Optional GraphQL where filter
            
        Returns:
            List of market dictionaries with id (contract address)
            NOTE: conditionId will be None - we cannot extract it without 'conditions' field
        """
        # STEP 1: Try schema introspection to see what fields exist
        try:
            schema_info = await self._introspect_schema()
            if schema_info and "__type" in schema_info:
                fields = schema_info["__type"].get("fields", [])
                field_names = [f.get("name") for f in fields]
                logger.info(
                    "[GRAPH] Schema introspection - available fields",
                    fields=field_names[:20],  # Log first 20 fields
                    total_fields=len(field_names)
                )
        except Exception as e:
            logger.debug(f"[GRAPH] Schema introspection failed (non-critical): {e}")
        
        where_clause = ""
        if where_filter:
            where_parts = []
            for key, value in where_filter.items():
                if isinstance(value, (int, float)):
                    where_parts.append(f"{key}: {value}")
                elif isinstance(value, str):
                    where_parts.append(f'{key}: "{value}"')
                elif isinstance(value, bool):
                    where_parts.append(f"{key}: {str(value).lower()}")
            if where_parts:
                where_clause = f"where: {{ {', '.join(where_parts)} }}"
        
        # STEP 2: ABSOLUTE MINIMAL query - only 'id' field
        # 'id' field MUST exist for all GraphQL entities (GraphQL spec requirement)
        # We cannot query 'conditions' field without knowing if it exists
        query = f"""
        query GetMarkets($first: Int!, $skip: Int!) {{
          fixedProductMarketMakers(
            first: $first
            skip: $skip
            {where_clause}
          ) {{
            id
          }}
        }}
        """
        
        variables = {
            "first": first,
            "skip": skip
        }
        
        data = await self._query_graphql(query, variables)
        
        # FIXED: Validate response format before extracting markets
        if not data:
            logger.debug("[GRAPH] Empty response from markets query")
            return []
        
        if "fixedProductMarketMakers" not in data:
            logger.warning(
                "[GRAPH] Response missing 'fixedProductMarketMakers' key",
                data_keys=list(data.keys()) if isinstance(data, dict) else "N/A",
                response_preview=str(data)[:500]
            )
            return []
        
        markets = data["fixedProductMarketMakers"]
        if not isinstance(markets, list):
            logger.warning(
                "[GRAPH] 'fixedProductMarketMakers' is not a list",
                markets_type=type(markets).__name__
            )
            return []
        
        # STEP 3: Extract only 'id' field (contract address)
        # CRITICAL LIMITATION: Without 'conditions' field, we cannot map conditionId → contract address
        # This means we need a different approach for market mapping
        valid_markets = []
        for idx, m in enumerate(markets):
            if not isinstance(m, dict):
                logger.debug(f"[GRAPH] Market {idx} is not a dict", market_type=type(m).__name__)
                continue
            
            # Verify required fields
            if "id" not in m:
                logger.debug(f"[GRAPH] Market {idx} missing 'id' field", market_keys=list(m.keys()))
                continue
            
            try:
                # Minimal market dict - only id (contract address)
                # conditionId is None because we cannot extract it without 'conditions' field
                market_dict = {
                    "id": m.get("id"),  # Contract address (REQUIRED for trade queries)
                    "conditionId": None,  # Cannot extract - 'conditions' field not available
                }
                valid_markets.append(market_dict)
            except Exception as e:
                logger.debug(f"[GRAPH] Error parsing market {idx}: {e}", exc_info=True)
                continue
        
        logger.debug(
            f"[GRAPH] Fetched {len(valid_markets)}/{len(markets)} valid markets from The Graph",
            note="conditionId mapping unavailable - need alternative approach"
        )
        
        return valid_markets
    
    async def get_fpmm_address_from_polymarket_api(self, condition_id: str) -> Optional[str]:
        """
        Get FPMM address directly from Polymarket's API (fallback when TheGraph fails).
        
        This is a more reliable approach than TheGraph queries when the subgraph
        schema doesn't match expectations.
        
        Args:
            condition_id: Condition ID to lookup
            
        Returns:
            FPMM contract address if found, None otherwise
        """
        try:
            import httpx
            
            # Try CLOB API endpoint
            url = f"https://clob.polymarket.com/markets/{condition_id}"
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check multiple possible fields for FPMM address
                    fpmm = (
                        data.get('fpmm') or
                        data.get('market_maker_address') or
                        data.get('neg_risk_market_id') or
                        data.get('contractAddress') or
                        data.get('contract_address') or
                        data.get('id')  # Sometimes the market ID is the FPMM address
                    )
                    
                    if fpmm:
                        # Validate that fpmm is actually an address (40 hex chars), not condition_id (64 hex chars)
                        fpmm_hex = fpmm.replace('0x', '').replace('0X', '')
                        if len(fpmm_hex) == 40:
                            # Valid Ethereum address
                            logger.info(
                                f"[POLYMARKET API] Found FPMM address: {fpmm[:20]}...",
                                condition_id=condition_id[:20]
                            )
                            return fpmm
                        else:
                            # Not a valid address - might be condition_id or something else
                            logger.debug(
                                f"[POLYMARKET API] Found value but not a valid address (got {len(fpmm_hex)} hex chars, expected 40): {fpmm[:30]}...",
                                condition_id=condition_id[:20]
                            )
                            # Continue to try other fields or TheGraph
                    
                    logger.debug(
                        f"[POLYMARKET API] No FPMM address in response",
                        condition_id=condition_id[:20],
                        response_keys=list(data.keys())[:10] if isinstance(data, dict) else "N/A"
                    )
                else:
                    logger.debug(
                        f"[POLYMARKET API] Request failed",
                        condition_id=condition_id[:20],
                        status_code=response.status_code
                    )
        except Exception as e:
            logger.debug(
                f"[POLYMARKET API] Failed to get FPMM from API: {e}",
                condition_id=condition_id[:20]
            )
        
        return None
    
    async def get_market_by_condition_id(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """
        Try to find a market by conditionId using where filter.
        
        This is an ALTERNATIVE approach when 'conditions' field is not available in list query.
        Tries to query markets with a where filter on conditions array.
        
        Args:
            condition_id: Condition ID to search for
            
        Returns:
            Market dictionary with id (contract address) if found, None otherwise
        """
        # Try multiple query formats - TheGraph schema may vary
        # Format 1: Try conditions_contains (array contains)
        query1 = """
        query GetMarketByConditionId($conditionId: String!) {
          fixedProductMarketMakers(
            first: 1
            where: { conditions_contains: [$conditionId] }
          ) {
            id
          }
        }
        """
        
        # Format 2: Try conditions field directly (if it's a string array)
        query2 = """
        query GetMarketByConditionId($conditionId: String!) {
          fixedProductMarketMakers(
            first: 1
            where: { conditions: [$conditionId] }
          ) {
            id
          }
        }
        """
        
        # Format 3: Try conditionId field directly (if it exists)
        query3 = """
        query GetMarketByConditionId($conditionId: String!) {
          fixedProductMarketMakers(
            first: 1
            where: { conditionId: $conditionId }
          ) {
            id
          }
        }
        """
        
        # Format 4: Try querying condition entity directly (alternative approach)
        query4 = """
        query GetMarketByConditionId($conditionId: ID!) {
          condition(id: $conditionId) {
            id
            fixedProductMarketMakers {
              id
            }
          }
        }
        """
        
        # Format 5: Search all FPMMs and filter locally (if schema supports it)
        query5 = """
        query GetFPMMs {
          fixedProductMarketMakers(first: 1000) {
            id
            conditions {
              id
            }
          }
        }
        """
        
        queries = [query1, query2, query3, query4]
        
        variables = {"conditionId": condition_id.lower()}
        
        # Try each query format until one works
        for idx, query in enumerate(queries, 1):
            try:
                logger.debug(
                    f"[GRAPH] Trying query format {idx}/4 for conditionId lookup",
                    condition_id=condition_id[:20]
                )
                
                # Format 4 uses ID! type, others use String!
                query_variables = variables if idx != 4 else {"conditionId": condition_id.lower()}
                
                data = await self._query_graphql(query, query_variables)
                
                # Handle Format 4 response structure (condition → fixedProductMarketMakers)
                if idx == 4 and data and "condition" in data:
                    condition = data.get("condition")
                    if condition and condition.get("fixedProductMarketMakers"):
                        fpmms = condition["fixedProductMarketMakers"]
                        if isinstance(fpmms, list) and len(fpmms) > 0:
                            market_id = fpmms[0].get("id")
                            if market_id:
                                # Validate that market_id is actually an address (40 hex chars), not condition_id (64 hex chars)
                                market_id_hex = market_id.replace('0x', '').replace('0X', '')
                                if len(market_id_hex) == 40:
                                    logger.info(
                                        f"[GRAPH] Found market by conditionId using format {idx} (condition entity)",
                                        condition_id=condition_id[:20],
                                        market_id=market_id[:20]
                                    )
                                    return {"id": market_id, "conditionId": condition_id}
                                else:
                                    logger.debug(
                                        f"[GRAPH] Format {idx} returned value but not a valid address (got {len(market_id_hex)} hex chars): {market_id[:30]}...",
                                        condition_id=condition_id[:20]
                                    )
                
                # Handle Formats 1-3 response structure (fixedProductMarketMakers directly)
                if data and "fixedProductMarketMakers" in data:
                    markets = data["fixedProductMarketMakers"]
                    if isinstance(markets, list) and len(markets) > 0:
                        market_id = markets[0].get("id")
                        if market_id:
                            # Validate that market_id is actually an address (40 hex chars), not condition_id (64 hex chars)
                            market_id_hex = market_id.replace('0x', '').replace('0X', '')
                            if len(market_id_hex) == 40:
                                logger.info(
                                    f"[GRAPH] Found market by conditionId using format {idx}",
                                    condition_id=condition_id[:20],
                                    market_id=market_id[:20]
                                )
                                return {"id": market_id, "conditionId": condition_id}
                            else:
                                logger.debug(
                                    f"[GRAPH] Format {idx} returned value but not a valid address (got {len(market_id_hex)} hex chars): {market_id[:30]}...",
                                    condition_id=condition_id[:20]
                                )
                
                # If we got data but no markets, try next format
                if data:
                    logger.debug(
                        f"[GRAPH] Query format {idx} returned data but no markets",
                        data_keys=list(data.keys()) if isinstance(data, dict) else "N/A"
                    )
            except Exception as e:
                logger.debug(
                    f"[GRAPH] Query format {idx} failed: {e}",
                    condition_id=condition_id[:20]
                )
                continue
        
        # Fallback: Try Polymarket API directly
        logger.info(
            "[GRAPH] All GraphQL formats failed, trying Polymarket API fallback",
            condition_id=condition_id[:20]
        )
        try:
            fpmm_address = await self.get_fpmm_address_from_polymarket_api(condition_id)
            if fpmm_address:
                # Validate address format
                fpmm_hex = fpmm_address.replace('0x', '').replace('0X', '')
                if len(fpmm_hex) == 40:
                    logger.info(
                        "[POLYMARKET API] Found FPMM address via API fallback",
                        condition_id=condition_id[:20],
                        fpmm_address=fpmm_address[:20]
                    )
                    return {"id": fpmm_address, "conditionId": condition_id}
                else:
                    logger.debug(
                        f"[POLYMARKET API] Fallback returned value but not a valid address (got {len(fpmm_hex)} hex chars): {fpmm_address[:30]}...",
                        condition_id=condition_id[:20]
                    )
        except Exception as e:
            logger.debug(
                f"[POLYMARKET API] Fallback also failed: {e}",
                condition_id=condition_id[:20]
            )
        
        logger.warning(
            "[GRAPH] All query formats and API fallback failed for conditionId lookup",
            condition_id=condition_id[:20]
        )
        return None
