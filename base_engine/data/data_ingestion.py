import asyncio
import json
import math
import traceback
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any, Tuple
from datetime import datetime, timedelta, timezone
from structlog import get_logger
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.data.database import Database
from base_engine.data.recovery_hierarchy import RecoveryHierarchy, RecoveryLevel
from base_engine.data.market_parser_v2 import MarketParserV2
from base_engine.exceptions import (
    DataIngestionError,
    MarketFetchError,
    PriceFetchError,
    DatabaseError,
    ValidationError
)
from config.settings import settings

# V2 CLEANUP: Blockchain/FPMM clients only imported if needed
# These are deprecated for Polymarket V2 (uses CLOB, not FPMM)
try:
    from base_engine.data.blockchain_client import BlockchainClient
    from base_engine.data.thegraph_client import TheGraphClient
    BLOCKCHAIN_AVAILABLE = True
except ImportError:
    BlockchainClient = None
    TheGraphClient = None
    BLOCKCHAIN_AVAILABLE = False

logger = get_logger()

# Category inference keywords — used when Polymarket API returns no category.
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "crypto": ["bitcoin","btc","eth","ethereum","solana","sol","crypto","blockchain",
               "token","coin","defi","nft"],
    # CRITICAL: esports MUST come before sports — dict iteration is insertion-order.
    # "league", "championship" in sports would match "League of Legends Championship" first.
    "esports": ["esports","league of legends","lol ","cs2","counter-strike","csgo","dota",
                "valorant","overwatch","rainbow six","call of duty","worlds","msi",
                "lck","lec","lpl","lcs","blast premier","esl ","pgl ","iem ","vct"],
    "sports": ["nfl","nba","mlb","nhl","fifa","soccer","football","basketball","baseball",
               "hockey","tennis","ufc","mma","golf","f1","racing","league","championship",
               "playoffs","world cup","super bowl","bundesliga","la liga","serie a",
               "premier league","ligue 1","eredivisie"],
    "politics": ["trump","biden","election","president","congress","senate","democrat",
                 "republican","vote","campaign","governor","minister","parliament","referendum"],
    "weather": ["temperature","rain","snow","hurricane","storm","degrees","fahrenheit",
                "celsius","precipitation","forecast","drought"],
    "finance": ["stock","s&p","nasdaq","dow","fed","interest rate","inflation","gdp",
                "cpi","earnings","ipo","bond"],
    "science": ["nasa","spacex","asteroid","comet","launch","rocket","scientific",
                "discovery","research"],
    "entertainment": ["oscar","grammy","emmy","award","movie","album","tv show",
                      "music","artist","actor"],
    "geopolitical": ["war","invasion","ceasefire","sanctions","nato","un ","united nations",
                     "treaty","conflict","troops"],
}


def _infer_category(question: str) -> str:
    """Infer market category from question text using keyword matching."""
    q = question.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    return "unknown"


def _naive_utc_ts(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize datetime to naive UTC for PostgreSQL TIMESTAMP WITHOUT TIME ZONE / asyncpg."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _safe_log_str(s: str) -> str:
    """Sanitize for console logging on Windows (cp1252); avoids UnicodeEncodeError from non-ASCII chars."""
    if not s:
        return ""
    return (s.encode("ascii", "replace").decode("ascii"))


async def _fetch_price_history_chunked(
    client: PolymarketClient,
    token_id: str,
    from_ts: int,
    to_ts: int,
    interval: str = "1h",
    days_per_request: int = 30,
    delay_seconds: float = 0.2,
    max_retries: int = 3,
) -> List[Dict[str, Any]]:
    """Fetch full price history. Tries interval=max first (single call), then chunked with retries."""
    # Try interval=max first (single API call, no startTs/endTs) - Polymarket returns all available history
    try_max_first = getattr(settings, "PRICE_HISTORY_TRY_MAX_FIRST", True)
    if try_max_first:
        try:
            resp = await client.get_price_history(token_id=token_id, interval="max")
            if resp and isinstance(resp, dict):
                history = resp.get("history") or []
                if isinstance(history, list) and len(history) > 0:
                    # Filter to requested range
                    filtered = [p for p in history if isinstance(p, dict) and from_ts <= p.get("t", 0) <= to_ts]
                    if filtered:
                        logger.debug(f"interval=max returned {len(filtered)} points for token {token_id}")
                        return filtered
        except Exception as e:
            logger.debug(f"interval=max failed for token {token_id}, falling back to chunked: {e}")
        await asyncio.sleep(delay_seconds)

    # Chunked fetch with retries
    out: List[Dict[str, Any]] = []
    window_seconds = days_per_request * 24 * 60 * 60
    w_start = from_ts
    while w_start < to_ts:
        w_end = min(w_start + window_seconds, to_ts)
        last_err = None
        for attempt in range(max_retries):
            try:
                resp = await client.get_price_history(
                    token_id=token_id,
                    start_ts=w_start,
                    end_ts=w_end,
                    interval=interval,
                )
                if resp and isinstance(resp, dict):
                    history = resp.get("history") or []
                    if isinstance(history, list):
                        out.extend(history)
                break
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    backoff = 1.0 * (2 ** attempt)
                    logger.debug(f"Chunk retry {attempt + 1}/{max_retries} for token {token_id}: {e}, backoff {backoff}s")
                    await asyncio.sleep(backoff)
                else:
                    logger.debug(f"Chunk fetch failed for token {token_id} [{w_start}-{w_end}]: {e}")
        await asyncio.sleep(delay_seconds)
        w_start = w_end
    return out


async def _fill_price_gaps_secondary(
    market_id: str,
    token_id: str,
    from_ts: int,
    to_ts: int,
    clob_point_count: int,
    min_expected_ratio: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Optional: fill price history when CLOB returns sparse data.
    Returns list of {t, p}. Currently no-op when USE_THEGRAPH_QUERIES=False or no CLOB subgraph.
    To enable: set USE_THEGRAPH_QUERIES=True and implement CLOB subgraph in thegraph_client.
    See RUNBOOK.md for 99% price coverage checklist.
    """
    window_points_approx = max(1, (to_ts - from_ts) // 3600)  # 1h bins
    min_expected = int(window_points_approx * min_expected_ratio)
    if clob_point_count >= min_expected:
        return []
    if not getattr(settings, "USE_THEGRAPH_QUERIES", False) or not BLOCKCHAIN_AVAILABLE or TheGraphClient is None:
        return []
    # Future: call The Graph or blockchain indexer for CLOB trades/prices when available
    logger.debug(f"Sparse CLOB history for market {market_id} token {token_id} ({clob_point_count} < {min_expected}); secondary source not configured for CLOB")
    return []


class DataIngestionService:
    """
    Data ingestion service for Polymarket markets, users, and price data.
    
    CRITICAL FIX: Removed broken background thread approach.
    Root cause: Background thread with event loop was fundamentally flawed:
    - loop.run_until_complete(asyncio.sleep(1)) doesn't actually run tasks
    - Tasks were created but never executed
    - Status showed "Running" but nothing happened
    - Manual ingestion bypassed background thread anyway
    
    New approach: Simple, reliable manual ingestion only.
    - Manual ingestion runs synchronously when button clicked
    - No background threads, no complexity
    - Status reflects actual progress, not just flags
    - Future: Can add automated ingestion as separate, simpler feature if needed
    """
    def __init__(
        self,
        client: PolymarketClient,
        db: Optional[Database],
        *,
        blockchain_client: Optional[Any] = None,
        thegraph_client: Optional[Any] = None,
        smart_fetcher: Optional[Any] = None,
    ) -> None:
        self.client = client
        self.db = db
        self.smart_fetcher = smart_fetcher
        # Allow tests to inject mocks; otherwise init only if USE_BLOCKCHAIN_PRICES=True
        if blockchain_client is not None and thegraph_client is not None:
            self.blockchain_client = blockchain_client
            self.thegraph_client = thegraph_client
        else:
            self.blockchain_client = None
            self.thegraph_client = None
            use_blockchain = getattr(settings, 'USE_BLOCKCHAIN_PRICES', False)
            if use_blockchain and BLOCKCHAIN_AVAILABLE:
                self.blockchain_client = BlockchainClient()
                self.thegraph_client = TheGraphClient()
                logger.info("Blockchain/FPMM clients initialized (USE_BLOCKCHAIN_PRICES=True)")
            elif use_blockchain and not BLOCKCHAIN_AVAILABLE:
                logger.warning("USE_BLOCKCHAIN_PRICES=True but blockchain clients not available (missing dependencies)")
        self.running = False  # Flag for future automated ingestion (not currently used)
        self.last_market_update = None
        self.last_user_update = None
        self.recovery = RecoveryHierarchy(max_retries=3, retry_delay=1.0)
        # M6 FIX: Track failed batch market items for checkpoint retry on next cycle.
        # Stores List[List[Dict]] — one sub-list per failed batch. Cleared on full success.
        self._failed_ingestion_batches: List[List[Dict]] = []
        self.ingestion_progress = {
            "current": 0,
            "total": 0,
            "status": "idle",
            "current_batch": 0,
            "total_batches": 0,
            "api_fetched": 0,
            "db_saved": 0,
            "recovery_level": None,
            "error_message": None,
            "error_info": None
        }
        self.cached_markets: List[Dict] = []
        self.cached_markets_timestamp: Optional[datetime] = None

    async def _bulk_insert_prices_safe(self, prices: List[Dict[str, Any]]) -> int:
        """Try raw bulk INSERT (ON CONFLICT DO NOTHING) first; fall back to merge loop if constraint missing."""
        if not prices or not self.db or not self.db.session_factory:
            return 0
        try:
            return await self.db.bulk_insert_prices_raw(prices)
        except Exception as e:
            if "uq_market_prices_market_token_timestamp" in str(e) or "unique" in str(e).lower() or "does not exist" in str(e).lower():
                logger.info("Using merge fallback for prices (run schema/add_market_prices_unique_constraint.sql for faster inserts): %s", e)
            # FIX NEW-7: bulk_insert_prices now returns actual success count instead of None
            return await self.db.bulk_insert_prices(prices)
    
    async def start(self) -> None:
        """
        Start data ingestion service.
        
        SIMPLIFIED: Just sets running flag. No background threads.
        Manual ingestion is triggered by UI button, not by this method.
        """
        if self.running:
            logger.warning("Data ingestion service already running")
            return
        
        self.running = True
        logger.info("Data ingestion service started (manual ingestion mode)")
    
    async def stop(self) -> None:
        """
        Stop data ingestion service.
        
        SIMPLIFIED: Just clears running flag. No thread management needed.
        """
        if not self.running:
            return
        
        self.running = False
        logger.info("Data ingestion service stopped")

    async def run_resolution_backfill(self, *, log_progress: bool = True, **kwargs) -> Dict[str, Any]:
        """Run resolution backfill (fetch missing markets, backfill resolution, update prediction_log).

        Optional kwargs:
            performance_tracker: PerformanceTracker instance for scoring resolved paper trades.
            learning_engine: LearningEngine instance for online learning from resolved trades.
        """
        if not self.db or not self.client:
            return {"inserted": 0, "updated": 0, "prediction_log_updated": 0, "error": "DB or client not available"}
        from base_engine.data.resolution_backfill import run_resolution_backfill
        started_at = datetime.now(timezone.utc)
        result = await run_resolution_backfill(
            self.db,
            self.client,
            missing_limit=500,
            resolution_limit=500,
            log_progress=log_progress,
            **kwargs,
        )
        # Write sync_log so health_runner._check_resolution_backfill() can confirm runs occurred
        if not result.get("error"):
            await self.db.insert_sync_log(
                sync_type="resolution_backfill",
                component="resolution_backfill",
                status="success",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                records_processed=(result.get("updated", 0) + result.get("inserted", 0)),
                records_inserted=result.get("inserted", 0),
            )
        return result
    
    # REMOVED: Unused background ingestion loops
    # These methods were part of a broken background thread approach that has been removed.
    # Manual ingestion is now the only supported method (triggered by UI button).
    # If automated ingestion is needed in the future, it should be implemented as a simpler,
    # more reliable approach (e.g., using asyncio tasks instead of background threads).
    
    # REMOVED: Unused background ingestion loops
    # These methods were part of a broken background thread approach that has been removed.
    # Manual ingestion is now the only supported method (triggered by UI button).
    # If automated ingestion is needed in the future, it should be implemented as a simpler,
    # more reliable approach (e.g., using asyncio tasks instead of background threads).
    
    # Removed methods:
    # - _ingest_markets_loop()
    # - _ingest_top_users_loop()
    # - _ingest_elite_trader_activity_loop()
    # - _ingest_market_prices_loop()
    
    def _validate_market_data(self, markets: Any) -> bool:  # type: ignore[type-arg]
        """
        Validate market data structure.
        Ensures markets is a non-empty list of dicts with required 'id' field.
        More lenient: allows some markets to be missing 'id' if most have it.
        """
        if not markets or not isinstance(markets, list):
            logger.debug("Validation failed: markets is not a list or is None")
            return False
        if len(markets) == 0:
            logger.debug("Validation failed: markets list is empty")
            return False
        
        # Count valid markets (with 'id' field)
        valid_count = 0
        invalid_markets = []
        
        for idx, market in enumerate(markets):
            if not isinstance(market, dict):
                invalid_markets.append(f"Index {idx}: not a dict")
                continue
            raw_id = market.get("id")
            if raw_id is None:
                invalid_markets.append(f"Index {idx}: missing 'id' field")
                continue
            if not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
                invalid_markets.append(f"Index {idx}: invalid 'id' value: {raw_id}")
                continue
            valid_count += 1
        
        # Allow if at least 80% of markets are valid (lenient validation)
        if not markets or valid_count == 0:
            logger.warning(f"Validation failed: No valid markets found. Invalid samples: {invalid_markets[:5]}")
            return False

        validity_ratio = valid_count / len(markets)
        if validity_ratio < 0.8:
            logger.warning(
                f"Validation failed: Only {valid_count}/{len(markets)} markets valid ({validity_ratio:.1%}). "
                f"Invalid samples: {invalid_markets[:5]}"
            )
            return False
        
        if invalid_markets:
            logger.debug(f"Validation passed: {valid_count}/{len(markets)} markets valid. Some invalid: {invalid_markets[:3]}")
        
        return True
    
    async def ingest_all_markets(
        self,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        top_markets_count: int = 1000,
        num_processors: int = 50,
        include_closed: bool = False,
    ) -> int:
        try:
            num_processors = max(1, int(num_processors)) if isinstance(num_processors, (int, float)) else 50
        except (OverflowError, ValueError, TypeError):
            num_processors = 50
        try:
            top_markets_count = max(1, int(top_markets_count)) if isinstance(top_markets_count, (int, float)) else 1000
        except (OverflowError, ValueError, TypeError):
            top_markets_count = 1000
        logger.info(f"Ingesting top {top_markets_count} markets from Polymarket API using {num_processors} parallel processors")
        started_at = datetime.now(timezone.utc)

        # Reset error status when starting new ingestion
        self.ingestion_progress = {
            "current": 0,
            "total": top_markets_count,
            "status": "starting",
            "current_batch": 0,
            "total_batches": 0,
            "api_fetched": 0,
            "db_saved": 0,
            "recovery_level": None,
            "error_info": None,
            "error_message": None
        }
        
        if progress_callback:
            try:
                progress_callback(self.ingestion_progress)
            except Exception as e:
                logger.warning(f"Progress callback error: {str(e)}")

        # M6 FIX: Checkpoint retry — if previous cycle left failed batches, process only those
        # instead of restarting from scratch. Clears the checkpoint before running so a second
        # failure on the same data triggers a fresh full ingest on the cycle after next.
        if self._failed_ingestion_batches:
            _retry_batches = self._failed_ingestion_batches
            self._failed_ingestion_batches = []
            logger.info(
                "M6: Retrying %d failed batch(es) from previous ingestion cycle "
                "(%d total markets).",
                len(_retry_batches),
                sum(len(b) for b in _retry_batches),
            )
            _retry_semaphore = asyncio.Semaphore(1)
            _retry_api_total = 0
            _retry_db_total = 0

            async def _retry_one_batch(b_idx: int, b_markets: List[Dict]) -> tuple[int, int]:
                nonlocal _retry_api_total, _retry_db_total
                async with _retry_semaphore:
                    try:
                        market_data = []
                        for _m in b_markets:
                            if not isinstance(_m, dict):
                                continue
                            try:
                                processed = self._process_market(_m) if hasattr(self, "_process_market") else _m
                                if processed:
                                    market_data.append(processed)
                            except Exception:
                                pass
                        if not market_data:
                            return 0, 0
                        db_count = 0
                        if self.db and self.db.session_factory:
                            try:
                                db_count = await self.db.bulk_insert_markets(market_data)
                            except Exception as _dbe:
                                logger.warning("M6: Retry batch %d DB save failed: %s", b_idx, _dbe)
                                # Re-add to failed list for next cycle
                                self._failed_ingestion_batches.append(b_markets)
                                return 0, 0
                        _retry_api_total += len(market_data)
                        _retry_db_total += db_count
                        return len(market_data), db_count
                    except Exception as _e:
                        logger.warning("M6: Retry batch %d error: %s", b_idx, _e)
                        self._failed_ingestion_batches.append(b_markets)
                        return 0, 0

            _retry_tasks = [_retry_one_batch(i, b) for i, b in enumerate(_retry_batches)]
            await asyncio.gather(*_retry_tasks, return_exceptions=True)
            logger.info(
                "M6: Retry complete — %d markets processed, %d saved to DB.",
                _retry_api_total, _retry_db_total,
            )
            # Continue with normal full ingestion after retry (don't short-circuit)

        last_progress_update = 0.0
        progress_update_interval = 0.5
        
        def throttled_progress_callback(progress_dict: Dict):
            nonlocal last_progress_update
            import time
            now = time.time()
            if now - last_progress_update >= progress_update_interval:
                last_progress_update = now
                if progress_callback:
                    try:
                        progress_callback(progress_dict)
                    except Exception as e:
                        logger.warning(f"Progress callback error: {str(e)}")
        
        async def fetch_markets_batch(offset_val: int, limit_val: int, active: bool = True) -> List[Dict]:
            # BUG FIX: Added explicit None check at function start
            if not self.client:
                error_msg = "CRITICAL: Polymarket client is None - client not initialized"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            # Events API has better clobTokenIds coverage than /markets - use for both active and closed
            use_events = hasattr(self.client, "get_events")
            logger.debug(f"fetch_markets_batch: active={active}, offset={offset_val}, limit={limit_val}, use_events={use_events}")
            try:
                result = None
                if use_events:
                    result = await self.client.get_events(active=active, limit=min(limit_val, 100), offset=offset_val)
                    if not result or (isinstance(result, list) and len(result) == 0):
                        logger.info("get_events returned empty, falling back to get_markets")
                        result = await self.client.get_markets(active=active, limit=limit_val, offset=offset_val)
                if result is None:
                    result = await self.client.get_markets(active=active, limit=limit_val, offset=offset_val)
                logger.info(f"API returned: type={type(result).__name__}, length={len(result) if isinstance(result, list) else 'N/A'}")
                if isinstance(result, list) and len(result) > 0:
                    logger.info(f"First market sample keys: {list(result[0].keys())[:10] if isinstance(result[0], dict) else 'N/A'}")
                
                if result is None:
                    logger.warning(f"API returned None for offset {offset_val} - treating as empty")
                    return []
                
                if isinstance(result, list):
                    if len(result) == 0:
                        logger.warning(f"API returned empty list for offset {offset_val}, limit {limit_val}")
                    else:
                        logger.info(f"Successfully fetched {len(result)} markets for offset {offset_val}")
                    return result
                
                if isinstance(result, dict):
                    if "error" in result or "message" in result:
                        error_msg = result.get("error") or result.get("message", "Unknown error")
                        logger.error(f"API returned error dict: {error_msg}")
                        raise RuntimeError(f"Polymarket API error: {error_msg}")
                    
                    if "data" in result and isinstance(result["data"], list):
                        markets_list = result["data"]
                        logger.info(f"Found {len(markets_list)} markets in 'data' key for offset {offset_val}")
                        return markets_list
                    elif "markets" in result and isinstance(result["markets"], list):
                        markets_list = result["markets"]
                        logger.info(f"Found {len(markets_list)} markets in 'markets' key for offset {offset_val}")
                        return markets_list
                    elif "results" in result and isinstance(result["results"], list):
                        markets_list = result["results"]
                        logger.info(f"Found {len(markets_list)} markets in 'results' key for offset {offset_val}")
                        return markets_list
                    elif "items" in result and isinstance(result["items"], list):
                        markets_list = result["items"]
                        logger.info(f"Found {len(markets_list)} markets in 'items' key for offset {offset_val}")
                        return markets_list
                    else:
                        logger.warning(
                            "Unexpected API response dict structure",
                            offset=offset_val,
                            keys=list(result.keys())[:10],
                            result_type=type(result).__name__
                        )
                        if len(result) == 0:
                            return []
                        logger.warning(f"Attempting to extract list from dict values...")
                        for key, value in result.items():
                            if isinstance(value, list):
                                logger.info(f"Found list in key '{key}', using it (length: {len(value)})")
                                return value
                        raise RuntimeError(f"Could not extract market list from API response dict. Keys: {list(result.keys())[:10]}")
                
                raise RuntimeError(f"Unexpected API response type: {type(result).__name__}")
                
            except RuntimeError:
                raise
            except Exception as e:
                resp = getattr(e, "response", None)
                error_details = {
                    "offset": offset_val,
                    "limit": limit_val,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "status": getattr(resp, "status_code", None),
                    "body_preview": (getattr(resp, "text", None) or "")[:300],
                }
                logger.error("Polymarket Gamma API call failed", **error_details, exc_info=True)
                # Use custom exception with context
                raise MarketFetchError(
                    f"API call failed at offset {offset_val}: {str(e)}",
                    api_endpoint=self.client.gamma_api if self.client else None,
                    status_code=error_details['status'],
                    offset=offset_val,
                    limit=limit_val
                ) from e
        
        async def save_to_db(markets_data: List[Dict]) -> int:
            if not markets_data:
                logger.debug("save_to_db called with empty markets_data")
                return 0
            # Fix: guard with not self.db or self.db.session_factory before saving
            if not self.db or self.db.session_factory is None:
                if not self.db:
                    error_msg = "CRITICAL: Database object not initialized. Cannot save markets to database."
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                error_msg = (
                    "CRITICAL: Database connection not established. "
                    "Database session_factory is None. "
                    "Check if database is reachable and .env DATABASE_URL is correct. "
                    "Markets cannot be saved without database connection."
                )
                logger.error(error_msg)
                logger.error("Database may not have been initialized properly during BaseEngine.init()")
                raise DatabaseError(
                    error_msg,
                    operation="save_markets",
                    table="markets",
                    record_count=len(markets_data) if markets_data else 0,
                    db_path=getattr(self.db, 'db_path', None) if self.db else None
                )
            
            if getattr(settings, "SKIP_RERESOLVED_MARKETS", True):
                try:
                    resolved_ids = await self.db.get_resolved_market_ids()
                    if resolved_ids:
                        before = len(markets_data)
                        markets_data = [m for m in markets_data if str(m.get("id") or "").strip() not in resolved_ids]
                        if before != len(markets_data):
                            logger.debug("M3: skipped %s already-resolved markets", before - len(markets_data))
                except Exception as e:
                    logger.debug("SKIP_RERESOLVED_MARKETS filter failed: %s", e)
            if not markets_data:
                logger.debug("save_to_db: no markets to save after filter")
                return 0
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"save_to_db: Attempting to save {len(markets_data)} markets to database")
                    await self.db.bulk_insert_markets(markets_data)
                    logger.info(f"Successfully saved {len(markets_data)} markets to database")
                    return len(markets_data)
                except Exception as e:
                    is_deadlock = (
                        "DeadlockDetectedError" in type(e).__name__
                        or "deadlock detected" in str(e).lower()
                    )
                    if is_deadlock and attempt < max_retries - 1:
                        delay = 0.2 * (attempt + 1)
                        logger.warning(
                            "Deadlock on save_markets, retrying",
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_sec=delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        error_msg = f"CRITICAL: Database save failed for {len(markets_data)} markets: {str(e)}"
                        logger.error(error_msg, exc_info=True)
                        import traceback
                        logger.error(f"Full traceback: {traceback.format_exc()}")
                        raise DatabaseError(
                            error_msg,
                            operation="save_markets",
                            table="markets",
                            record_count=len(markets_data) if markets_data else 0,
                            original_error=str(e),
                            error_type=type(e).__name__
                        ) from e
        
        try:
            ok, error_msg = await self._pre_ingestion_checks()
            if not ok:
                logger.error(error_msg)
                self.ingestion_progress["status"] = "error"
                self.ingestion_progress["error_message"] = error_msg
                self.ingestion_progress["error_info"] = error_msg or "Pre-ingestion checks failed."
                throttled_progress_callback(self.ingestion_progress)
                return 0

            # CRITICAL FIX: Only set status to "ingesting" AFTER all checks pass
            # Root cause: Status was set before verification, causing misleading UI state
            # Impact: Users see "Ingesting" but no progress because prerequisites failed
            # Fix: Set status only after successful verification
            self.ingestion_progress["status"] = "ingesting"
            self.ingestion_progress["current"] = 0
            self.ingestion_progress["api_fetched"] = 0
            self.ingestion_progress["db_saved"] = 0
            throttled_progress_callback(self.ingestion_progress)
            
            logger.info("Starting market fetch - fetching markets to identify top markets by liquidity/volume...")
            all_markets = []
            offset = 0
            fetch_limit = 200
            fetch_batch_num = 0
            max_fetch_batches = 50
            batch_delay_seconds = 0.1
            consecutive_empty_batches = 0
            max_consecutive_empty = 5
            
            # REBUILD: Update progress immediately when starting fetch
            self.ingestion_progress["current_batch"] = 0
            throttled_progress_callback(self.ingestion_progress)
            
            while len(all_markets) < top_markets_count * 2 and fetch_batch_num < max_fetch_batches:
                fetch_batch_num += 1
                logger.info(f"Fetching batch {fetch_batch_num} at offset {offset} (limit: {fetch_limit})...")
                
                # REBUILD: Update progress for each batch attempt
                self.ingestion_progress["current_batch"] = fetch_batch_num
                throttled_progress_callback(self.ingestion_progress)
                
                try:
                    # REBUILD: Simplified direct API call - no complex recovery mechanism for initial fetch
                    # Root cause: Recovery mechanism might be blocking or failing silently
                    # Impact: Ingestion gets stuck, no progress updates
                    # Fix: Use direct API call first, add recovery only if needed
                    current_offset = offset
                    current_limit = fetch_limit
                    
                    logger.info(f"Making direct API call for batch {fetch_batch_num} (active)...")
                    markets = await fetch_markets_batch(current_offset, current_limit, active=True)
                    logger.info(f"Direct API call returned: type={type(markets).__name__}, length={len(markets) if isinstance(markets, list) else 'N/A'}")
                    
                    # If direct call fails, try recovery mechanism
                    if not markets or (isinstance(markets, list) and len(markets) == 0):
                        logger.info(f"Direct call returned empty, trying recovery mechanism for batch {fetch_batch_num}...")
                        try:
                            markets = await self.recovery.execute_with_recovery(
                                operation_name=f"fetch_markets_discovery_{fetch_batch_num}",
                                primary_operation=lambda: fetch_markets_batch(current_offset, current_limit),
                                is_critical=True,
                                validation_fn=self._validate_market_data
                            )
                        except RuntimeError as recovery_error:
                            # RuntimeErrors from recovery mechanism - log and continue
                            logger.warning(f"Recovery mechanism failed for batch {fetch_batch_num}: {recovery_error}")
                            markets = None
                    
                    # Process the results
                    if markets is None:
                        consecutive_empty_batches += 1
                        logger.warning(
                            f"API returned None for batch {fetch_batch_num} at offset {offset} (consecutive failures: {consecutive_empty_batches})"
                        )
                        if consecutive_empty_batches >= max_consecutive_empty:
                            error_msg = f"API returned None after {consecutive_empty_batches} consecutive attempts"
                            logger.error(error_msg)
                            self.ingestion_progress["status"] = "error"
                            self.ingestion_progress["error_message"] = error_msg
                            self.ingestion_progress["error_info"] = (
                                "Polymarket API returned None (null) for multiple batches. "
                                "This indicates an API issue or connectivity problem."
                            )
                            throttled_progress_callback(self.ingestion_progress)
                            return 0
                        offset += fetch_limit
                        if fetch_batch_num < max_fetch_batches:
                            await asyncio.sleep(batch_delay_seconds)
                        continue
                    
                    if not isinstance(markets, list):
                        error_msg = f"API returned invalid type {type(markets).__name__} instead of list for batch {fetch_batch_num}"
                        logger.error(error_msg)
                        self.ingestion_progress["status"] = "error"
                        self.ingestion_progress["error_message"] = error_msg
                        self.ingestion_progress["error_info"] = f"Expected list, got {type(markets).__name__}"
                        throttled_progress_callback(self.ingestion_progress)
                        return 0
                    
                    if len(markets) == 0:
                        consecutive_empty_batches += 1
                        logger.warning(
                            f"Empty batch {fetch_batch_num} at offset {offset} (consecutive empty: {consecutive_empty_batches})"
                        )
                        if consecutive_empty_batches >= max_consecutive_empty:
                            logger.warning(f"Received {consecutive_empty_batches} consecutive empty batches, stopping fetch")
                            break
                        offset += fetch_limit
                        if fetch_batch_num < max_fetch_batches:
                            await asyncio.sleep(batch_delay_seconds)
                        continue
                    
                    # Success - we got markets!
                    consecutive_empty_batches = 0
                    all_markets.extend(markets)
                    logger.info("Successfully fetched batch %s: %s markets (total so far: %s)", fetch_batch_num, len(markets), len(all_markets))
                    
                    # REBUILD: Update progress after successful fetch
                    self.ingestion_progress["api_fetched"] = len(all_markets)
                    self.ingestion_progress["current"] = len(all_markets)
                    throttled_progress_callback(self.ingestion_progress)
                    
                    offset += fetch_limit
                    
                    if len(markets) < fetch_limit:
                        logger.info(f"Received fewer markets than requested ({len(markets)} < {fetch_limit}), stopping fetch")
                        break
                    
                    if fetch_batch_num < max_fetch_batches:
                        await asyncio.sleep(batch_delay_seconds)
                except Exception as e:
                    error_str = str(e)
                    logger.error(
                        "Error fetching discovery batch",
                        batch_num=fetch_batch_num,
                        offset=offset,
                        error=_safe_log_str(error_str),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
                    import traceback
                    logger.error(_safe_log_str(traceback.format_exc()))
                    consecutive_empty_batches += 1
                    if consecutive_empty_batches >= max_consecutive_empty:
                        logger.error(f"Too many consecutive failures ({consecutive_empty_batches}), stopping fetch")
                        break
                    await asyncio.sleep(batch_delay_seconds * 2)
            
            if not all_markets:
                error_msg = "No markets fetched from API - ingestion failed"
                logger.error(error_msg)
                logger.error("Possible causes:")
                logger.error("- Polymarket API is unreachable or blocked")
                logger.error("- Network connectivity issues")
                logger.error("- API authentication/rate limiting issues")
                logger.error("- Check logs above for specific API call failures")
                logger.error(f"Total batches attempted: {fetch_batch_num}, Consecutive failures: {consecutive_empty_batches}")
                logger.error(f"API endpoint used: {self.client.gamma_api if self.client else 'N/A'}")
                logger.error(f"Last API call parameters: offset={offset}, limit={fetch_limit}")
                self.ingestion_progress["status"] = "error"
                self.ingestion_progress["error_message"] = error_msg
                self.ingestion_progress["error_info"] = (
                    f"No markets were fetched from the Polymarket API after {fetch_batch_num} batch attempts. "
                    f"Consecutive failures: {consecutive_empty_batches}. "
                    f"API endpoint: {self.client.gamma_api if self.client else 'N/A'}. "
                    "Check: 1) Network connection, 2) API accessibility, 3) Logs for specific errors, 4) VPS IP geo-access"
                )
                throttled_progress_callback(self.ingestion_progress)
                return 0
            
            # Optionally fetch closed markets and merge (dedupe by id; active already in all_markets)
            if include_closed:
                closed_offset = 0
                closed_batch_num = 0
                existing_ids = {str(m.get("id") or m.get("market_id", "")).strip() for m in all_markets if m}
                max_closed_batches = 30
                while closed_batch_num < max_closed_batches:
                    closed_batch_num += 1
                    try:
                        closed_batch = await fetch_markets_batch(closed_offset, fetch_limit, active=False)
                        if not closed_batch:
                            break
                        added = 0
                        for m in closed_batch:
                            mid = str(m.get("id") or m.get("market_id", "")).strip()
                            if mid and mid not in existing_ids:
                                existing_ids.add(mid)
                                all_markets.append(m)
                                added += 1
                        logger.info(f"Closed batch {closed_batch_num}: got {len(closed_batch)}, added {added}, total markets {len(all_markets)}")
                        if len(closed_batch) < fetch_limit:
                            break
                        closed_offset += fetch_limit
                        await asyncio.sleep(batch_delay_seconds)
                    except Exception as e:
                        logger.warning("Closed markets fetch failed (non-fatal): %s", e)
                        break

            logger.info(f"Fetched {len(all_markets)} markets, sorting by liquidity+volume to get top {top_markets_count}...")
            
            def market_score(market: Dict) -> float:
                try:
                    liquidity = float(market.get("liquidity", 0.0) or 0.0)
                    volume = float(market.get("volume", 0.0) or 0.0)
                    return liquidity + volume
                except (ValueError, TypeError) as e:
                    logger.debug(f"Error calculating market score: {str(e)}")
                    return 0.0
            
            all_markets.sort(key=market_score, reverse=True)
            top_markets = all_markets[:min(top_markets_count, len(all_markets))]

            # Fix 1: Filter out zero-liquidity/zero-volume zombie markets before saving.
            # Markets with combined score < MIN_MARKET_VOLUME are junk with no price discovery.
            _min_vol = getattr(settings, "MIN_MARKET_VOLUME", 100.0)
            _pre_filter = len(top_markets)
            top_markets = [
                m for m in top_markets
                if (float(m.get("volume") or 0) + float(m.get("liquidity") or 0)) >= _min_vol
            ]
            if len(top_markets) < _pre_filter:
                logger.info(
                    "Volume filter (min %.0f USD): %d/%d markets passed",
                    _min_vol, len(top_markets), _pre_filter,
                )

            if not top_markets:
                error_msg = "No markets selected after sorting - cannot proceed"
                logger.error(error_msg)
                self.ingestion_progress["status"] = "error"
                self.ingestion_progress["error_message"] = error_msg
                throttled_progress_callback(self.ingestion_progress)
                return 0
            
            logger.info(f"Selected top {len(top_markets)} markets, dividing into {num_processors} processors...")
            
            markets_per_processor = len(top_markets) // num_processors
            remainder = len(top_markets) % num_processors
            
            if markets_per_processor == 0:
                num_processors = len(top_markets)
                markets_per_processor = 1
                remainder = 0
                logger.info(f"Adjusted to {num_processors} processors (1 market each)")
            
            processor_batches = []
            start_idx = 0
            for i in range(num_processors):
                batch_size = markets_per_processor + (1 if i < remainder else 0)
                if batch_size > 0 and start_idx < len(top_markets):
                    processor_batches.append(top_markets[start_idx:start_idx + batch_size])
                    start_idx += batch_size
            
            if not processor_batches:
                logger.error("No processor batches created - cannot proceed")
                self.ingestion_progress["status"] = "error"
                throttled_progress_callback(self.ingestion_progress)
                return 0
            
            logger.info(f"Created {len(processor_batches)} processor batches (sizes: {[len(b) for b in processor_batches[:5]]}...)")
            
            self.ingestion_progress["total"] = len(top_markets)
            self.ingestion_progress["total_batches"] = len(processor_batches)
            throttled_progress_callback(self.ingestion_progress)
            
            max_concurrent = 1  # Sequential batch processing to avoid pool exhaustion
            semaphore = asyncio.Semaphore(max_concurrent)
            progress_lock = asyncio.Lock()
            api_fetched_total = 0
            db_saved_total = 0
            completed_batches = 0
            
            async def process_batch(batch_idx: int, markets_batch: List[Dict]) -> tuple[int, int]:
                nonlocal api_fetched_total, db_saved_total, completed_batches
                async with semaphore:
                    try:
                        await asyncio.sleep(batch_idx * 0.05)
                        
                        market_data = []
                        raw_markets_for_cache = []
                        for market in markets_batch:
                            try:
                                if not isinstance(market, dict):
                                    logger.warning(f"Market is not a dict: {type(market)}")
                                    continue
                                
                                raw_markets_for_cache.append(market)
                                
                                end_date = None
                                # Gamma API returns "endDateIso" (lowercase 'so') or "endDate".
                                # CLOB API returns "endDateISO" (uppercase 'ISO').
                                # Check all variants so we never store NULL end_date_iso.
                                _end_raw = (market.get("endDateISO") or market.get("endDateIso")
                                            or market.get("endDate") or market.get("end_date")
                                            or market.get("end_date_iso"))
                                if _end_raw:
                                    try:
                                        date_str = _end_raw
                                        if isinstance(date_str, str):
                                            if "Z" in date_str:
                                                end_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                                            else:
                                                end_date = datetime.fromisoformat(date_str)
                                        else:
                                            logger.debug(f"end date is not a string for market {market.get('id')}: {type(date_str)}")
                                    except (ValueError, AttributeError) as e:
                                        logger.debug(
                                            "Could not parse end date",
                                            market_id=market.get('id'),
                                            date_str=date_str if isinstance(date_str, str) else str(date_str)[:50],
                                            error=str(e)
                                        )
                                
                                raw_id = market.get("id")
                                if raw_id is None:
                                    logger.warning(f"Market missing 'id' field: {list(market.keys())[:5]}")
                                    continue
                                market_id = str(raw_id).strip()
                                if not market_id:
                                    logger.warning(f"Invalid market_id (empty after str): {raw_id!r}, skipping")
                                    continue
                                
                                liquidity_val = market.get("liquidity")
                                volume_val = market.get("volume")
                                
                                try:
                                    liquidity = float(liquidity_val) if liquidity_val is not None else 0.0
                                    volume = float(volume_val) if volume_val is not None else 0.0
                                    
                                    if liquidity < 0:
                                        logger.warning(f"Negative liquidity {liquidity} for market {market_id}, using 0.0")
                                        liquidity = 0.0
                                    if volume < 0:
                                        logger.warning(f"Negative volume {volume} for market {market_id}, using 0.0")
                                        volume = 0.0
                                    
                                    import math
                                    if math.isnan(liquidity) or math.isinf(liquidity):
                                        logger.warning(f"Invalid liquidity {liquidity} for market {market_id}, using 0.0")
                                        liquidity = 0.0
                                    if math.isnan(volume) or math.isinf(volume):
                                        logger.warning(f"Invalid volume {volume} for market {market_id}, using 0.0")
                                        volume = 0.0
                                except (ValueError, TypeError) as e:
                                    logger.warning(f"Could not parse liquidity/volume for market {market_id}: {str(e)}, using 0.0")
                                    liquidity = 0.0
                                    volume = 0.0
                                
                                category_value = market.get("category")
                                if not category_value:
                                    tags = market.get("tags")
                                    if isinstance(tags, list) and len(tags) > 0:
                                        category_value = tags[0]
                                    elif isinstance(tags, (str, int)):
                                        category_value = tags
                                
                                if not category_value or str(category_value).lower() == "unknown":
                                    _q = market.get("question") or market.get("title") or ""
                                    category_value = _infer_category(_q)

                                if not isinstance(category_value, str):
                                    category_value = str(category_value)
                                
                                if len(category_value) > 100:
                                    logger.warning(f"Category too long ({len(category_value)} chars) for market {market_id}, truncating")
                                    category_value = category_value[:100]
                                
                                # V2 FIX: Use MarketParserV2 to properly extract token IDs and prices
                                parsed_market = MarketParserV2.parse_market(market)
                                
                                if not parsed_market:
                                    logger.warning(f"Failed to parse market {market_id}, skipping")
                                    continue
                                
                                # FULL DATA: Gamma list often omits clobTokenIds; fetch full market when missing token IDs
                                if not parsed_market.get("yes_token_id"):
                                    try:
                                        full_market = await self.client.get_market(market_id, use_cache=False)
                                        if full_market and isinstance(full_market, dict):
                                            full_parsed = MarketParserV2.parse_market(full_market)
                                            if full_parsed and full_parsed.get("yes_token_id"):
                                                parsed_market = full_parsed
                                                logger.debug(f"Enriched market {market_id} with token IDs from full fetch")
                                        await asyncio.sleep(0.15)
                                    except Exception as e:
                                        logger.debug(f"Full market fetch for {market_id} failed: {e}")
                                
                                # Extract resolution (for learnable data / prediction training)
                                resolved_flag = bool(
                                    market.get("resolved") or market.get("isResolved") or market.get("closed", False)
                                )
                                resolution_val = (
                                    market.get("resolution") or market.get("outcome") or market.get("resolutionPrice")
                                )
                                if resolution_val is not None:
                                    rv = str(resolution_val).strip().upper()
                                    if rv in ("YES", "NO"):
                                        resolution_val = rv
                                    elif rv in ("1", "1.0"):
                                        resolution_val = "YES"
                                    elif rv in ("0", "0.0"):
                                        resolution_val = "NO"
                                    else:
                                        resolution_val = rv if rv else None
                                # Gamma API often omits resolution; infer from outcomePrices when closed
                                if resolution_val is None and resolved_flag:
                                    op = parsed_market.get("outcome_prices") or market.get("outcomePrices")
                                    if isinstance(op, str):
                                        try:
                                            op = json.loads(op) if op.strip().startswith("[") else [x.strip() for x in op.split(",")]
                                        except (json.JSONDecodeError, ValueError):
                                            op = [x.strip() for x in str(op).split(",")] if "," in str(op) else []
                                    if isinstance(op, (list, tuple)) and len(op) >= 2:
                                        p0 = float(op[0]) if op[0] else 0
                                        p1 = float(op[1]) if op[1] else 0
                                        if p0 >= 0.99 and p1 <= 0.01:
                                            resolution_val = "YES"
                                        elif p0 <= 0.01 and p1 >= 0.99:
                                            resolution_val = "NO"
                                resolved_at_val = None
                                for key in ("resolvedAt", "resolved_at", "closedAt", "closed_at"):
                                    raw_ts = market.get(key)
                                    if raw_ts:
                                        resolved_at_val = self._parse_timestamp(raw_ts)
                                        break
                                # Fallback: use end_date_iso as resolved_at proxy for resolved markets
                                # without an explicit resolution timestamp (prevents data leakage in training)
                                if resolved_at_val is None and resolved_flag and end_date:
                                    resolved_at_val = end_date
                                # Build market data with V2 fields
                                market_data.append({
                                    "id": market_id,
                                    "condition_id": parsed_market.get("condition_id") or "",
                                    "question": parsed_market.get("question") or "",
                                    "description": parsed_market.get("description"),  # Market description/context
                                    "slug": parsed_market.get("slug") or "",
                                    "category": category_value,
                                    "resolution_source": market.get("resolutionSource") or market.get("resolution_source") or "",
                                    "end_date_iso": end_date,
                                    "image": market.get("image") or market.get("imageUrl") or "",
                                    "active": bool(market.get("active", market.get("isActive", True))),
                                    "liquidity": liquidity,
                                    "volume": volume,
                                    # Resolution (CRITICAL for learning / prediction training)
                                    "resolved": resolved_flag,
                                    "resolution": resolution_val,
                                    "resolved_at": resolved_at_val,
                                    # V2 CLOB Token IDs (CRITICAL for full price history)
                                    # M1: Normalize empty string to NULL so downstream filters don't need != ''
                                    "yes_token_id": (parsed_market.get("yes_token_id") or "").strip() or None,
                                    "no_token_id": (parsed_market.get("no_token_id") or "").strip() or None,
                                    "yes_price": parsed_market.get("yes_price"),
                                    "no_price": parsed_market.get("no_price"),
                                    "outcome_prices": parsed_market.get("outcome_prices")
                                })
                            except Exception as e:
                                logger.warning(f"Error processing market {market.get('id', 'unknown')}: {str(e)}")
                                continue
                        
                        if not market_data:
                            logger.warning(f"Batch {batch_idx} produced no valid market data")
                            return 0, 0
                        
                        batch_api_count = len(market_data)
                        try:
                            batch_db_count = await save_to_db(market_data)
                        except RuntimeError as db_error:
                            logger.error(
                                "CRITICAL: Database save failed for batch",
                                batch_index=batch_idx,
                                error=str(db_error),
                                exc_info=True
                            )
                            raise
                        
                        async with progress_lock:
                            # BUG FIX: Protected cached_markets update with same lock
                            # Root cause: Multiple async tasks can modify self.cached_markets concurrently
                            # without proper locking, causing race conditions
                            # Impact: Data corruption, lost data, inconsistent state
                            # Fix: Use progress_lock to protect both progress updates AND cache updates
                            api_fetched_total += batch_api_count
                            db_saved_total += batch_db_count
                            completed_batches += 1
                            
                            self.ingestion_progress["api_fetched"] = api_fetched_total
                            self.ingestion_progress["db_saved"] = db_saved_total
                            self.ingestion_progress["current"] = api_fetched_total
                            self.ingestion_progress["current_batch"] = completed_batches
                            
                            # CRITICAL FIX: Update last_market_update incrementally as batches complete
                            # Root cause: last_market_update only set at end, so if ingestion freezes, timestamp never updates
                            # Impact: UI shows "Never" even though data is being saved
                            # Fix: Update timestamp after each successful batch save
                            if batch_db_count > 0:
                                self.last_market_update = datetime.now(timezone.utc)
                                logger.debug(f"Updated last_market_update after batch {batch_idx}: {batch_db_count} markets saved")
                            
                            # Protected cache update - prevents race conditions
                            if not hasattr(self, 'cached_markets') or not self.cached_markets:
                                self.cached_markets = []
                            self.cached_markets.extend(raw_markets_for_cache)
                        
                        throttled_progress_callback(self.ingestion_progress)
                        
                        logger.debug(f"Batch {batch_idx} completed: {batch_api_count} fetched, {batch_db_count} saved")
                        return batch_api_count, batch_db_count
                    except RuntimeError as db_error:
                        logger.error(
                            "CRITICAL: Database save error in batch processing",
                            batch_index=batch_idx,
                            error=str(db_error),
                            error_type=type(db_error).__name__,
                            exc_info=True
                        )
                        import traceback
                        logger.error(
                            "Database save error traceback",
                            batch_index=batch_idx,
                            traceback=traceback.format_exc()
                        )
                        raise
                    except Exception as e:
                        logger.error(
                            "Error processing batch",
                            batch_index=batch_idx,
                            error=str(e),
                            exc_info=True
                        )
                        import traceback
                        logger.error(
                            "Batch processing traceback",
                            batch_index=batch_idx,
                            traceback=traceback.format_exc()
                        )
                        return 0, 0
            
            logger.info(f"Starting parallel processing of {len(processor_batches)} batches (max {max_concurrent} concurrent)...")
            tasks = [process_batch(i, batch) for i, batch in enumerate(processor_batches)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            failed_batches = 0
            successful_batches = 0
            # M6 FIX: Reset checkpoint before recording new failures so retries accumulate cleanly.
            self._failed_ingestion_batches = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    failed_batches += 1
                    # M6 FIX: Store the actual market data so next cycle can retry only this batch.
                    if i < len(processor_batches):
                        self._failed_ingestion_batches.append(processor_batches[i])
                    logger.error(
                        "Batch failed with exception",
                        batch_index=i,
                        error=str(result),
                        error_type=type(result).__name__,
                        exc_info=True
                    )
                elif isinstance(result, tuple) and len(result) == 2:
                    api_count, db_count = result
                    if api_count == 0 and db_count == 0:
                        logger.warning(
                            "Batch returned zero results",
                            batch_index=i,
                            may_indicate_failure=True
                        )
                        # M6 FIX: Zero-result batches are ambiguous — store for retry.
                        if i < len(processor_batches):
                            self._failed_ingestion_batches.append(processor_batches[i])
                        failed_batches += 1
                    else:
                        successful_batches += 1
                else:
                    logger.warning(
                        "Batch returned unexpected result type",
                        batch_index=i,
                        result_type=type(result).__name__,
                        result_value=str(result)[:200] if result else None
                    )
                    if i < len(processor_batches):
                        self._failed_ingestion_batches.append(processor_batches[i])
                    failed_batches += 1

            if self._failed_ingestion_batches:
                logger.info(
                    "M6: Stored %d failed batch(es) (%d markets) for retry on next ingestion cycle.",
                    len(self._failed_ingestion_batches),
                    sum(len(b) for b in self._failed_ingestion_batches),
                )
            else:
                logger.debug("M6: All batches succeeded — no checkpoint needed.")
            
            if failed_batches > 0:
                logger.warning(
                    "Some batches failed during processing",
                    failed_batches=failed_batches,
                    successful_batches=successful_batches,
                    total_batches=len(processor_batches),
                    failure_rate=f"{(failed_batches / len(processor_batches) * 100):.1f}%" if processor_batches else "0.0%"
                )
            
            if api_fetched_total == 0:
                error_msg = "No markets were fetched from API - ingestion failed"
                logger.error(error_msg)
                self.ingestion_progress["status"] = "error"
                self.ingestion_progress["error_message"] = error_msg
                self.ingestion_progress["current"] = 0
                throttled_progress_callback(self.ingestion_progress)
                return 0
            
            if successful_batches == 0 and failed_batches > 0:
                error_msg = f"All {failed_batches} batches failed - ingestion failed"
                logger.error(error_msg)
                self.ingestion_progress["status"] = "error"
                self.ingestion_progress["error_message"] = error_msg
                throttled_progress_callback(self.ingestion_progress)
                return 0
            
            self.ingestion_progress["total"] = api_fetched_total
            self.ingestion_progress["current"] = api_fetched_total
            self.ingestion_progress["status"] = "complete"
            self.ingestion_progress["api_fetched"] = api_fetched_total
            self.ingestion_progress["db_saved"] = db_saved_total
            throttled_progress_callback(self.ingestion_progress)
            
            self.cached_markets = all_markets[:top_markets_count] if all_markets else []
            self.cached_markets_timestamp = datetime.now(timezone.utc)
            
            logger.info(f"=== INGESTION COMPLETE ===")
            logger.info(f"Total markets fetched from API: {api_fetched_total}")
            logger.info(f"Total markets saved to database: {db_saved_total}")
            logger.info(f"Total markets cached in memory: {len(self.cached_markets)}")
            logger.info(f"Successful batches: {successful_batches}, Failed batches: {failed_batches}")
            
            # CRITICAL FIX: Update timestamp even if some batches failed, as long as we saved data
            # Root cause: Timestamp only updated if all batches succeeded
            # Impact: If ingestion partially succeeds, timestamp never updates
            # Fix: Update timestamp if any data was saved
            if db_saved_total > 0:
                self.last_market_update = datetime.now(timezone.utc)
                logger.info(f"Updated last_market_update timestamp: {self.last_market_update}")
            else:
                logger.warning("No data was saved - last_market_update not updated")

            await self._log_sync_run(
                "markets",
                started_at,
                "success",
                records_processed=api_fetched_total,
                records_inserted=db_saved_total,
                records_failed=failed_batches if failed_batches else None,
            )
            return api_fetched_total
            
        except Exception as e:
            error_msg = str(e)
            error_traceback = None
            try:
                import traceback
                error_traceback = traceback.format_exc()
            except Exception as e:
                logger.debug("traceback format failed: %s", e)

            self.ingestion_progress["status"] = "error"
            self.ingestion_progress["error_message"] = error_msg
            self.ingestion_progress["error_info"] = error_traceback or error_msg
            logger.error("Error during market ingestion: %s", _safe_log_str(error_msg))
            if error_traceback:
                logger.error(_safe_log_str(error_traceback))
            throttled_progress_callback(self.ingestion_progress)
            await self._log_sync_run("markets", started_at, "failure", error_message=error_msg)
            raise
    
    def _parse_timestamp(self, timestamp_value: Any) -> datetime:
        """
        Parse timestamp from various formats.
        
        Args:
            timestamp_value: Timestamp as string, int, float, or datetime
        
        Returns:
            datetime object (timezone-aware UTC)
        
        Raises:
            ValueError: If timestamp cannot be parsed
        """
        if timestamp_value is None:
            return datetime.now(timezone.utc)
        
        if isinstance(timestamp_value, datetime):
            if timestamp_value.tzinfo is None:
                return timestamp_value.replace(tzinfo=timezone.utc)
            return timestamp_value
        
        if isinstance(timestamp_value, (int, float)):
            try:
                ts = float(timestamp_value)
                if ts > 1e12:
                    ts = ts / 1000.0
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OSError) as e:
                logger.warning(f"Could not parse numeric timestamp {timestamp_value}: {str(e)}")
                return datetime.now(timezone.utc)
        
        if isinstance(timestamp_value, str):
            try:
                if "Z" in timestamp_value:
                    return datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
                else:
                    return datetime.fromisoformat(timestamp_value)
            except ValueError as e:
                logger.warning(f"Could not parse timestamp string '{timestamp_value}': {str(e)}")
                return datetime.now(timezone.utc)
        
        logger.warning(f"Unknown timestamp type {type(timestamp_value)}, using current time")
        return datetime.now(timezone.utc)
    
    def _validate_user_data(self, users: Any) -> bool:
        """
        Validate user data structure.
        Ensures users is a non-empty list of dicts with required 'address' field.
        
        BUG FIX: This function was incorrectly nested inside _parse_timestamp().
        Root cause: Copy-paste error or indentation mistake during refactoring.
        Impact: ingest_top_users() and ingest_elite_trader_activity() would fail
        when calling validation_fn=self._validate_user_data because the function
        wasn't accessible at class level.
        """
        if not users or not isinstance(users, list):
            return False
        if len(users) == 0:
            return False
        for user in users:
            if not isinstance(user, dict):
                return False
            address = user.get("address")
            if not address or not isinstance(address, str) or not address.startswith("0x"):
                return False
        return True
    
    async def ingest_top_users(self) -> int:  # Already has return type
        """
        Ingest top N elite traders from Polymarket API (limit: TOP_TRADER_COUNT).
        Only the absolute top tier traders are monitored for maximum profitability.
        """
        logger.info(f"Ingesting elite traders (limit: {settings.TOP_TRADER_COUNT}) from Polymarket API")
        
        async def fetch_users():
            return await self.client.get_top_users(limit=settings.TOP_TRADER_COUNT)
        
        top_users = await self.recovery.execute_with_recovery(
            operation_name="fetch_top_users",
            primary_operation=fetch_users,
            is_critical=True,
            validation_fn=self._validate_user_data
        )
        
        if not top_users:
            logger.warning("No top users fetched from API")
            return 0
        
        if self.db.session_factory is None:
            logger.warning(f"Database not available - fetched {len(top_users)} users from API but not saved")
            self.last_user_update = datetime.now(timezone.utc)
            return len(top_users)
        
        try:
            rows = []
            for user in top_users:
                addr = user.get("address")
                if not addr or not isinstance(addr, str) or not addr.startswith("0x"):
                    continue
                rows.append({
                    "address": addr,
                    "total_profit": user.get("totalProfit", 0.0),
                    "total_volume": user.get("totalVolume", 0.0),
                    "win_rate": user.get("winRate", 0.0),
                    "total_trades": user.get("totalTrades", 0),
                    "wins": user.get("wins", 0),
                    "losses": user.get("losses", 0),
                    "roi": user.get("roi", 0.0),
                    "is_elite": True,
                })
            if rows:
                await self.db.upsert_users(rows)
            logger.info(f"Fetched {len(top_users)} users from API, saved to database")
            self.last_user_update = datetime.now(timezone.utc)
            return len(top_users)
        except Exception as e:
            # FIX NEW-6: Return 0 on DB failure, not len(top_users).
            # Previous behavior masked DB failures — caller thought data was persisted.
            logger.error(f"Database save failed for {len(top_users)} users: {str(e)}")
            return 0
    
    async def ingest_elite_trader_activity(self) -> int:  # Already has return type
        if self.db.session_factory is None:
            logger.warning("Database not available - cannot get elite traders list. Fetching top users from API instead.")
            top_users = await self.recovery.execute_with_recovery(
                operation_name="fetch_top_users_for_activity",
                primary_operation=lambda: self.client.get_top_users(limit=settings.TOP_TRADER_COUNT),
                is_critical=True,
                validation_fn=self._validate_user_data
            )
            if not top_users:
                return 0
            trader_addresses = [u.get("address") for u in top_users if u.get("address")]
        else:
            try:
                elite_traders = await self.db.get_elite_traders(limit=settings.TOP_TRADER_COUNT)
                trader_addresses = [t["address"] for t in elite_traders if t.get("address")]
            except Exception as e:
                logger.warning(f"Database query failed, fetching from API: {str(e)}")
                top_users = await self.recovery.execute_with_recovery(
                    operation_name="fetch_top_users_for_activity",
                    primary_operation=lambda: self.client.get_top_users(limit=settings.TOP_TRADER_COUNT),
                    is_critical=True,
                    validation_fn=self._validate_user_data
                )
                if not top_users:
                    return 0
                trader_addresses = [u.get("address") for u in top_users if u.get("address")]
        
        started_at = datetime.now(timezone.utc)
        count = 0
        api_fetched = 0
        try:
            async with self.client:
                for trader_address in trader_addresses:
                    try:
                        activity = await self.recovery.execute_with_recovery(
                            operation_name=f"fetch_activity_{trader_address}",
                            primary_operation=lambda addr=trader_address: self.client.get_user_activity(addr, limit=500, offset=0),
                            is_critical=True
                        )
                        
                        if not activity:
                            continue
                        
                        trade_data = []
                        for act in activity:
                            if act.get("type") == "trade":
                                api_fetched += 1
                                raw_market_id = act.get("marketId")
                                trade_data.append({
                                    "id": act.get("id") or f"trade_{act.get('marketId', 'unknown')}_{act.get('timestamp', 'unknown')}",
                                    "market_id": raw_market_id,
                                    "token_id": act.get("tokenId"),
                                    "user_address": trader_address,
                                    "side": act.get("side"),
                                    "size": float(act.get("size", 0.0) or 0.0),
                                    "price": float(act.get("price", 0.0) or 0.0),
                                    "timestamp": self._parse_timestamp(act.get("timestamp"))
                                })
                        
                        if trade_data and self.db.session_factory:
                            from base_engine.data.id_resolver import resolve_market_ids_batch
                            raw_ids = [t.get("market_id") for t in trade_data if t.get("market_id")]
                            resolved = await resolve_market_ids_batch(self.db, raw_ids) if raw_ids else {}
                            for t in trade_data:
                                rid = t.get("market_id")
                                if rid and rid in resolved:
                                    t["market_id"] = resolved[rid]
                            try:
                                await self.db.bulk_insert_trades(trade_data)
                                count += len(trade_data)
                            except Exception as e:
                                logger.warning(f"Database save failed for trades (data available from API): {str(e)}")
                        
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.warning(f"Error ingesting activity for {trader_address}", error=str(e))
                        continue
            
            logger.info(
                "Trade ingestion complete",
                api_fetched=api_fetched,
                db_saved=count,
                traders_processed=len(trader_addresses)
            )
            await self._log_sync_run(
                "trades",
                started_at,
                "success",
                records_processed=api_fetched,
                records_inserted=count,
                metadata={"traders_processed": len(trader_addresses)},
            )
            return api_fetched
        except Exception as e:
            logger.exception("ingest_elite_trader_activity failed")
            await self._log_sync_run("trades", started_at, "failure", error_message=str(e))
            return 0
    
    async def ingest_market_prices_from_database(self) -> int:  # Already has return type
        """
        V2 FIX: Ingest price history using CLOB API with token IDs from database.
        
        This method now:
        1. Gets markets from database (which have yes_token_id stored)
        2. Uses CLOB API get_price_history with token_id (not market_id)
        3. Fetches historical price data properly
        """
        if self.db.session_factory is None:
            logger.warning("Database not available - cannot get markets with token IDs")
            return 0
        
        # Get markets with token IDs from database
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import select, and_
                from base_engine.data.database import Market
                
                # Get markets that have token IDs
                result = await session.execute(
                    select(Market).where(
                        and_(
                            Market.active == True,
                            Market.yes_token_id.isnot(None),
                            Market.yes_token_id != ""
                        )
                    ).order_by(Market.liquidity.desc()).limit(settings.SOFTEST_MARKETS_COUNT)
                )
                markets = result.scalars().all()
                
                if not markets:
                    logger.warning("No markets with token IDs found in database")
                    return 0
                
                logger.info(f"Found {len(markets)} markets with token IDs for price ingestion")
        
        except Exception as e:
            logger.error(f"Database query failed: {str(e)}", exc_info=True)
            return 0
        
        count = 0
        api_fetched = 0
        days_back = 7  # Fetch last 7 days of price history
        
        # Calculate time range
        end_ts = int(datetime.now(timezone.utc).timestamp())
        start_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
        
        async with self.client:
            for market in markets:
                try:
                    # V2 FIX: Use yes_token_id from database (not from API call)
                    token_id = market.yes_token_id
                    if not token_id:
                        logger.debug(f"Market {market.id} has no yes_token_id, skipping")
                        continue
                    
                    # V2 FIX: Use CLOB API get_price_history with token_id
                    # Enhanced retry logic with exponential backoff
                    price_history = None
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            price_history = await self.client.get_price_history(
                                token_id=token_id,
                                start_ts=start_ts,
                                end_ts=end_ts,
                                interval="1h"  # Hourly data
                            )
                            if price_history and price_history.get("history"):
                                break  # Success
                        except Exception as e:
                            if attempt < max_retries - 1:
                                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                                logger.debug(f"Price history fetch failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                                await asyncio.sleep(wait_time)
                            else:
                                logger.warning(f"Price history fetch failed after {max_retries} attempts for market {market.id}: {str(e)}")
                    
                    if not price_history:
                        price_history = {"history": []}
                    
                    history = price_history.get("history", [])
                    if not history:
                        logger.debug(f"No price history for token {token_id} (market {market.id})")
                        continue
                    
                    api_fetched += len(history)
                    
                    # Convert to price data format
                    price_data = []
                    for point in history:
                        try:
                            timestamp_ts = point.get("t")
                            price = point.get("p")
                            
                            if timestamp_ts is None or price is None:
                                continue
                            
                            # Convert Unix timestamp to datetime; use naive UTC for DB
                            timestamp = datetime.fromtimestamp(timestamp_ts, tz=timezone.utc)
                            price_data.append({
                                "market_id": market.id,
                                "token_id": token_id,
                                "price": float(price),
                                "timestamp": _naive_utc_ts(timestamp),
                                "side": "YES"  # YES token
                            })
                        except Exception as e:
                            logger.debug(f"Error parsing price point: {str(e)}")
                            continue
                    
                    # Also fetch NO token if available (with retry logic)
                    if market.no_token_id:
                        no_price_history = None
                        for attempt in range(max_retries):
                            try:
                                no_price_history = await self.client.get_price_history(
                                    token_id=market.no_token_id,
                                    start_ts=start_ts,
                                    end_ts=end_ts,
                                    interval="1h"
                                )
                                if no_price_history and no_price_history.get("history"):
                                    break  # Success
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    wait_time = 2 ** attempt
                                    logger.debug(f"NO token price history fetch failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                                    await asyncio.sleep(wait_time)
                                else:
                                    logger.warning(f"NO token price history fetch failed after {max_retries} attempts for market {market.id}: {str(e)}")
                        
                        if not no_price_history:
                            no_price_history = {"history": []}
                        
                        no_history = no_price_history.get("history", [])
                        api_fetched += len(no_history)
                        
                        for point in no_history:
                            try:
                                timestamp_ts = point.get("t")
                                price = point.get("p")
                                
                                if timestamp_ts is None or price is None:
                                    continue
                                
                                timestamp = datetime.fromtimestamp(timestamp_ts, tz=timezone.utc)
                                price_data.append({
                                    "market_id": market.id,
                                    "token_id": market.no_token_id,
                                    "price": float(price),
                                    "timestamp": _naive_utc_ts(timestamp),
                                    "side": "NO"  # NO token
                                })
                            except Exception as e:
                                logger.debug(f"Error parsing NO token price point: {str(e)}")
                                continue
                    
                    if price_data and self.db.session_factory:
                        try:
                            n = await self._bulk_insert_prices_safe(price_data)
                            count += n
                            logger.debug(f"Saved {n} price points for market {market.id}")
                        except Exception as e:
                            logger.warning(f"Database save failed for prices: {str(e)}")
                    
                    await asyncio.sleep(0.2)  # Rate limiting
                    
                except Exception as e:
                    logger.warning(f"Error ingesting prices for market {market.id}: {str(e)}", exc_info=True)
                    continue
        
        logger.info(
            "Price ingestion complete (V2 CLOB API)",
            api_fetched=api_fetched,
            db_saved=count,
            markets_processed=len(markets)
        )
        return api_fetched
    
    def _extract_tokens_from_market(
        self, 
        market_data: Dict[str, Any], 
        market_id: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Extract tokens from market data using multiple extraction strategies.
        
        REBUILT: Comprehensive token extraction with validation and diagnostics.
        
        Returns:
            Tuple[List[Dict], Dict]: (tokens_list, diagnostics_dict)
        """
        diagnostics = {
            "market_data_type": type(market_data).__name__,
            "is_dict": isinstance(market_data, dict),
            "keys_found": [],
            "extraction_strategy_used": None,
            "tokens_found": 0,
            "error": None
        }
        
        tokens = []
        
        # CRITICAL FIX: Validate input before accessing keys
        if not isinstance(market_data, dict):
            diagnostics["error"] = f"Market data is not a dict, got {type(market_data).__name__}"
            logger.error(f"Market {market_id}: {diagnostics['error']}")
            return tokens, diagnostics
        
        diagnostics["keys_found"] = list(market_data.keys())[:20]  # First 20 keys for debugging
        
        # Strategy 1: Direct tokens array
        if "tokens" in market_data:
            direct_tokens = market_data.get("tokens")
            if isinstance(direct_tokens, list) and len(direct_tokens) > 0:
                tokens = [t for t in direct_tokens if isinstance(t, dict)]
                if tokens:
                    diagnostics["extraction_strategy_used"] = "direct_tokens"
                    diagnostics["tokens_found"] = len(tokens)
                    logger.info(f"Market {market_id}: Found {len(tokens)} tokens via direct 'tokens' key")
                    return tokens, diagnostics
        
        # Strategy 2: Outcomes array (common in Polymarket API)
        if "outcomes" in market_data:
            outcomes = market_data.get("outcomes")
            if isinstance(outcomes, list):
                for outcome in outcomes:
                    if isinstance(outcome, dict):
                        # Token might be the outcome itself or nested
                        if "tokenId" in outcome:
                            tokens.append(outcome)
                        elif "token" in outcome and isinstance(outcome["token"], dict):
                            tokens.append(outcome["token"])
                if tokens:
                    diagnostics["extraction_strategy_used"] = "outcomes_array"
                    diagnostics["tokens_found"] = len(tokens)
                    logger.info(f"Market {market_id}: Found {len(tokens)} tokens via 'outcomes' array")
                    return tokens, diagnostics
        
        # Strategy 3: Conditions array
        if "conditions" in market_data:
            conditions = market_data.get("conditions")
            if isinstance(conditions, list):
                for condition in conditions:
                    if isinstance(condition, dict):
                        if "tokenId" in condition:
                            tokens.append(condition)
                        elif "tokens" in condition and isinstance(condition["tokens"], list):
                            tokens.extend([t for t in condition["tokens"] if isinstance(t, dict)])
            elif isinstance(conditions, dict):
                if "tokens" in conditions and isinstance(conditions["tokens"], list):
                    tokens.extend([t for t in conditions["tokens"] if isinstance(t, dict)])
            if tokens:
                diagnostics["extraction_strategy_used"] = "conditions_array"
                diagnostics["tokens_found"] = len(tokens)
                logger.info(f"Market {market_id}: Found {len(tokens)} tokens via 'conditions'")
                return tokens, diagnostics
        
        # Strategy 4: Check if market data itself has tokenId (single token market)
        if "tokenId" in market_data:
            tokens.append(market_data)
            diagnostics["extraction_strategy_used"] = "root_tokenId"
            diagnostics["tokens_found"] = 1
            logger.info(f"Market {market_id}: Found single token in market root")
            return tokens, diagnostics
        
        # Strategy 5: clobTokenIds array (Gamma API V2 - most common)
        clob_token_ids = market_data.get("clobTokenIds") or market_data.get("clob_token_ids")
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except (json.JSONDecodeError, TypeError):
                clob_token_ids = None
        if isinstance(clob_token_ids, list) and len(clob_token_ids) > 0:
            for tid in clob_token_ids:
                if tid:
                    tokens.append({"tokenId": str(tid)})
            if tokens:
                diagnostics["extraction_strategy_used"] = "clobTokenIds"
                diagnostics["tokens_found"] = len(tokens)
                logger.info(f"Market {market_id}: Found {len(tokens)} tokens via clobTokenIds")
                return tokens, diagnostics
        
        # Strategy 6: yes_token_id / no_token_id (parsed/DB format)
        yes_tid = market_data.get("yes_token_id") or market_data.get("yesTokenId")
        no_tid = market_data.get("no_token_id") or market_data.get("noTokenId")
        if yes_tid:
            tokens.append({"tokenId": str(yes_tid)})
        if no_tid:
            tokens.append({"tokenId": str(no_tid)})
        if tokens:
            diagnostics["extraction_strategy_used"] = "yes_no_token_id"
            diagnostics["tokens_found"] = len(tokens)
            logger.info(f"Market {market_id}: Found {len(tokens)} tokens via yes_token_id/no_token_id")
            return tokens, diagnostics
        
        # No tokens found - log detailed diagnostics
        diagnostics["error"] = "No tokens found in any expected location"
        logger.warning(
            f"Market {market_id}: No tokens found",
            **diagnostics,
            has_tokens="tokens" in market_data,
            has_outcomes="outcomes" in market_data,
            has_conditions="conditions" in market_data,
            market_preview=str(market_data)[:500]
        )
        
        return tokens, diagnostics
    
    def _extract_token_id(self, token: Dict) -> Optional[str]:
        """Extract tokenId from token dict using multiple field names."""
        return (
            token.get("tokenId") or
            token.get("id") or
            token.get("token_id") or
            token.get("token") or
            (token.get("token") if isinstance(token.get("token"), str) else None)
        )
    
    def get_cached_markets(self, limit: Optional[int] = None, active: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        Retrieve cached markets from ingestion service.
        
        Args:
            limit: Maximum number of markets to return
            active: Filter by active status (None = all)
        
        Returns:
            List of market dictionaries
        """
        if not hasattr(self, 'cached_markets') or not self.cached_markets:
            return []
        
        markets = self.cached_markets
        
        if active is not None:
            markets = [m for m in markets if m.get("active") == active]
        
        if limit is not None and limit > 0:
            markets = markets[:limit]
        
        return markets
    
    async def _get_market_resolution_normalized(  # Return type will be inferred
        self,
        market: Dict[str, Any],
        condition_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get market resolution using normalized format (FOOLPROOF with fallback).
        
        This ensures both Gamma API and blockchain methods return compatible data:
        - Consistent structure: {'resolved': bool, 'resolution': str, 'source_method': str, 'resolved_at': datetime}
        - Option A (Gamma API): Fast, but needs error handling
        - Option B (Blockchain): Slower but foolproof fallback
        
        Args:
            market: Market data dictionary from API
            condition_id: Optional condition ID for blockchain fallback
            
        Returns:
            Normalized resolution dict with:
            - resolved: bool
            - resolution: str (YES/NO/etc) or None
            - source_method: str ('gamma_api' or 'blockchain')
            - resolved_at: datetime or None
        """
        # Normalized return structure
        result = {
            "resolved": False,
            "resolution": None,
            "source_method": None,
            "resolved_at": None
        }
        
        # Option A: Gamma API (fastest, but needs foolproof error handling)
        try:
            # Check multiple possible fields for resolution status
            resolved_flag = (
                market.get("resolved") or
                market.get("isResolved") or
                market.get("closed") or
                False
            )
            
            if resolved_flag:
                # Extract resolution outcome from multiple possible fields
                resolution = (
                    market.get("resolution") or
                    market.get("outcome") or
                    market.get("winningOutcome") or
                    None
                )
                # Gamma API often omits resolution; infer from outcomePrices
                if resolution is None:
                    op = market.get("outcomePrices") or market.get("outcome_prices")
                    if isinstance(op, str):
                        try:
                            op = json.loads(op) if op.strip().startswith("[") else [x.strip() for x in op.split(",")]
                        except (json.JSONDecodeError, ValueError):
                            op = [x.strip() for x in str(op).split(",")] if "," in str(op) else []
                    if isinstance(op, (list, tuple)) and len(op) >= 2:
                        p0 = float(op[0]) if op[0] else 0
                        p1 = float(op[1]) if op[1] else 0
                        if p0 >= 0.99 and p1 <= 0.01:
                            resolution = "YES"
                        elif p0 <= 0.01 and p1 >= 0.99:
                            resolution = "NO"

                # Extract resolved timestamp (only parse when value is a string)
                resolved_at = None
                resolved_at_raw = market.get("resolvedAt")
                if isinstance(resolved_at_raw, str):
                    try:
                        resolved_at = datetime.fromisoformat(resolved_at_raw.replace('Z', '+00:00'))
                    except (ValueError, AttributeError, TypeError):
                        pass
                closed_at_raw = market.get("closedAt")
                if resolved_at is None and isinstance(closed_at_raw, str):
                    try:
                        resolved_at = datetime.fromisoformat(closed_at_raw.replace('Z', '+00:00'))
                    except (ValueError, AttributeError, TypeError):
                        pass
                # Fallback: use endDate as resolved_at proxy (prevents NULL resolved_at)
                if resolved_at is None:
                    end_date_raw = market.get("endDate") or market.get("end_date_iso")
                    if isinstance(end_date_raw, str):
                        try:
                            resolved_at = datetime.fromisoformat(end_date_raw.replace('Z', '+00:00'))
                        except (ValueError, AttributeError, TypeError):
                            pass

                result = {
                    "resolved": True,
                    "resolution": resolution,
                    "source_method": "gamma_api",
                    "resolved_at": resolved_at
                }
                
                logger.debug(
                    f"Resolution from Gamma API",
                    resolved=True,
                    resolution=resolution,
                    resolved_at=resolved_at
                )
                return result
                
        except Exception as e:
            logger.warning(
                f"Gamma API resolution check failed (will try blockchain): {str(e)}",
                exc_info=True
            )
            # Continue to fallback
        
        # V2 CLEANUP: Blockchain fallback removed - Polymarket V2 uses API resolution only
        # If Gamma API doesn't have resolution, market is likely unresolved
        
        # Neither method found resolution
        result = {
            "resolved": False,
            "resolution": None,
            "source_method": None,
            "resolved_at": None
        }
        
        return result
    
    async def ingest_everything(
        self,
        top_markets_count: int = 1000,
        days_back: int = 365,
        max_markets_prices: int = 1000,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        incremental: bool = False,
    ) -> Dict[str, Any]:
        """
        Run market ingest then historical price ingest in one batch.
        Phase 1: ingest_all_markets (Gamma -> markets table).
        Phase 2: ingest_historical_prices for markets from DB (CLOB prices-history -> market_prices).

        When incremental=True (scheduled runs): uses shorter days_back (PRICE_HISTORY_INCREMENTAL_DAYS),
        skips markets that have price data updated within PRICE_HISTORY_SKIP_RECENT_HOURS.
        """
        result: Dict[str, Any] = {
            "success": False,
            "phase1_count": 0,
            "phase2_result": None,
            "error": None,
        }
        started_at = datetime.now(timezone.utc)
        if self.db:
            await self.db.mark_stale_sync_logs_failed(
                component="data_ingestion", sync_type="full",
                older_than_hours=getattr(settings, "SYNC_LOG_STALE_HOURS", 2.0),
            )
            in_progress = await self.db.is_sync_in_progress(component="data_ingestion", sync_type="full")
            if in_progress:
                result["error"] = "Full ingestion already in progress (check sync_log for running entry)"
                return result
            await self.db.insert_sync_log(
                sync_type="full",
                component="data_ingestion",
                status="running",
                started_at=started_at,
                completed_at=None,
            )
        ok, err = await self._pre_ingestion_checks()
        if not ok:
            result["error"] = err
            if self.db:
                await self.db.update_running_sync_log(
                    "data_ingestion", "full", started_at, "failed", error_message=err
                )
            return result
        try:
            top_markets_count = max(1, int(top_markets_count)) if isinstance(top_markets_count, (int, float)) else 1000
            days_back = max(1, int(days_back)) if isinstance(days_back, (int, float)) else 365
            max_markets_prices = max(1, int(max_markets_prices)) if isinstance(max_markets_prices, (int, float)) else 1000
        except (OverflowError, ValueError, TypeError):
            top_markets_count, days_back, max_markets_prices = 1000, 365, 1000

        # Incremental mode: shorter window + skip recently-updated markets
        if incremental:
            days_back = getattr(settings, "PRICE_HISTORY_INCREMENTAL_DAYS", 7)
            skip_recent_hours = getattr(settings, "PRICE_HISTORY_SKIP_RECENT_HOURS", 6.0)
        else:
            skip_recent_hours = None

        try:
            # Phase 1: markets (include_closed=True to get resolved markets for learning)
            count = await self.ingest_all_markets(
                progress_callback=progress_callback,
                top_markets_count=top_markets_count,
                include_closed=True,
            )
            result["phase1_count"] = count

            # Log Phase 1 success immediately so PipelineGate sees fresh market data
            # even if Phase 2 (price history) takes hours or fails.
            if count > 0:
                try:
                    await self._log_sync_run(
                        "markets", started_at, "success",
                        records_processed=count,
                        metadata={"phase": 1, "source": "ingest_everything"},
                    )
                    logger.info("Phase 1 (markets) logged success: %d markets", count)
                except Exception as e:
                    logger.warning("Failed to log Phase 1 sync (non-fatal): %s", e)

            # Phase 2: historical prices for markets we have in DB
            market_ids: Optional[List[str]] = None
            if self.db:
                if self.smart_fetcher and not incremental:
                    try:
                        market_ids = await self.smart_fetcher.predict_active_market_ids(top_n=max_markets_prices)
                    except Exception as e:
                        logger.warning("SmartDataFetcher failed, falling back to get_softest_markets: %s", e)
                if not market_ids:
                    if incremental and skip_recent_hours and skip_recent_hours > 0:
                        markets = await self.db.get_markets_needing_price_update(
                            limit=max_markets_prices, skip_recent_hours=skip_recent_hours
                        )
                    else:
                        markets = await self.db.get_markets_for_price_ingestion(limit=max_markets_prices)
                    market_ids = [str(m["id"]) for m in markets if m.get("id") is not None]
                if not market_ids:
                    markets = await self.db.get_softest_markets(limit=max_markets_prices)
                    market_ids = [str(m["id"]) for m in markets if m.get("id") is not None]
                if not market_ids:
                    market_ids = await self.db.get_recent_market_ids(limit=max_markets_prices) or None

            to_ts = int(datetime.now(timezone.utc).timestamp())
            from_ts = to_ts - (days_back * 24 * 60 * 60)
            def _phase2_progress_cb(prog: Dict[str, Any]) -> None:
                if progress_callback:
                    try:
                        progress_callback({"phase": 2, "phase_name": "price", **prog})
                    except Exception as e:
                        logger.debug("price phase progress callback failed: %s", e)
            phase2 = await self.ingest_historical_prices(
                market_ids=market_ids,
                from_timestamp=from_ts,
                to_timestamp=to_ts,
                max_markets=max_markets_prices,
                progress_callback=_phase2_progress_cb,
                resume_from_checkpoint=True,
            )
            result["phase2_result"] = phase2
            result["success"] = phase2.get("success", False)
            # So Status section reflects market ingest after Pull all, not phase-2 overwrite
            self.ingestion_progress["api_fetched"] = count
            self.ingestion_progress["db_saved"] = count
            diag = (phase2 or {}).get("diagnostics", {}) or {}
            await self._log_sync_run(
                "full",
                started_at,
                "success" if result["success"] else "failure",
                records_processed=count,
                records_inserted=diag.get("prices_ingested"),
                metadata={"phase1_count": count, "phase2_success": result["success"]},
            )
            # Phase 3: elite users + trades (non-fatal; populates users & trades for Elite Traders UI)
            try:
                await self.ingest_top_users()
            except Exception as eu:
                logger.warning("Elite user ingest failed (non-fatal): %s", eu)
            try:
                await self.ingest_elite_trader_activity()
            except Exception as ea:
                logger.warning("Elite trader activity ingest failed (non-fatal): %s", ea)
        except Exception as e:
            logger.exception("ingest_everything failed")
            result["error"] = str(e)
            await self._log_sync_run("full", started_at, "failure", error_message=str(e))
        return result

    async def _log_sync_run(
        self,
        sync_type: str,
        started_at: datetime,
        status: str,
        records_processed: Optional[int] = None,
        records_inserted: Optional[int] = None,
        records_failed: Optional[int] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Close the running sync_log row (update to success/failure); if none found, insert completion row."""
        if not self.db:
            return
        try:
            completed_at = datetime.now(timezone.utc)
            updated = await self.db.update_running_sync_log(
                "data_ingestion",
                sync_type,
                started_at,
                status,
                completed_at=completed_at,
                error_message=error_message,
                records_processed=records_processed,
                records_inserted=records_inserted,
                metadata=metadata,
            )
            if updated == 0:
                await self.db.insert_sync_log(
                    sync_type=sync_type,
                    component="data_ingestion",
                    status=status,
                    started_at=started_at,
                    completed_at=completed_at,
                    records_processed=records_processed,
                    records_inserted=records_inserted,
                    records_failed=records_failed,
                    error_message=error_message,
                    metadata=metadata,
                )
        except Exception as e:
            logger.warning("_log_sync_run failed (non-fatal): %s", e)

    async def _pre_ingestion_checks(self) -> Tuple[bool, Optional[str]]:
        """
        Shared pre-flight: client, API connectivity, DB verify.
        Returns (True, None) on success, (False, error_message) on failure.
        """
        if not self.client:
            return (False, "CRITICAL: Polymarket client not initialized")
        try:
            logger.info("Testing Polymarket API connectivity...")
            test_result = await self.client.check_gamma_connectivity()
            is_connected, connection_msg = test_result
            if not is_connected:
                return (False, f"CRITICAL: Cannot connect to Polymarket API - {connection_msg}")
            logger.info("API connectivity check passed: %s", connection_msg)
        except Exception as e:
            try:
                p = Path(__file__).resolve().parent / "ingestion_error_capture.txt"
                with open(p, "w", encoding="utf-8") as _f:
                    _f.write(f"[API] {type(e).__name__}: {e}\n{traceback.format_exc()}\n")
            except Exception as e2:
                logger.debug("API error capture file write failed: %s", e2)
            return (False, f"CRITICAL: API connectivity check failed: {str(e)}")
        if self.db and self.db.session_factory:
            try:
                await self.db._verify_database()
                logger.info("Database connection verified - ready for ingestion")
            except Exception as db_err:
                db_detail = (str(db_err) or type(db_err).__name__).strip()
                try:
                    capture_path = Path(__file__).resolve().parent / "ingestion_error_capture.txt"
                    with open(capture_path, "w", encoding="utf-8") as _f:
                        _f.write(f"Exception type: {type(db_err).__name__}\nException: {db_err}\nTraceback:\n{traceback.format_exc()}\n")
                except Exception as e:
                    logger.debug("DB error capture file write failed: %s", e)
                return (False, f"CRITICAL: Database connection verification failed: {db_detail}")
        else:
            return (False, "CRITICAL: Database not initialized - cannot save data")
        return (True, None)

    def _backfill_checkpoint_path(self) -> Path:
        """Path for resumable backfill checkpoint (data/backfill_checkpoint.json)."""
        return Path(__file__).resolve().parent.parent / "data" / "backfill_checkpoint.json"

    def _read_backfill_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Read checkpoint for resumable backfill. Returns None if missing or invalid."""
        path = self._backfill_checkpoint_path()
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "phase1_done" in data and "last_batch_index" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _write_backfill_checkpoint(self, phase1_done: bool, last_batch_index: int) -> None:
        """Persist checkpoint after phase 1 or after each price batch."""
        path = self._backfill_checkpoint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "phase1_done": phase1_done,
                    "last_batch_index": last_batch_index,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, f, indent=0)
        except OSError as e:
            logger.debug("Could not write backfill checkpoint: %s", e)

    async def run_backfill(
        self,
        days_back: Optional[int] = None,
        markets_batch_size: Optional[int] = None,
        prices_markets_per_batch: Optional[int] = None,
        max_market_batches: int = 1,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        resume_from_checkpoint: bool = True,
    ) -> Dict[str, Any]:
        """
        One-time backfill: ingest markets (active + closed) then historical prices in small batches.
        Uses settings BACKFILL_* when args are None. If resume_from_checkpoint=True and a checkpoint
        exists (phase1_done), resumes from last completed price batch.
        """
        days_back = days_back or getattr(settings, "BACKFILL_DAYS", 365)
        markets_batch_size = markets_batch_size or getattr(settings, "BACKFILL_MARKETS_BATCH_SIZE", 100)
        prices_markets_per_batch = prices_markets_per_batch or getattr(settings, "BACKFILL_PRICES_MARKETS_PER_BATCH", 50)
        batch_delay = getattr(settings, "BACKFILL_BATCH_DELAY_SECONDS", 2.0)
        result: Dict[str, Any] = {
            "success": False,
            "markets_ingested": 0,
            "price_batches_run": 0,
            "prices_total_ingested": 0,
            "error": None,
        }
        started_at = datetime.now(timezone.utc)
        if self.db:
            await self.db.mark_stale_sync_logs_failed(
                component="data_ingestion", sync_type="backfill",
                older_than_hours=getattr(settings, "SYNC_LOG_STALE_HOURS", 2.0),
            )
            in_progress = await self.db.is_sync_in_progress(component="data_ingestion", sync_type="backfill")
            if in_progress:
                result["error"] = "Backfill already in progress (check sync_log for running entry)"
                return result
            await self.db.insert_sync_log(
                sync_type="backfill",
                component="data_ingestion",
                status="running",
                started_at=started_at,
                completed_at=None,
            )
        ok, err = await self._pre_ingestion_checks()
        if not ok:
            result["error"] = err
            if self.db:
                await self.db.update_running_sync_log(
                    "data_ingestion", "backfill", started_at, "failed", error_message=err
                )
            return result
        try:
            checkpoint = self._read_backfill_checkpoint() if resume_from_checkpoint else None
            skip_phase1 = bool(checkpoint and checkpoint.get("phase1_done"))
            start_batch_index = 0
            if skip_phase1 and checkpoint is not None:
                start_batch_index = int(checkpoint.get("last_batch_index", -1)) + 1
                logger.info("Resuming backfill from price batch index %s", start_batch_index)

            if not skip_phase1:
                # Phase 1: markets (active + closed) in one or more batches
                for batch_num in range(max_market_batches):
                    if progress_callback:
                        try:
                            progress_callback({"phase": "markets", "batch": batch_num + 1, "max_batches": max_market_batches})
                        except Exception as e:
                            logger.debug("markets phase progress callback failed: %s", e)
                    count = await self.ingest_all_markets(
                        progress_callback=progress_callback,
                        top_markets_count=markets_batch_size,
                        include_closed=True,
                    )
                    result["markets_ingested"] += count
                    if count == 0 and batch_num == 0:
                        break
                    if max_market_batches > 1 and batch_num < max_market_batches - 1:
                        await asyncio.sleep(batch_delay)
                self._write_backfill_checkpoint(phase1_done=True, last_batch_index=-1)

            if not self.db:
                result["success"] = True
                return result
            # Phase 2: historical prices in chunks
            market_ids = await self.db.get_recent_market_ids(limit=5000)
            if not market_ids:
                result["success"] = True
                return result
            to_ts = int(datetime.now(timezone.utc).timestamp())
            from_ts = to_ts - (days_back * 24 * 60 * 60)
            chunks = [
                market_ids[i : i + prices_markets_per_batch]
                for i in range(0, len(market_ids), prices_markets_per_batch)
            ]
            for idx in range(start_batch_index, len(chunks)):
                chunk = chunks[idx]
                if progress_callback:
                    try:
                        progress_callback({"phase": "prices", "batch": idx + 1, "total_batches": len(chunks)})
                    except Exception as e:
                        logger.debug("prices phase progress callback failed: %s", e)
                pr = await self.ingest_historical_prices(
                    market_ids=chunk,
                    from_timestamp=from_ts,
                    to_timestamp=to_ts,
                    max_markets=len(chunk),
                    progress_callback=progress_callback,
                    resume_from_checkpoint=True,
                )
                result["price_batches_run"] += 1
                result["prices_total_ingested"] += pr.get("diagnostics", {}).get("prices_ingested", 0)
                self._write_backfill_checkpoint(phase1_done=True, last_batch_index=idx)
                await asyncio.sleep(batch_delay)
            result["success"] = True
            await self._log_sync_run(
                "backfill",
                started_at,
                "success",
                records_processed=result.get("markets_ingested", 0) + result.get("prices_total_ingested", 0),
                records_inserted=result.get("prices_total_ingested"),
                metadata={"markets_ingested": result["markets_ingested"], "price_batches_run": result["price_batches_run"]},
            )
        except Exception as e:
            logger.exception("run_backfill failed")
            result["error"] = str(e)
            await self._log_sync_run("backfill", started_at, "failure", error_message=str(e))
        return result

    def _price_ingestion_checkpoint_path(self, run_id: str) -> Path:
        """Path for resumable price ingestion checkpoint."""
        base = Path(__file__).resolve().parent.parent / "data"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"price_ingestion_checkpoint_{run_id}.json"

    def _read_price_ingestion_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Read checkpoint for resumable price ingestion. Returns None if missing or invalid."""
        path = self._price_ingestion_checkpoint_path(run_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "last_market_index" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _write_price_ingestion_checkpoint(self, run_id: str, last_market_index: int) -> None:
        """Persist checkpoint after each market."""
        path = self._price_ingestion_checkpoint_path(run_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "last_market_index": last_market_index,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, f, indent=0)
        except OSError as e:
            logger.debug("Could not write price ingestion checkpoint: %s", e)

    def _clear_price_ingestion_checkpoint(self, run_id: str) -> None:
        """Remove checkpoint on successful completion."""
        path = self._price_ingestion_checkpoint_path(run_id)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    async def ingest_historical_prices(
        self,
        market_ids: Optional[List[str]] = None,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None,
        max_markets: int = 100,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        resume_from_checkpoint: bool = False,
    ) -> Dict[str, Any]:
        """
        Ingest historical prices using Polymarket CLOB API (V2 approach).
        
        V2 CLEANUP: Removed blockchain/FPMM strategies - Polymarket V2 uses CLOB only.
        This method uses API-only approach with token IDs from database.
        
        Strategy Priority:
        1. Polymarket Price History API (https://clob.polymarket.com/prices-history) - PRIMARY
           - Uses token_id from database (clobTokenIds)
           - Fetches both YES and NO token prices
        2. Orderbook API (current price) - Fallback
        3. Current price from market data - Last resort
        
        When to use which:
        - Full backfill / historical range with DB token IDs: use this method with market_ids
          (DB path: get_markets_with_token_ids + chunked CLOB + bulk insert).
        - Scheduled softest-markets-only (7 days, top N by liquidity): use
          ingest_market_prices_from_database().
        
        Args:
            market_ids: Optional list of specific market IDs to ingest. If None, fetches active markets.
            from_timestamp: Optional start timestamp (Unix timestamp). Defaults to 1 year ago.
            to_timestamp: Optional end timestamp (Unix timestamp). Defaults to now.
            max_markets: Maximum number of markets to process
            
        Returns:
            Dictionary with ingestion results and diagnostics
        """
        logger.info("Starting historical price ingestion (V2 CLOB API approach)")
        
        # Initialize progress
        self.ingestion_progress = {
            "current": 0,
            "total": 0,
            "status": "running",
            "current_batch": 0,
            "total_batches": 0,
            "api_fetched": 0,
            "db_saved": 0,
            "recovery_level": None,
            "error_message": None,
            "error_info": None
        }
        
        diagnostics = {
            "markets_processed": 0,
            "markets_successful": 0,
            "markets_failed": 0,
            "markets_no_events": 0,
            "markets_full_history": 0,
            "markets_snapshot_only": 0,
            "prices_ingested": 0,
            "errors": []
        }
        
        try:
            # Set default timestamps (1 year default)
            if to_timestamp is None:
                to_timestamp = int(datetime.now(timezone.utc).timestamp())
            if from_timestamp is None:
                from_timestamp = to_timestamp - (365 * 24 * 60 * 60)

            days_per_request = getattr(settings, "PRICE_HISTORY_DAYS_PER_REQUEST", 30)
            delay_req = getattr(settings, "PRICE_HISTORY_DELAY_BETWEEN_REQUESTS_SECONDS", 0.2)

            # Branch 1: Prefer DB path when we have market IDs (99% coverage).
            # When market_ids is None but DB exists, load IDs from DB so we use DB path.
            db_markets: Optional[List[Dict[str, Any]]] = None
            markets_to_process: List[Dict[str, Any]] = []
            ids_to_use: Optional[List[str]] = market_ids
            # When no market_ids provided, try loading from DB so DB path (token IDs) can be used.
            # Must await get_recent_market_ids; tests use AsyncMock for this when db is mocked.
            if ids_to_use is None and self.db and self.db.session_factory:
                try:
                    recent = await self.db.get_recent_market_ids(limit=max_markets)
                    ids_to_use = recent if recent else None
                    if ids_to_use:
                        logger.info(f"Historical prices: no market_ids provided; loaded {len(ids_to_use)} IDs from DB for DB path")
                except (TypeError, AttributeError):
                    # Mock db (e.g. get_recent_market_ids not awaitable) — skip DB load, use API path
                    ids_to_use = None
            if ids_to_use and self.db and self.db.session_factory:
                try:
                    db_markets = await self.db.get_markets_with_token_ids(ids_to_use[:max_markets])
                    # Defensive: filter out rows where both tokens are empty (DB path needs at least one)
                    if db_markets:
                        db_markets = [
                            r for r in db_markets
                            if (r.get("yes_token_id") and str(r.get("yes_token_id", "")).strip())
                            or (r.get("no_token_id") and str(r.get("no_token_id", "")).strip())
                        ]
                    if db_markets:
                        diagnostics["markets_processed"] = len(db_markets)
                        self.ingestion_progress["total"] = len(db_markets)
                        logger.info(
                            f"Processing {len(db_markets)} markets from DB (token IDs)",
                            from_timestamp=from_timestamp,
                            to_timestamp=to_timestamp,
                        )
                    else:
                        logger.info(
                            "get_markets_with_token_ids returned 0 markets (no token IDs in DB for given IDs). "
                            "Falling back to API path."
                        )
                except Exception as e:
                    logger.warning("get_markets_with_token_ids failed, falling back to API path: %s", e)
                    db_markets = None
            # Branch 2: no DB markets (empty or not attempted) -> fetch from API.
            # Flow: only one path runs — DB path returns above; API path runs only when db_markets is empty/None.
            if not db_markets:
                if ids_to_use:
                    for market_id in ids_to_use[:max_markets]:
                        try:
                            market = await self.client.get_market(market_id)
                            if market:
                                markets_to_process.append(market)
                        except Exception as e:
                            logger.warning(f"Failed to fetch market {market_id}: {str(e)}")
                            diagnostics["markets_failed"] += 1
                            diagnostics["errors"].append(f"Market {market_id}: {str(e)}")
                else:
                    markets_data = await self.client.get_markets(active=True, limit=max_markets)
                    if not markets_data:
                        raise ValueError("No markets returned from API")
                    markets_to_process = markets_data if isinstance(markets_data, list) else []
                if not markets_to_process:
                    diagnostics["markets_processed"] = 0
                    logger.info("No markets to process (all fetches returned empty)")
                    return {"success": True, "diagnostics": diagnostics, "message": "No markets to process"}
                diagnostics["markets_processed"] = len(markets_to_process)
                self.ingestion_progress["total"] = len(markets_to_process)
                logger.info(
                    f"Processing {len(markets_to_process)} markets from API",
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                )

            # --- DB path: (market_id, token_id, side) with chunked fetch + bulk insert ---
            # Concurrent token fetches per market (semaphore limits concurrent markets).
            if db_markets:
                run_id = f"{from_timestamp}_{to_timestamp}"
                start_index = 0
                if resume_from_checkpoint and self.db:
                    cp = self._read_price_ingestion_checkpoint(run_id)
                    if cp is not None:
                        start_index = int(cp.get("last_market_index", -1)) + 1
                        if start_index > 0:
                            logger.info("Resuming price ingestion from market index %s", start_index)

                max_ts_map: Dict[Tuple[str, str], int] = {}
                if getattr(settings, "PRICE_HISTORY_RANGE_AWARE_FETCH", True) and self.db:
                    try:
                        mids = [str(r["id"]) for r in db_markets if r.get("id")]
                        max_ts_map = await self.db.get_max_price_timestamps_for_markets(mids)
                        if max_ts_map:
                            logger.debug("Range-aware fetch: loaded max timestamps for %s token(s)", len(max_ts_map))
                    except Exception as e:
                        logger.debug("Range-aware fetch disabled (fallback to full range): %s", e)

                all_price_data: List[Dict[str, Any]] = []
                bulk_batch_size = getattr(settings, "PRICE_HISTORY_BULK_BATCH_SIZE", 10000)
                price_interval = getattr(settings, "PRICE_HISTORY_INTERVAL", "1h")
                max_concurrent = getattr(settings, "PRICE_HISTORY_MAX_CONCURRENT_MARKETS", 8)
                semaphore = asyncio.Semaphore(max(1, max_concurrent))

                def _effective_from_ts(mid: str, tid: str) -> Optional[int]:
                    key = (str(mid), str(tid))
                    existing = max_ts_map.get(key)
                    if existing is None:
                        return from_timestamp
                    if existing >= to_timestamp:
                        return None
                    return max(from_timestamp, existing + 1)

                async def fetch_token_history(market_id: str, token_id: str, side: str) -> Tuple[str, str, str, List[Dict[str, Any]]]:
                    eff_from = _effective_from_ts(market_id, token_id)
                    if eff_from is None:
                        return (market_id, token_id, side, [])
                    async with semaphore:
                        history = await _fetch_price_history_chunked(
                            self.client,
                            token_id,
                            eff_from,
                            to_timestamp,
                            interval=price_interval,
                            days_per_request=days_per_request,
                            delay_seconds=delay_req,
                        )
                        extra = await _fill_price_gaps_secondary(
                            market_id, token_id, eff_from, to_timestamp, len(history),
                        )
                        if extra:
                            history = list(history) + list(extra)
                        return (market_id, token_id, side, history)

                for idx, row in enumerate(db_markets):
                    if idx < start_index:
                        continue
                    self.ingestion_progress["current"] = idx + 1
                    if progress_callback:
                        try:
                            progress_callback(dict(self.ingestion_progress))
                        except Exception as cb_err:
                            logger.debug("ingest_historical_prices progress_callback error: %s", cb_err)
                    market_id = str(row["id"])
                    tasks = []
                    for token_id, side in [
                        (row.get("yes_token_id"), "YES"),
                        (row.get("no_token_id"), "NO"),
                    ]:
                        if token_id and str(token_id).strip():
                            tasks.append(fetch_token_history(market_id, str(token_id), side))
                    if not tasks:
                        continue
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for r in results:
                        if isinstance(r, Exception):
                            logger.warning("Price history failed: %s", r)
                            diagnostics["errors"].append(str(r))
                            continue
                        _market_id, _token_id, _side, history = r
                        for point in history:
                            t_ts = point.get("t")
                            p_val = point.get("p")
                            if t_ts is None or p_val is None:
                                continue
                            try:
                                ts_dt = datetime.fromtimestamp(t_ts, tz=timezone.utc)
                                all_price_data.append({
                                    "market_id": _market_id,
                                    "token_id": _token_id,
                                    "price": float(p_val),
                                    "timestamp": _naive_utc_ts(ts_dt),
                                    "side": _side,
                                })
                            except (ValueError, TypeError):
                                continue
                        if history:
                            logger.info("Market %s %s: %s points", _market_id, _side, len(history))
                    # P4: track empty vs successful price fetch so we can skip/deprioritize repeat empties
                    points_this_market = sum(
                        len(r[3]) for r in results
                        if not isinstance(r, Exception) and len(r) >= 4 and r[0] == market_id
                    )
                    if points_this_market == 0 and tasks:
                        await self.db.record_empty_price_fetch(market_id)
                    elif points_this_market > 0:
                        await self.db.reset_price_fetch_attempts(market_id)
                    if resume_from_checkpoint:
                        self._write_price_ingestion_checkpoint(run_id, idx)
                    if len(all_price_data) >= bulk_batch_size and self.db.session_factory:
                        # Dedupe by (market_id, token_id, timestamp) before insert
                        seen_keys: set = set()
                        deduped_batch: List[Dict[str, Any]] = []
                        skipped_null = 0  # FIX NEW-5: track skipped rows
                        skipped_dup = 0
                        for row in all_price_data:
                            mid = row.get("market_id")
                            tid = row.get("token_id")
                            ts = row.get("timestamp")
                            # Skip rows with missing key components to avoid None key collisions
                            if mid is None or tid is None or ts is None:
                                skipped_null += 1
                                continue
                            key = (mid, tid, ts)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                deduped_batch.append(row)
                            else:
                                skipped_dup += 1
                        if skipped_null > 0 or skipped_dup > 0:
                            logger.warning(
                                "Price dedup: %d rows had NULL key fields, %d duplicates removed (from %d total)",
                                skipped_null, skipped_dup, len(all_price_data)
                            )
                        all_price_data = deduped_batch
                        try:
                            n = await self._bulk_insert_prices_safe(all_price_data)
                            diagnostics["prices_ingested"] += (n or 0)
                            if n == 0 and len(all_price_data) > 0:
                                logger.warning(
                                    "Bulk insert (batch) returned 0 but had %s rows; sample market_id=%s",
                                    len(all_price_data),
                                    all_price_data[0].get("market_id"),
                                )
                            logger.info(f"Bulk inserted {n} price records (total so far: {diagnostics['prices_ingested']})")
                            all_price_data = []
                        except Exception as e:
                            logger.warning(f"Bulk insert prices failed: {e}")
                            diagnostics["errors"].append(f"bulk_insert_prices: {e}")
                if all_price_data and self.db.session_factory:
                    seen_final: set = set()
                    deduped_final: List[Dict[str, Any]] = []
                    skipped_null_final = 0
                    skipped_dup_final = 0
                    for row in all_price_data:
                        mid = row.get("market_id")
                        tid = row.get("token_id")
                        ts = row.get("timestamp")
                        if mid is None or tid is None or ts is None:
                            skipped_null_final += 1
                            continue
                        key = (mid, tid, ts)
                        if key not in seen_final:
                            seen_final.add(key)
                            deduped_final.append(row)
                        else:
                            skipped_dup_final += 1
                    if skipped_null_final > 0 or skipped_dup_final > 0:
                        logger.warning(
                            "Final price dedup: %d NULL key rows, %d duplicates removed (from %d total)",
                            skipped_null_final, skipped_dup_final, len(all_price_data)
                        )
                    try:
                        n = await self._bulk_insert_prices_safe(deduped_final)
                        diagnostics["prices_ingested"] += (n or 0)
                        if n == 0 and len(deduped_final) > 0:
                            logger.warning(
                                "Bulk insert returned 0 but had %s rows (check PRE_INSERT_VALIDATION, unique constraint); sample: market_id=%s token_id=%s",
                                len(deduped_final),
                                deduped_final[0].get("market_id"),
                                deduped_final[0].get("token_id"),
                            )
                    except Exception as e:
                        logger.warning(f"Bulk insert prices (final) failed: {e}")
                        diagnostics["errors"].append(f"bulk_insert_prices: {e}")
                diagnostics["markets_successful"] = len(db_markets)
                diagnostics["markets_full_history"] = len(db_markets)
                self.ingestion_progress["status"] = "completed"
                self.ingestion_progress["db_saved"] = diagnostics["prices_ingested"]
                if resume_from_checkpoint:
                    self._clear_price_ingestion_checkpoint(run_id)
                logger.info("Historical price ingestion completed (DB token path)", **diagnostics)
                return {"success": True, "diagnostics": diagnostics, "message": f"Ingested {diagnostics['prices_ingested']} prices from {diagnostics['markets_successful']} markets"}

            # --- API path: market dicts with token extraction, chunked fetch, bulk insert, all tokens ---
            for idx, market in enumerate(markets_to_process):
                self.ingestion_progress["current"] = idx + 1
                if progress_callback:
                    try:
                        progress_callback(dict(self.ingestion_progress))
                    except Exception as cb_err:
                        logger.debug("ingest_historical_prices progress_callback error: %s", cb_err)
                if market is None or not isinstance(market, dict):
                    logger.warning(f"Market {idx} invalid (None or not dict), skipping")
                    diagnostics["markets_failed"] += 1
                    continue
                market_id = market.get("id") or market.get("market_id")
                condition_id = market.get("conditionId") or market.get("condition_id")
                
                if not market_id:
                    logger.warning(f"Market {idx} missing ID, skipping")
                    diagnostics["markets_failed"] += 1
                    continue
                
                logger.info(
                    f"Processing market {idx + 1}/{len(markets_to_process)}: {market_id}",
                    condition_id=condition_id
                )
                
                # Get market resolution using normalized method (FOOLPROOF with fallback)
                resolution_data = await self._get_market_resolution_normalized(market, condition_id)
                
                # Store resolution data in database (for learnable data) — only when DB is available
                if resolution_data.get("resolved") and self.db and self.db.session_factory:
                    try:
                        await self.db.save_market_resolution(
                            market_id=str(market_id),
                            resolved=True,
                            resolution=resolution_data.get("resolution"),
                            resolution_source_method=resolution_data.get("source_method"),
                            resolved_at=resolution_data.get("resolved_at")
                        )
                        logger.debug(
                            f"Saved resolution for market {market_id}",
                            resolution=resolution_data.get("resolution"),
                            source=resolution_data.get("source_method")
                        )
                    except Exception as e:
                        logger.debug(f"Failed to save resolution for market {market_id}: {e}")
                
                try:
                    prices_ingested = 0
                    price_data_batch: List[Dict[str, Any]] = []

                    # Strategy 1: Chunked price history for all tokens (no break after first)
                    tokens = []
                    token_diagnostics = {}
                    collected_yes_tid: Optional[str] = None
                    collected_no_tid: Optional[str] = None
                    if isinstance(market, dict):
                        tokens, token_diagnostics = self._extract_tokens_from_market(market, market_id)
                    if tokens:
                        for i, token in enumerate(tokens):
                            token_id_str = self._extract_token_id(token)
                            if not token_id_str:
                                continue
                            if i == 0:
                                collected_yes_tid = token_id_str
                            elif i == 1:
                                collected_no_tid = token_id_str
                            try:
                                history = await _fetch_price_history_chunked(
                                    self.client,
                                    token_id_str,
                                    from_timestamp,
                                    to_timestamp,
                                    interval="1h",
                                    days_per_request=days_per_request,
                                    delay_seconds=delay_req,
                                )
                                for point in history:
                                    t_ts = point.get("t")
                                    p_val = point.get("p")
                                    if t_ts is None or p_val is None:
                                        continue
                                    try:
                                        ts_dt = datetime.fromtimestamp(t_ts, tz=timezone.utc)
                                        price_data_batch.append({
                                            "market_id": str(market_id),
                                            "token_id": token_id_str,
                                            "price": float(p_val),
                                            "timestamp": _naive_utc_ts(ts_dt),
                                            "side": None,
                                        })
                                    except (ValueError, TypeError):
                                        continue
                                if history:
                                    logger.info("Market %s token: %s points", market_id, len(history))
                            except Exception as e:
                                logger.debug(f"Chunked price history failed for market {market_id} token {token_id_str}: {e}")

                    if price_data_batch and self.db.session_factory:
                        try:
                            prices_ingested = await self._bulk_insert_prices_safe(price_data_batch)
                            diagnostics["markets_successful"] += 1
                            diagnostics["prices_ingested"] += prices_ingested
                            diagnostics["markets_full_history"] = diagnostics.get("markets_full_history", 0) + 1
                            logger.info(f"Market {market_id}: Ingested {prices_ingested} prices (Strategy 1 chunked)")
                            # Backfill token IDs into DB so next run uses DB path (full history)
                            if (collected_yes_tid or collected_no_tid) and self.db:
                                try:
                                    await self.db.update_market_token_ids(str(market_id), collected_yes_tid, collected_no_tid)
                                except Exception as e:
                                    logger.debug("token ID backfill failed for market %s: %s", market_id, e)
                            continue
                        except Exception as e:
                            logger.warning(f"Bulk insert prices failed for market {market_id}: {e}")
                            price_data_batch = []
                    
                    # Strategy 2: Fallback to Orderbook API (current price) — snapshot only, not full history
                    strategy_2_result = {"success": False, "reason": None, "prices_ingested": 0}
                    if prices_ingested == 0:
                        try:
                            if isinstance(market, dict):
                                tokens, token_diagnostics = self._extract_tokens_from_market(market, market_id)
                                if not tokens:
                                    strategy_2_result["reason"] = f"No tokens found: {token_diagnostics.get('error', 'unknown')}"
                                elif tokens:
                                    token_id_str = self._extract_token_id(tokens[0])  # Use first token (usually YES)
                                    if token_id_str:
                                        logger.debug(f"Trying orderbook API for market {market_id}, token {token_id_str}")
                                        
                                        orderbook = await self.client.get_orderbook(market_id, token_id_str)
                                        
                                        if orderbook:
                                            # Calculate midpoint price from orderbook
                                            bids = orderbook.get("bids", [])
                                            asks = orderbook.get("asks", [])
                                            
                                            if bids and asks:
                                                try:
                                                    best_bid = float(bids[0].get("price", 0))
                                                    best_ask = float(asks[0].get("price", 0))
                                                    midpoint_price = (best_bid + best_ask) / 2.0
                                                    
                                                    if 0 <= midpoint_price <= 1:
                                                        await self.db.save_market_price(
                                                            market_id=str(market_id),
                                                            token_id=token_id_str,
                                                            price=midpoint_price,
                                                            timestamp=_naive_utc_ts(datetime.now(timezone.utc)),
                                                            side=None
                                                        )
                                                        prices_ingested += 1
                                                        strategy_2_result["success"] = True
                                                        strategy_2_result["prices_ingested"] = prices_ingested
                                                        logger.info(f"Market {market_id}: Got current price from orderbook: {midpoint_price}")
                                                except (ValueError, IndexError, KeyError) as e:
                                                    strategy_2_result["reason"] = f"Failed to calculate midpoint: {e}"
                                                    logger.debug(f"Failed to calculate midpoint from orderbook: {e}")
                                else:
                                    strategy_2_result["reason"] = "Market data is not a dict"
                        except Exception as e:
                            strategy_2_result["reason"] = f"Exception: {str(e)}"
                            logger.debug(f"Orderbook API fallback failed for market {market_id}: {str(e)}")
                    
                    # Update diagnostics with strategy 2 result
                    if strategy_2_result["success"]:
                        diagnostics["markets_successful"] += 1
                        diagnostics["markets_snapshot_only"] = diagnostics.get("markets_snapshot_only", 0) + 1
                        diagnostics["prices_ingested"] += strategy_2_result["prices_ingested"]
                        continue  # Snapshot only — not full history
                    
                    # Strategy 2.5: Use current price from market data as last API resort
                    strategy_2_5_result = {"success": False, "reason": None, "prices_ingested": 0}
                    if prices_ingested == 0 and isinstance(market, dict):
                        try:
                            # Try to get current price from market data
                            outcome_prices = market.get('outcomePrices') or market.get('outcome_prices')
                            if isinstance(outcome_prices, str):
                                try:
                                    outcome_prices = json.loads(outcome_prices)
                                except (json.JSONDecodeError, TypeError):
                                    outcome_prices = None
                            if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) > 0:
                                current_price = float(outcome_prices[0])
                                if 0 <= current_price <= 1:
                                    # Get token ID if available
                                    tokens, token_diagnostics = self._extract_tokens_from_market(market, market_id)
                                    token_id_str = None
                                    if tokens:
                                        token_id_str = self._extract_token_id(tokens[0])
                                    
                                    await self.db.save_market_price(
                                        market_id=str(market_id),
                                        token_id=token_id_str or "current",
                                        price=current_price,
                                        timestamp=_naive_utc_ts(datetime.now(timezone.utc)),
                                        side=None
                                    )
                                    prices_ingested += 1
                                    strategy_2_5_result["success"] = True
                                    strategy_2_5_result["prices_ingested"] = prices_ingested
                                    logger.info(f"Market {market_id}: Used current price from market data: {current_price}")
                                else:
                                    strategy_2_5_result["reason"] = "Price out of valid range [0,1]"
                            else:
                                strategy_2_5_result["reason"] = "No outcomePrices found in market data"
                        except (ValueError, TypeError, KeyError) as e:
                            strategy_2_5_result["reason"] = f"Exception: {e}"
                            logger.debug(f"Failed to extract current price from market data: {e}")
                    else:
                        strategy_2_5_result["reason"] = "Market data is not a dict"
                    
                    # Update diagnostics with strategy 2.5 result
                    if strategy_2_5_result["success"]:
                        diagnostics["markets_successful"] += 1
                        diagnostics["markets_snapshot_only"] = diagnostics.get("markets_snapshot_only", 0) + 1
                        diagnostics["prices_ingested"] += strategy_2_5_result["prices_ingested"]
                        continue  # Snapshot only — not full history
                    
                    # V2 CLEANUP: Strategy 3 (Blockchain) and Strategy 4 (FPMM) have been completely removed
                    # Polymarket V2 uses CLOB (Central Limit Order Book) only - no blockchain/FPMM queries needed
                    # All price ingestion now uses API-based methods (Strategy 1, 2, 2.5)
                    
                    if prices_ingested == 0:
                        failure_reasons = [
                            "strategy_1: no tokens or no chunked history",
                            f"strategy_2: {strategy_2_result.get('reason', 'unknown')}",
                            f"strategy_2_5: {strategy_2_5_result.get('reason', 'unknown')}",
                        ]
                        diagnostics["markets_no_events"] += 1
                        diagnostics["errors"].append(
                            f"Market {market_id}: All strategies failed. Reasons: {'; '.join(failure_reasons)}"
                        )
                        logger.warning(
                            f"Market {market_id}: All price ingestion strategies failed",
                            strategy_2=strategy_2_result,
                            strategy_2_5=strategy_2_5_result,
                        )
                
                except Exception as e:
                    logger.error(
                        f"Error processing market {market_id}: {str(e)}",
                        exc_info=True
                    )
                    diagnostics["markets_failed"] += 1
                    diagnostics["errors"].append(f"Market {market_id}: {str(e)}")
            
            # Finalize progress
            self.ingestion_progress["status"] = "completed"
            self.ingestion_progress["db_saved"] = diagnostics["prices_ingested"]
            
            logger.info(
                "Historical price ingestion completed (API-first approach)",
                **diagnostics
            )
            
            return {
                "success": True,
                "diagnostics": diagnostics,
                "message": f"Ingested {diagnostics['prices_ingested']} prices from {diagnostics['markets_successful']} markets"
            }
            
        except Exception as e:
            logger.error(f"Historical price ingestion failed: {str(e)}", exc_info=True)
            self.ingestion_progress["status"] = "error"
            self.ingestion_progress["error_message"] = str(e)
            
            return {
                "success": False,
                "error": str(e),
                "diagnostics": diagnostics
            }