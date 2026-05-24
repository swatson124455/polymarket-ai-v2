import asyncio
import time
from typing import Dict, Optional, Any
from datetime import datetime, timezone
from eth_account import Account
from structlog import get_logger
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.risk.risk_manager import RiskManager
from base_engine.data.database import Database
from base_engine.execution.contract_manager import ContractManager
from base_engine.utils.validation import validate_price, validate_size, validate_market_id
from base_engine.exceptions import OrderPlacementError, RiskCheckError
from config.settings import settings

logger = get_logger()


class CircuitBreaker:
    """
    Circuit breaker for CLOB API calls.

    States:
      CLOSED  - normal operation, requests pass through
      OPEN    - too many failures, all requests rejected immediately
      HALF_OPEN - after cooldown, allow one probe request

    Prevents hammering a down API and lets it recover.
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = self.HALF_OPEN
                self.half_open_calls = 0
                logger.info("Circuit breaker half-open, allowing probe request")
                return True
            return False
        # HALF_OPEN
        return self.half_open_calls < self.half_open_max_calls

    def record_success(self) -> None:
        """Record a successful call. Resets breaker to CLOSED."""
        if self.state == self.HALF_OPEN:
            logger.info("Circuit breaker closing (probe succeeded)")
        self.state = self.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0

    def record_failure(self) -> None:
        """Record a failed call. Opens breaker if threshold exceeded."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == self.HALF_OPEN:
            self.state = self.OPEN
            logger.warning("Circuit breaker re-opened (probe failed)", failures=self.failure_count)
        elif self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            logger.warning(
                "Circuit breaker opened",
                failures=self.failure_count,
                cooldown_seconds=self.cooldown_seconds,
            )


class ExecutionEngine:
    def __init__(
        self,
        client: PolymarketClient,
        risk_manager: RiskManager,
        db: Database,
        private_key: Optional[str] = None,
        kill_switch: Optional[Any] = None,
    ) -> None:
        self.client = client
        self.risk_manager = risk_manager
        self.db = db
        self.kill_switch = kill_switch
        self.account = None
        self.clob_adapter = None  # Set by BaseEngine if CLOB creds available
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            cooldown_seconds=60.0,
        )
        pk = (private_key or "").strip() or settings.PRIVATE_KEY
        if pk:
            self.account = Account.from_key(pk)
            self.contract_manager = ContractManager(private_key=pk)
            logger.info("Execution engine initialized with wallet and contract manager")
        else:
            logger.warning("No private key configured - execution engine in read-only mode")
            self.contract_manager = None

    def set_kill_switch(self, kill_switch: Optional[Any]) -> None:
        """Set kill switch after init (BaseEngine creates it asynchronously)."""
        self.kill_switch = kill_switch

    async def place_order(
        self,
        bot_name: str,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        confidence: float,
        skip_position_update: bool = False,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # NOTE: Kill switch + risk limits are checked in OrderGateway *before* this.
        # We only keep input validation and wallet check here to avoid duplicate DB queries.
        if self.kill_switch is not None:
            if await self.kill_switch.is_engaged():
                logger.warning("Order blocked: kill switch engaged", bot_name=bot_name, market_id=market_id)
                return {"success": False, "error": "Kill switch engaged"}
        try:
            market_id = validate_market_id(market_id)
            price = validate_price(price, "price")
            size = validate_size(size, "size")
            
            if side not in ["YES", "NO", "BUY", "SELL"]:
                return {
                    "success": False,
                    "error": f"Invalid side: {side}. Must be 'YES', 'NO', 'BUY', or 'SELL'"
                }
            
            if not token_id or not isinstance(token_id, str):
                return {
                    "success": False,
                    "error": f"Invalid token_id: {token_id}"
                }
        except ValueError as e:
            return {
                "success": False,
                "error": f"Validation failed: {str(e)}"
            }
        
        # Risk limits already checked by OrderGateway (skip_position_update=True indicates Gateway path).
        # Only run risk check when called directly (non-Gateway path) to avoid duplicate DB round-trips.
        if not skip_position_update:
            risk_check = await self.risk_manager.check_risk_limits(
                bot_name, market_id, size, price, confidence
            )
            if not risk_check["allowed"]:
                return {
                    "success": False,
                    "error": "Risk limits exceeded",
                    "reasons": risk_check["reasons"]
                }
        
        if not self.account:
            return {
                "success": False,
                "error": "No wallet configured"
            }
        
        try:
            # S120: Pre-trade USDC balance check (live path only, non-blocking on failure)
            if self.contract_manager and not getattr(settings, "SIMULATION_MODE", True):
                try:
                    _bal = await self.contract_manager.get_usdce_balance()
                    if _bal.get("success"):
                        _cost = size * price
                        if _bal["balance_usd"] < _cost:
                            return {
                                "success": False,
                                "error": f"Insufficient USDC: need ${_cost:.2f}, have ${_bal['balance_usd']:.2f}",
                            }
                except Exception as _bal_err:
                    logger.debug("Pre-trade balance check failed (non-blocking): %s", _bal_err)

            # Phase 6: approval timeout (colocated = shorter)
            approval_timeout = (
                getattr(settings, "ORDER_EXECUTION_TIMEOUT_COLOCATED", 10)
                if getattr(settings, "SPEED_PROFILE", "default") == "colocated"
                else 30.0
            )
            if self.contract_manager:
                if side in ["BUY", "YES", "NO"]:
                    # BUG FIX: Validate calculation doesn't overflow and add error handling
                    # Root cause: size * price calculation might overflow or result in invalid values
                    # Impact: Contract operations fail with cryptic errors
                    # Fix: Validate calculation result and add comprehensive error handling
                    try:
                        size_usd = size * price
                        
                        # Validate the calculated USD amount
                        import math
                        if math.isnan(size_usd) or math.isinf(size_usd) or size_usd <= 0:
                            return {
                                "success": False,
                                "error": f"Invalid USD amount calculated: {size_usd} (size={size}, price={price})"
                            }
                        
                        # Check for reasonable limits (e.g., not more than total capital)
                        if size_usd > settings.TOTAL_CAPITAL:
                            return {
                                "success": False,
                                "error": f"Order size ${size_usd:.2f} exceeds total capital ${settings.TOTAL_CAPITAL:.2f}"
                            }
                    except (OverflowError, ValueError) as calc_error:
                        return {
                            "success": False,
                            "error": f"Error calculating order value: {str(calc_error)}"
                        }
                    
                    # BUG FIX: Add timeout and comprehensive error handling for contract operations
                    try:
                        usdce_approval = await asyncio.wait_for(
                            self.contract_manager.ensure_usdce_approved(amount_usd=size_usd),
                            timeout=float(approval_timeout)
                        )
                    except asyncio.TimeoutError:
                        return {
                            "success": False,
                            "error": f"USDCe approval timeout ({approval_timeout}s) - blockchain may be congested"
                        }
                    except Exception as approval_error:
                        return {
                            "success": False,
                            "error": f"USDCe approval failed: {str(approval_error)}"
                        }
                    if not usdce_approval.get("success") and not usdce_approval.get("already_approved"):
                        logger.warning("USDCe approval failed", error=usdce_approval.get("error"))
                        return {
                            "success": False,
                            "error": f"USDCe approval failed: {usdce_approval.get('error')}"
                        }
                
                elif side == "SELL":
                    # BUG FIX: Add same timeout and error handling for token approval
                    # Root cause: Token approval can also hang or fail
                    # Impact: SELL orders get stuck
                    # Fix: Add timeout and comprehensive error handling
                    try:
                        token_approval = await asyncio.wait_for(
                            self.contract_manager.ensure_outcome_token_approved(token_id, amount_tokens=size),
                            timeout=float(approval_timeout)
                        )
                    except asyncio.TimeoutError:
                        return {
                            "success": False,
                            "error": f"Token approval timeout ({approval_timeout}s) - blockchain may be congested"
                        }
                    except Exception as approval_error:
                        return {
                            "success": False,
                            "error": f"Token approval failed: {str(approval_error)}"
                        }
                    
                    if not token_approval.get("success") and not token_approval.get("already_approved"):
                        logger.warning("Outcome token approval failed", error=token_approval.get("error"))
                        return {
                            "success": False,
                            "error": f"Outcome token approval failed: {token_approval.get('error')}"
                        }
            
            # Final kill switch check right before API call (prevents race with delayed approvals)
            if self.kill_switch is not None and await self.kill_switch.is_engaged():
                return {"success": False, "error": "Kill switch engaged (pre-execution)"}

            # Circuit breaker: reject immediately if CLOB API is known-down
            if not self.circuit_breaker.allow_request():
                return {
                    "success": False,
                    "error": "Circuit breaker OPEN — CLOB API temporarily unavailable, retrying in %ds" % int(self.circuit_breaker.cooldown_seconds),
                }

            # Phase 3: retry on transient failures (timeout, 5xx, rate limit). Phase 6: colocated uses fewer retries.
            profile = getattr(settings, "SPEED_PROFILE", "default")
            max_retries = (
                getattr(settings, "MAX_RETRIES_COLOCATED", 2)
                if profile == "colocated"
                else getattr(settings, "EXECUTION_ENGINE_MAX_RETRIES", 2)
            )
            last_error: Optional[str] = None
            order_result = None
            for attempt in range(max_retries + 1):
                try:
                    if self.clob_adapter and self.clob_adapter.available:
                        order_result = await self.clob_adapter.place_order(
                            market_id=market_id,
                            token_id=token_id,
                            side=side,
                            size=size,
                            price=price,
                        )
                    else:
                        async with self.client:
                            order_result = await self.client.place_order(
                                market_id=market_id,
                                token_id=token_id,
                                side=side,
                                size=size,
                                price=price
                            )
                    # S150: Check for retryable dict responses (e.g. HTTP 425 from async_clob_client).
                    # These don't raise — they return {"success": False, "retryable": True}.
                    if (isinstance(order_result, dict)
                            and not order_result.get("success")
                            and order_result.get("retryable")
                            and attempt < max_retries):
                        self.circuit_breaker.record_failure()
                        last_error = order_result.get("error", "retryable error")
                        delay = min(0.1 * (2 ** attempt), 2.0)
                        await asyncio.sleep(delay)
                        continue
                    # S228 Bug 10: distinguish actual success vs non-retryable
                    # failure for circuit-breaker state. Pre-fix, any break-
                    # path called record_success() — including non-retryable
                    # CLOB failures — which incorrectly cleared prior failures.
                    if isinstance(order_result, dict) and order_result.get("success"):
                        self.circuit_breaker.record_success()
                    else:
                        self.circuit_breaker.record_failure()
                    break
                except asyncio.TimeoutError as e:
                    self.circuit_breaker.record_failure()
                    last_error = str(e)
                    if attempt < max_retries:
                        delay = min(0.1 * (2 ** attempt), 2.0)
                        await asyncio.sleep(delay)
                        continue
                    return {"success": False, "error": f"Order timeout after {max_retries + 1} attempts: {last_error}"}
                except Exception as e:
                    err_str = str(e).lower()
                    is_transient = "timeout" in err_str or "429" in err_str or "503" in err_str or "502" in err_str or "connection" in err_str
                    if is_transient:
                        self.circuit_breaker.record_failure()
                    if attempt < max_retries and is_transient:
                        last_error = str(e)
                        delay = min(0.1 * (2 ** attempt), 2.0)
                        await asyncio.sleep(delay)
                        continue
                    raise

            if not order_result or not isinstance(order_result, dict):
                logger.error("Invalid order result from API", result=str(order_result)[:200])
                return {
                    "success": False,
                    "error": "Invalid order result from API"
                }

            # S228 Bug 10: CLOB-side failures must surface as failure, not as
            # "Order placed" with order_id=None. Pre-fix, a non-retryable
            # {success: False, error: ...} response broke the retry loop and
            # fell through to the success path — caller received {success:
            # True, order_id: None} and could not tell the order failed.
            # Surfaced S228 live flip #3 when Bug 9's AsyncClobClient produced
            # 4 distinct "CLOB client or request build failed" responses, each
            # logged as a fake "Order placed" event.
            if not order_result.get("success"):
                _err = order_result.get("error", "unknown CLOB failure")
                logger.warning(
                    "order_placement_failed",
                    bot_name=bot_name,
                    market_id=market_id,
                    side=side,
                    size=size,
                    price=price,
                    error=_err,
                )
                return {
                    "success": False,
                    "error": _err,
                    "market_id": market_id,
                    "side": side,
                }

            order_id = order_result.get("id") or order_result.get("order_id")
            if not order_id:
                logger.warning("Order placed but no order_id returned", result_keys=list(order_result.keys()))
            
            # Skip when OrderGateway handles position via TradeCoordinator.confirm_position
            if not skip_position_update:
                await self.risk_manager.update_position(
                    bot_name=bot_name,
                    market_id=market_id,
                    token_id=token_id,
                    side=side,
                    size=size,
                    price=price
                )
            
            logger.info(
                "Order placed",
                bot_name=bot_name,
                market_id=market_id,
                side=side,
                size=size,
                price=price,
                order_id=order_id,
            )
            
            return {
                "success": True,
                "order_id": order_id,
                "market_id": market_id,
                "side": side,
                "size": size,
                "price": price,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        except Exception as e:
            logger.error("Order placement failed", bot_name=bot_name, market_id=market_id, error=str(e), exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    async def close_position(
        self,
        bot_name: str,
        market_id: str,
        exit_price: float
    ) -> Dict[str, Any]:
        position_result = await self.risk_manager.close_position(
            bot_name=bot_name,
            market_id=market_id,
            exit_price=exit_price
        )
        
        if not position_result:
            return {
                "success": False,
                "error": "Position not found"
            }
        
        logger.info(
            "Position closed",
            bot_name=bot_name,
            market_id=market_id,
            pnl=round(position_result["pnl"], 2),
        )
        
        return {
            "success": True,
            **position_result
        }
