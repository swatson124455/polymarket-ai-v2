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

# S245 CB-hardening: error signatures that are deterministic ORDER rejections
# ("this specific order is bad") rather than CLOB-health failures. The circuit
# breaker must stay NEUTRAL on these — retrying or escalating to the kill switch
# can't fix a malformed/closed-market order, and a run of them must not halt all
# trading (the S245 FOK regression: `invalid amounts` / `invalid token id` 400s
# escalated the CB -> in-process kill switch). Mirrors order_gateway._PERMANENT_PATTERNS
# (the no-retry set). Transient/unknown failures still count toward the breaker.
_CB_NEUTRAL_ORDER_REJECTIONS = (
    "invalid", "market closed", "delisted", "expired", "cancelled",
    "not found", "insufficient",
)


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
        escalation_threshold: int = 10,
        escalation_cooldown_seconds: float = 1800.0,
        max_consecutive_escalations: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0
        # S230 Bug 17 (2026-05-27): in-process kill-switch escalation.
        # Counts consecutive HALF_OPEN → OPEN re-openings since the last
        # success. When this hits escalation_threshold, the breaker
        # "escalates": allow_request() returns False for the next
        # escalation_cooldown_seconds regardless of state. After cooldown,
        # the escalation auto-clears and the normal HALF_OPEN probe cycle
        # resumes — if the underlying issue persists, re-escalation fires
        # on the next pattern detection.
        # Bug 17 addresses the gap surfaced in S230 live re-flip: 13+
        # consecutive re-opens under Bug 16's NO → SELL pattern with no
        # automatic escalation; the CircuitBreaker throttled the API but
        # the bot kept attempting orders forever with no rollback.
        self.escalation_threshold = escalation_threshold
        self.escalation_cooldown_seconds = escalation_cooldown_seconds
        self.consecutive_reopens = 0
        self.escalated = False
        self.escalated_at: float = 0.0
        # WI-7 (S235): PERMANENT_HALT terminal state.
        # Bug 17 escalation auto-clears after 30 min but re-escalates
        # immediately on the next probe failure (consecutive_reopens stays
        # at threshold). Without a ceiling this produces "keep firing every
        # 30 minutes forever" — the anti-pattern WI-7 targets.
        # After max_consecutive_escalations consecutive escalations without
        # an intervening success, the breaker enters PERMANENT_HALT:
        # allow_request() returns False indefinitely. Only explicit reset()
        # clears it — requires operator action (restart or direct call).
        self.max_consecutive_escalations = max_consecutive_escalations
        self.consecutive_escalation_count = 0
        self.permanently_halted = False

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        now = time.monotonic()
        # WI-7: PERMANENT_HALT check — fires before all other logic.
        # Once permanently halted, only reset() clears the state.
        if self.permanently_halted:
            return False
        # S230 Bug 17: escalation gate fires BEFORE the normal state machine.
        # If escalated, block all requests until cooldown elapses; auto-clear
        # then falls through to the existing CLOSED/OPEN/HALF_OPEN logic so
        # the breaker can probe and confirm recovery (or re-escalate).
        if self.escalated:
            if (now - self.escalated_at) >= self.escalation_cooldown_seconds:
                logger.info(
                    "Circuit breaker escalation auto-cleared",
                    elapsed_seconds=round(now - self.escalated_at, 1),
                    consecutive_reopens_at_engage=self.consecutive_reopens,
                )
                self.escalated = False
                # Leave state and consecutive_reopens as-is; next probe via
                # HALF_OPEN will exercise recovery. If it fails, record_failure
                # will detect the pattern and re-escalate.
            else:
                return False
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            elapsed = now - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = self.HALF_OPEN
                self.half_open_calls = 0
                logger.info("Circuit breaker half-open, allowing probe request")
                return True
            return False
        # HALF_OPEN
        return self.half_open_calls < self.half_open_max_calls

    def record_success(self) -> None:
        """Record a successful call. Resets breaker to CLOSED.

        S230 Bug 17: also resets consecutive_reopens — a single successful
        request indicates the system is healthy enough to count as recovery.
        Does NOT auto-clear self.escalated; only the cooldown timer clears
        escalation, so a single noise-success can't unwedge a pathological
        bot before the 30-min observation window completes.

        WI-7: also resets consecutive_escalation_count — a genuine success
        means the structural issue resolved, so the PERMANENT_HALT counter
        should reset from scratch.
        """
        if self.state == self.HALF_OPEN:
            logger.info("Circuit breaker closing (probe succeeded)")
        self.state = self.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0
        self.consecutive_reopens = 0
        self.consecutive_escalation_count = 0  # WI-7: reset on recovery

    def record_failure(self) -> None:
        """Record a failed call. Opens breaker if threshold exceeded.

        S230 Bug 17: track consecutive HALF_OPEN → OPEN re-openings. When
        the count hits escalation_threshold, engage in-process kill switch.
        """
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == self.HALF_OPEN:
            self.state = self.OPEN
            self.consecutive_reopens += 1
            logger.warning(
                "Circuit breaker re-opened (probe failed)",
                failures=self.failure_count,
                consecutive_reopens=self.consecutive_reopens,
            )
            # Bug 17 escalation trigger
            if (self.consecutive_reopens >= self.escalation_threshold
                    and not self.escalated):
                self._engage_escalation()
        elif self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            logger.warning(
                "Circuit breaker opened",
                failures=self.failure_count,
                cooldown_seconds=self.cooldown_seconds,
            )

    def _engage_escalation(self) -> None:
        """S230 Bug 17: escalate from API throttling to in-process kill switch.

        Triggered after escalation_threshold consecutive HALF_OPEN → OPEN
        re-openings without a single intervening success. This pattern
        indicates a structural failure (wrong-side orders, wallet not
        provisioned, contract drift, etc.) that the cooldown-and-retry loop
        cannot resolve on its own.

        Behavior: blocks all allow_request() calls for
        escalation_cooldown_seconds (default 30 min). The block is
        in-process — no DB write, no env mutation, no service restart.
        Auto-clears after cooldown to allow recovery from transient causes;
        if the issue persists, the next probe re-triggers escalation.

        WI-7 (S235): PERMANENT_HALT terminal state. After
        max_consecutive_escalations consecutive escalations without an
        intervening success, transitions to permanently_halted=True.
        No auto-clear; requires explicit reset() (operator action: restart
        service or direct call). Prevents the "keep escalating every 30 min
        forever" anti-pattern when the underlying fault is structural.

        Operator visibility: CRITICAL log with enough context to identify
        the failure pattern and decide whether to leave auto-clear or
        intervene (e.g., flip to paper, restart service, ship a fix).
        """
        self.escalated = True
        self.escalated_at = time.monotonic()
        self.consecutive_escalation_count += 1  # WI-7
        if self.consecutive_escalation_count >= self.max_consecutive_escalations:
            self.permanently_halted = True
            logger.critical(
                "Circuit breaker PERMANENT_HALT — operator action required",
                consecutive_escalations=self.consecutive_escalation_count,
                consecutive_reopens=self.consecutive_reopens,
                failure_count=self.failure_count,
                reason="structural_failure_max_escalations_reached",
                action=(
                    "ALL order placement permanently blocked. "
                    "Call circuit_breaker.reset() or restart the service to clear. "
                    "Do NOT clear without diagnosing the root cause."
                ),
            )
        else:
            logger.critical(
                "Circuit breaker escalated to in-process kill switch",
                consecutive_reopens=self.consecutive_reopens,
                consecutive_escalations=self.consecutive_escalation_count,
                max_escalations=self.max_consecutive_escalations,
                failure_count=self.failure_count,
                cooldown_seconds=self.escalation_cooldown_seconds,
                reason="pathological_failure_pattern",
                action="all live order placement blocked; auto-clear after cooldown",
            )

    def reset(self) -> None:
        """WI-7: Operator-action reset — clears PERMANENT_HALT and all counters.

        Only call this after diagnosing and resolving the root cause. Calling
        reset() without a fix re-starts the failure→escalation cycle.
        Logs a WARNING so the reset is always visible in journalctl.
        """
        logger.warning(
            "Circuit breaker reset by operator — all counters cleared",
            was_permanently_halted=self.permanently_halted,
            consecutive_escalations_at_reset=self.consecutive_escalation_count,
            consecutive_reopens_at_reset=self.consecutive_reopens,
        )
        self.permanently_halted = False
        self.consecutive_escalation_count = 0
        self.consecutive_reopens = 0
        self.escalated = False
        self.escalated_at = 0.0
        self.state = self.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0


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
            # S245 §0: Pre-trade pUSD balance check (live path only, non-blocking on
            # unexpected error). Replaces the S120 USDC.e@EOA check, which read the WRONG
            # token at the WRONG wallet — V2 collateral is pUSD at the deposit wallet, not
            # USDC.e at the EOA (WI-24: getCollateral() on both V2 exchanges == pUSD 0xC011;
            # the sibling approval step was already V2-fixed in S228 Bug 8, this balance
            # check was the one site that missed it). Reuses the WI-24 accessor
            # check_pusd_balance(); the deposit wallet it reads IS the CLOB order funder
            # (clob_adapter funder=DEPOSIT_WALLET_ADDRESS, signature_type=3). Function-local
            # import matches base_engine.py:1488 / bankroll_manager.py:184 and lets tests
            # patch clob_adapter.check_pusd_balance.
            if self.contract_manager and not getattr(settings, "SIMULATION_MODE", True):
                try:
                    from base_engine.execution.clob_adapter import check_pusd_balance
                    _pusd_bal = await check_pusd_balance()  # pUSD @ deposit wallet (order funder)
                    _cost = size * price
                    if _pusd_bal is None:
                        # Read failure (RPC/config) — NOT a zero balance, which returns 0.0.
                        # Fail closed: skip this attempt rather than submit unverified funding
                        # (the skip-on-stale principle of the S244 price-freshness fix). The
                        # error has no _PERMANENT_PATTERNS substring, so the gateway retry loop
                        # re-checks (recovers a transient RPC blip) and otherwise the signal
                        # re-evaluates next scan. Distinct log makes the None-rate observable.
                        logger.warning("pretrade_pusd_check_unavailable",
                                       bot_name=bot_name, market_id=str(market_id))
                        return {"success": False,
                                "error": "pUSD balance unavailable (read failed); skipping order"}
                    if _pusd_bal < _cost:
                        # Genuine shortfall. "insufficient" is in _PERMANENT_PATTERNS → no
                        # in-scan retry (balance can't change within the 1s/2s/4s retry window);
                        # the signal still re-evaluates next scan when capital changes (redeem /
                        # position close). Retryable here would only hammer an unchanged balance.
                        return {
                            "success": False,
                            "error": f"Insufficient pUSD: need ${_cost:.2f}, have ${_pusd_bal:.2f}",
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

            # Circuit breaker: reject immediately if CLOB API is known-down.
            # S230 Bug 17: differentiate normal CB open from escalation block so
            # operator log analysis can distinguish "API hiccup" from
            # "pathological failure pattern caught by in-process kill switch."
            if not self.circuit_breaker.allow_request():
                if self.circuit_breaker.escalated:
                    _cooldown = int(self.circuit_breaker.escalation_cooldown_seconds)
                    _err = (
                        f"Circuit breaker ESCALATED — in-process kill switch active "
                        f"(consecutive_reopens={self.circuit_breaker.consecutive_reopens}, "
                        f"auto-clear in up to {_cooldown}s)"
                    )
                else:
                    _err = (
                        f"Circuit breaker OPEN — CLOB API temporarily unavailable, "
                        f"retrying in {int(self.circuit_breaker.cooldown_seconds)}s"
                    )
                return {"success": False, "error": _err}

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
                    elif isinstance(order_result, dict) and order_result.get("not_filled"):
                        # S245 #1: a marketable FOK that didn't fill (no liquidity at the
                        # limit) is a benign MARKET outcome, NOT a CLOB-health failure.
                        # Recording it as a failure would let a run of wide-spread misses
                        # OPEN the circuit breaker and halt ALL execution (the kill-switch
                        # storm class — S230 Bug 17 / S244 Bug A). Leave the breaker untouched.
                        pass
                    else:
                        # S245 CB-hardening: the breaker tracks CLOB HEALTH, not order
                        # VALIDITY. A deterministic order rejection (invalid amounts/token/
                        # price, market closed, delisted, expired, cancelled, insufficient)
                        # is THIS order being bad — escalating to the kill switch can't help
                        # and a run of bad orders must not halt all trading. CB-neutral on
                        # those; transient/unknown failures still count toward escalation.
                        _err = str(order_result.get("error", "")).lower() if isinstance(order_result, dict) else ""
                        if not any(_p in _err for _p in _CB_NEUTRAL_ORDER_REJECTIONS):
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
