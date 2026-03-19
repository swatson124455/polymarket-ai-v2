"""
Paper Trading Mode
==================
Test strategies with real data, fake money.
Simulates order execution without real trades with realistic slippage.
When db is provided, each trade is persisted to paper_trades for resolution backfill and hypothetical P&L.
"""
import asyncio
import math
import random
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from structlog import get_logger
from config.settings import settings
from base_engine.features.market_impact import MarketImpactEstimator, DEFAULT_LAMBDA

logger = get_logger()


def _size_dependent_slippage_bps(order_size_usd: float, price: float = 0.5) -> int:
    """Tiered market-impact slippage model with boundary adjustment.

    Flat 50 bps is unrealistic: small orders face tight spreads, large orders
    move the book. Tiers calibrated to Polymarket CLOB typical depth.

    S95: Boundary multiplier — books thin dramatically near $0 and $1.
    At $0.50: 1.0x. At $0.10: ~1.2x. At $0.05: ~1.8x. At $0.02: ~3.5x.
    Capped at 5.0x to avoid extreme values at the very edges.

    < $50:    35 bps  — small retail, inside spread
    $50-200:  50 bps  — baseline (matches old flat rate)
    $200-500: 75 bps  — medium, liquidity thins
    > $500:  120 bps  — large, meaningful market impact
    """
    if order_size_usd < 50:
        base_bps = 35
    elif order_size_usd < 200:
        base_bps = 50
    elif order_size_usd < 500:
        base_bps = 75
    else:
        base_bps = 120

    # S95: Boundary multiplier — books are 2-5x thinner near $0 and $1
    # p*(1-p) peaks at 0.25 when p=0.50, approaches 0 at boundaries
    _p = max(0.01, min(0.99, price))
    boundary_mult = min(5.0, 1.0 + 0.02 / (_p * (1.0 - _p)))
    return int(base_bps * boundary_mult)


def _apply_slippage(price: float, side: str, slippage_bps: int) -> float:
    """
    Apply realistic slippage to a paper trade price.

    BUY orders get a worse (higher) price, SELL orders get a worse (lower) price.
    Adds random jitter (0-50% of slippage) to simulate variable market conditions.

    Args:
        price: Requested order price.
        side: 'BUY' or 'SELL'.
        slippage_bps: Slippage in basis points (e.g. 50 = 0.5%).

    Returns:
        Adjusted price clamped to [0.001, 0.999] (valid Polymarket range).
    """
    if slippage_bps <= 0:
        return price
    base_slip = slippage_bps / 10000.0
    # Random jitter: 50%-150% of base slippage to simulate variable conditions
    jitter = base_slip * (0.5 + random.random())
    if side == "BUY":
        adjusted = price + jitter
    else:
        adjusted = price - jitter
    return max(0.001, min(0.999, adjusted))


def _time_of_day_liquidity_mult() -> float:
    """S95: Time-of-day liquidity multiplier.

    Polymarket books are thickest during US+EU overlap (13:00-17:00 UTC)
    and thinnest during Asian late-night (21:00-02:00 UTC).
    Weekend factor: 0.6x depth vs weekday peak.

    Returns multiplier in [0.35, 1.0] applied to fill probability.
    """
    now = datetime.now(timezone.utc)
    hour = now.hour
    weekday = now.weekday()  # 0=Mon, 6=Sun

    # Hourly liquidity curve (UTC). Peak = 1.0 during US/EU overlap.
    _hourly = {
        0: 0.55, 1: 0.50, 2: 0.50, 3: 0.55, 4: 0.60, 5: 0.65,
        6: 0.70, 7: 0.75, 8: 0.80, 9: 0.85, 10: 0.90, 11: 0.95,
        12: 0.95, 13: 1.00, 14: 1.00, 15: 1.00, 16: 1.00, 17: 0.95,
        18: 0.90, 19: 0.85, 20: 0.80, 21: 0.70, 22: 0.65, 23: 0.60,
    }
    mult = _hourly.get(hour, 0.70)

    # Weekend discount: ~40% less depth
    if weekday >= 5:
        mult *= 0.60

    return mult


def _sqrt_market_impact_bps(order_size_usd: float, volume_24h: float, price: float) -> float:
    """S95: Square-root market impact model.

    ΔP = Y × σ × √(Q/V) where Y=2.0 (thin prediction market calibration),
    σ=0.05 (daily vol proxy for prediction markets).
    Returns additional slippage in basis points.

    Only meaningful for larger orders on thinner markets. For a $100 order
    on a $500K/day market, impact is ~0.6 bps (negligible). For a $500 order
    on a $10K/day market, impact is ~141 bps (significant).
    """
    if volume_24h <= 0:
        volume_24h = 50000.0  # S100: realistic fallback (median Polymarket market) for zero-volume markets
    _Y = 2.0      # market impact coefficient (1.5-3.0 for thin markets)
    _sigma = 0.05  # daily volatility proxy
    participation = order_size_usd / max(volume_24h, 1.0)
    impact = _Y * _sigma * math.sqrt(participation)
    return impact * 10000  # convert to bps


def _fill_probability(
    price: float,
    order_size_usd: float,
    spread: float,
    volume_24h: float,
) -> float:
    """Estimate probability of order fill based on market microstructure.

    Five independent factors (multiplicative):
    1. Price-depth: books thin at extremes (0.05, 0.95), deep at 0.50
    2. Size-impact: larger orders relative to volume are harder to fill
    3. Spread: wider spread = less likely to fill at your price
    4. Time-of-day: off-peak hours and weekends have thinner books (S95)
    5. Square-root participation: high Q/V ratio reduces fill chance (S95)

    Returns float in [0.05, 1.0]. The 5% floor ensures no order is
    completely unfillable (real CLOB always has some crossing chance).
    """
    # Price-depth: parabola peaks at 0.50, zero at 0/1
    depth_at_price = 4.0 * price * (1.0 - price)
    price_factor = 0.3 + 0.7 * depth_at_price

    # Size-impact: ratio of order to daily volume
    if volume_24h <= 0:
        volume_24h = 50000.0  # S100: realistic fallback (median Polymarket market)
    size_ratio = order_size_usd / max(volume_24h, 1.0)
    size_factor = max(0.1, 1.0 - 2.0 * size_ratio)

    # Spread: wider spread = less fill probability
    spread_factor = max(0.2, 1.0 - spread * 5.0)

    # S95: Time-of-day liquidity (off-peak = harder to fill)
    tod_factor = _time_of_day_liquidity_mult()

    # S95: High participation rate reduces fill probability
    # sqrt(Q/V) > 0.1 starts to matter; > 0.5 = very hard to fill
    _participation = order_size_usd / max(volume_24h, 1.0)
    participation_factor = max(0.2, 1.0 - math.sqrt(_participation) * 0.5)

    fill_prob = price_factor * size_factor * spread_factor * tod_factor * participation_factor
    return max(0.05, min(1.0, fill_prob))


def _alpha_decay_factor(latency_ms: Optional[float], half_life_s: float = 300.0) -> float:
    """S95: Exponential alpha decay — signal degrades with latency.

    Replaces the S91 linear latency drift (10 bps/sec, 500ms threshold) with
    a more realistic exponential model: decay = exp(-ln2 * t / half_life).

    No threshold — applies proportionally to ALL latencies.
    Returns decay factor in [0, 1] where 1.0 = no decay, 0.0 = full decay.
    """
    if latency_ms is None or latency_ms <= 0 or half_life_s <= 0:
        return 1.0
    latency_s = latency_ms / 1000.0
    return math.exp(-math.log(2) * latency_s / half_life_s)


def _resolution_proximity_penalty(hours_to_resolution: Optional[float]) -> Tuple[float, float]:
    """S95: Resolution-proximity adverse selection.

    Near market resolution, informed flow dominates — wider spreads and
    lower fill rates as market makers widen to protect against insiders.

    Returns (slippage_multiplier, fill_probability_multiplier).
    """
    if hours_to_resolution is None or hours_to_resolution < 0:
        return (1.0, 1.0)
    if hours_to_resolution > 6.0:
        return (1.0, 1.0)
    if hours_to_resolution > 2.0:
        return (1.5, 0.9)
    if hours_to_resolution > 0.5:
        return (2.0, 0.7)
    return (3.0, 0.5)


def _vwap_from_book(
    asks: List[Dict],
    order_size_shares: float,
    whale_size_shares: float = 0.0,
) -> Optional[Tuple[float, float, float]]:
    """S100: Walk L2 order book to compute VWAP fill price.

    Subtracts whale's consumed liquidity first (the whale bought before us,
    depleting the ask side), then fills the copier from remaining depth.

    Args:
        asks: List of {"price": str|float, "size": str|float} sorted ascending by price.
        order_size_shares: Copier's order size in shares.
        whale_size_shares: Whale's trade size in shares (subtracted from book first).

    Returns:
        (vwap_price, fill_fraction, slippage_vs_best_ask) or None if no depth.
    """
    if not asks or order_size_shares <= 0:
        return None

    # Parse and sort ascending by price
    levels: List[Tuple[float, float]] = []
    for level in asks:
        try:
            p = float(level.get("price", level.get("p", 0)))
            s = float(level.get("size", level.get("s", 0)))
            if p > 0 and s > 0:
                levels.append((p, s))
        except (ValueError, TypeError, AttributeError):
            continue
    if not levels:
        return None
    levels.sort(key=lambda x: x[0])

    best_ask = levels[0][0]

    # Phase 1: Subtract whale's consumed shares from the book
    remaining_whale = max(0.0, whale_size_shares)
    post_whale_levels: List[Tuple[float, float]] = []
    for price, size in levels:
        if remaining_whale > 0:
            consumed = min(remaining_whale, size)
            remaining_whale -= consumed
            leftover = size - consumed
            if leftover > 0.001:  # skip dust
                post_whale_levels.append((price, leftover))
        else:
            post_whale_levels.append((price, size))

    if not post_whale_levels:
        return None

    # Phase 2: Walk remaining book to fill copier's order
    remaining_order = order_size_shares
    filled_shares = 0.0
    total_cost = 0.0
    for price, size in post_whale_levels:
        fill_at_level = min(remaining_order, size)
        filled_shares += fill_at_level
        total_cost += fill_at_level * price
        remaining_order -= fill_at_level
        if remaining_order <= 0.001:
            break

    if filled_shares <= 0:
        return None

    vwap = total_cost / filled_shares
    fill_fraction = filled_shares / order_size_shares
    slippage = vwap - best_ask

    return (vwap, min(1.0, fill_fraction), slippage)


class PaperTrade:
    """Simulated trade"""
    def __init__(
        self,
        trade_id: str,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        timestamp: datetime
    ):
        self.trade_id = trade_id
        self.market_id = market_id
        self.token_id = token_id
        self.side = side
        self.size = size
        self.price = price
        self.timestamp = timestamp
        self.filled = True  # Paper trades always fill immediately
        self.status = "filled"


class PaperTradingEngine:
    """
    Paper trading mode - test strategies with real data, fake money.
    When db is set, each trade is written to paper_trades for resolution backfill and hypothetical P&L.
    """
    
    def __init__(self, initial_capital: float = 10000.0, db: Optional[Any] = None):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[Tuple[str, str], Dict] = {}  # (bot_name, market_id) -> position
        self.trades: List[PaperTrade] = []
        self.pnl_history: List[Dict] = []
        self.enabled = False
        self.db = db  # Database instance for persisting to paper_trades
        # BUG-3 fix: Track realized P&L for DrawdownController integration
        self.realized_pnl_today: Dict[str, float] = {}  # Per-bot realized P&L for current day
        self._pnl_reset_date: Optional[datetime] = None  # Date of last reset
        # RL Trade Timing: callback fired on realized P&L (sell trades)
        self._rl_outcome_callback = None
        # Protect cash/positions/pnl from concurrent bot updates (race condition fix)
        import asyncio
        self._trade_lock = asyncio.Lock()
        # S94: In-memory idempotency set — checked under lock to prevent gap
        # between lock release and DB idempotency check on next trade
        self._pending_correlation_ids: set = set()
        # S94: DB write tasks queued under lock, executed after lock release
        self._pending_db_writes: list = []
        # S95: Kyle's lambda — wire existing MarketImpactEstimator for AS penalty
        self._market_impact_estimator: Optional[MarketImpactEstimator] = None
        self._kyle_lambda_cache: Dict[str, Tuple[float, float]] = {}  # market_id -> (lambda, mono_ts)
        # S95: Cross-scan cumulative impact tracker
        self._scan_impact: Dict[str, Tuple[float, float]] = {}  # market_id -> (cumulative_bps, mono_ts)
        # S100: L2 order book tracker for book walk fills (set by base_engine)
        self._orderbook_tracker = None
        # S106: Fill-failure cooldown — back off markets with consecutive fill rejections.
        # Dict[market_id -> (consecutive_failures, last_failure_monotonic)]
        self._fill_failure_tracker: Dict[str, Tuple[int, float]] = {}
    
    async def _get_kyle_lambda(self, market_id: str) -> float:
        """S95: Cached Kyle's lambda lookup (1h TTL). DEFAULT_LAMBDA on miss."""
        _now = time.monotonic()
        _cached = self._kyle_lambda_cache.get(market_id)
        if _cached and (_now - _cached[1]) < 3600.0:
            return _cached[0]
        if self._market_impact_estimator is None:
            self._market_impact_estimator = MarketImpactEstimator(db=self.db)
        try:
            lam = await self._market_impact_estimator.estimate_kyle_lambda(market_id)
        except Exception:
            lam = DEFAULT_LAMBDA
        self._kyle_lambda_cache[market_id] = (lam, _now)
        return lam

    async def seed_positions_from_db(self) -> int:
        """Sync open positions from DB into in-memory paper portfolio.

        The OrderGateway + PositionManager load open positions from DB at startup.
        If the paper engine doesn't have matching positions, SELL orders (edge depletion,
        model reversal) will fail with "Insufficient position". This method seeds the
        paper engine so exits succeed.

        Returns count of positions seeded.
        """
        # Idempotency guard: seed only once per process lifetime.
        # Double-seeding deducts cash twice (or more) causing silent negative balance.
        if getattr(self, "_positions_seeded", False):
            return 0
        self._positions_seeded = True
        if not self.db or not self.db.session_factory:
            return 0
        try:
            from sqlalchemy import select, func
            from base_engine.data.database import Position, PaperTradeRecord
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(Position).where(
                        Position.status == "open",
                        Position.side != "SELL",  # SELL rows = exit attempts, not open capital
                    )
                )
                positions = result.scalars().all()
                count = 0
                total_value = 0.0
                for pos in positions:
                    # Theoretical hardening: per-position try/except so one bad DB row
                    # doesn't abort the entire seeding loop and leave cash wrongly set.
                    try:
                        mid = str(pos.market_id) if pos.market_id else ""
                        if not mid:
                            continue
                        size = float(pos.size or 0)
                        entry_price = float(pos.entry_price or 0)
                        if size <= 0:
                            continue
                        # I21: Skip NULL/zero avg_price — would corrupt P&L math (0-price exit = fake profit)
                        if entry_price <= 0:
                            logger.warning(
                                "Paper trading: skipping position with NULL/zero avg_price",
                                market_id=mid, size=size,
                            )
                            continue
                        # Seed per (bot_name, market_id) — prevents cross-bot contamination
                        _bot_id = str(getattr(pos, 'bot_id', '') or 'unknown')
                        pos_key = (_bot_id, mid)
                        if pos_key not in self.positions:
                            self.positions[pos_key] = {
                                "size": size,
                                "avg_price": entry_price,
                                "token_id": pos.token_id or "",
                                "side": pos.side or "YES",
                            }
                            # Deduct the capital used for this position
                            cost = size * entry_price
                            self.cash -= cost
                            total_value += cost
                            count += 1
                    except Exception as _pos_err:
                        logger.warning("Paper trading: skipped bad position row (non-fatal): %s", _pos_err)

                # Restore realized P&L from paper_trades so cash survives restarts.
                # SUM ignores NULLs — historical rows without realized_pnl don't corrupt the total.
                pnl_row = await session.execute(
                    select(func.coalesce(func.sum(PaperTradeRecord.realized_pnl), 0.0))
                    .where(PaperTradeRecord.realized_pnl.isnot(None))
                )
                cumulative_realized_pnl: float = float(pnl_row.scalar() or 0.0)
                if cumulative_realized_pnl != 0.0:
                    self.cash += cumulative_realized_pnl

                # Reconciliation: detect markets with multiple bots
                _markets: Dict[str, list] = {}
                for (bn, mid) in self.positions:
                    _markets.setdefault(mid, []).append(bn)
                _overlaps = {mid: bots for mid, bots in _markets.items() if len(bots) > 1}
                if _overlaps:
                    logger.info("paper_position_overlaps_seeded", overlap_count=len(_overlaps),
                                overlaps={mid[:20]: bots for mid, bots in list(_overlaps.items())[:5]})

                logger.info(
                    "Paper trading: seeded %d open positions (value $%.2f, realized_pnl $%.2f, cash $%.2f)",
                    count, total_value, cumulative_realized_pnl, self.cash,
                )
                return count
        except Exception as e:
            logger.warning("Paper trading: position seed from DB failed (non-critical): %s", e)
            return 0

    def enable(self):
        """Enable paper trading mode"""
        self.enabled = True
        logger.info("Paper trading enabled", capital=self.initial_capital)
    
    def disable(self):
        """Disable paper trading mode"""
        self.enabled = False
        logger.info("Paper trading disabled")
    
    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        bot_name: str = "paper_trader",
        confidence: Optional[float] = None,
        original_side: Optional[str] = None,
        order_type: str = "market",
        correlation_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
        bid: float = 0.0,
        ask: float = 0.0,
        volume: float = 0.0,
        model_version: Optional[int] = None,
        model_name: Optional[str] = None,
        event_data: Optional[dict] = None,
    ) -> Dict:
        """
        Place a paper trade order.

        Args:
            market_id: Market ID
            token_id: Token ID
            side: "BUY" or "SELL"
            size: Order size in shares
            price: Order price

        Returns:
            Dict with order result
        """
        if not self.enabled:
            logger.warning("Paper trade rejected: not enabled", market_id=market_id)
            return {
                "success": False,
                "error": "Paper trading not enabled"
            }

        # S94: Lock protects cash, positions, and realized_pnl from concurrent bot updates.
        # DB writes are queued under lock but EXECUTED after lock release so retry sleeps
        # don't block other bots.  ~200-1500ms latency reduction per trade.
        async with self._trade_lock:
            result = await self._place_order_locked(
                market_id, token_id, side, size, price, bot_name, confidence, original_side, order_type, correlation_id, latency_ms,
                bid=bid, ask=ask, volume=volume, model_version=model_version, model_name=model_name, event_data=event_data,
            )
            # Drain pending DB writes (populated by _place_order_locked)
            _db_writes = list(self._pending_db_writes)
            self._pending_db_writes.clear()

        # Execute DB persistence OUTSIDE the lock — retries won't block other bots
        for _write_coro in _db_writes:
            try:
                await _write_coro
            except Exception as _db_err:
                logger.warning("post_lock_db_write_failed", error=str(_db_err), market_id=market_id)

        return result

    async def _place_order_locked(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        bot_name: str = "paper_trader",
        confidence: Optional[float] = None,
        original_side: Optional[str] = None,
        order_type: str = "market",
        correlation_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
        bid: float = 0.0,
        ask: float = 0.0,
        volume: float = 0.0,
        model_version: Optional[int] = None,
        model_name: Optional[str] = None,
        event_data: Optional[dict] = None,
    ) -> Dict:
        """Inner order handler — called under self._trade_lock."""
        # S104: Default fill quality metrics — overwritten by BUY path when realistic fills enabled.
        # These must be initialized here because the BUY path sets them inside conditional blocks.
        _fill_prob = 1.0
        _fill_frac = 1.0
        _decay_slip_bps = 0
        _lambda_slip_bps = 0
        _cum_add = 0
        _res_slip_mult = 1.0

        # Auto-reset daily P&L at day boundary (UTC)
        today = datetime.now(timezone.utc).date()
        if self._pnl_reset_date is None or self._pnl_reset_date != today:
            if self._pnl_reset_date is not None:
                logger.info("Daily P&L reset", previous_pnl=self.realized_pnl_today)
            self.realized_pnl_today = {}
            self._pnl_reset_date = today

        # S94: In-memory idempotency fast-check (covers gap between lock release and DB write)
        # S107: Return success=False so order_gateway does NOT call confirm_position
        # again (the original trade already created the position). Previous behavior
        # returned success=True + filled=0, which created ghost positions (size=0).
        if correlation_id and correlation_id in self._pending_correlation_ids:
            logger.info("paper_trade_idempotent_memory", correlation_id=correlation_id, market_id=market_id)
            return {"success": False, "idempotent": True, "order_id": "pending", "error": "duplicate: already pending"}

        # H1: Idempotency guard — reject if correlation_id already executed (prevents double-fill
        # on timeout + retry). Checks DB before any cash/position mutation.
        if correlation_id and self.db and hasattr(self.db, "get_paper_trade_by_correlation_id"):
            try:
                existing = await self.db.get_paper_trade_by_correlation_id(correlation_id, market_id=market_id)
                if existing:
                    logger.info(
                        "paper_trade_idempotent",
                        correlation_id=correlation_id,
                        order_id=existing["order_id"],
                        market_id=market_id,
                    )
                    return {
                        "success": True,
                        "order_id": existing["order_id"],
                        "filled": existing["size"],
                        "price": existing["price"],
                        "idempotent": True,
                    }
            except Exception as _idem_err:
                logger.debug("Idempotency check failed (proceeding with trade): %s", _idem_err)

        # Order state machine: record timestamps for PENDING→SUBMITTED→FILLED transitions.
        _pending_at = datetime.now(timezone.utc)

        # B4: Use spread-side anchor when bid/ask available (buys fill at ask, sells at bid).
        # Falls back to mid-price when bid/ask not provided (backward-compatible default).
        if bid > 0.0 and ask > 0.0:
            price = ask if side == "BUY" else bid

        # S95: Realistic fills apply to ALL trades including RTDS fast-path.
        # Fill probability is pure math (no DB/API) — zero latency impact.
        # S94 bypassed this for speed, but it inflated P&L with 100% fills.
        _realistic = getattr(settings, "PAPER_REALISTIC_FILLS", False) is True

        # S106: Fill-failure cooldown — skip markets that keep failing fills.
        # Ported from WeatherBot's consecutive-failure tracking pattern.
        if _realistic and side == "BUY":
            try:
                _cooldown_s = int(getattr(settings, "PAPER_FILL_FAILURE_COOLDOWN_S", 300))
            except (TypeError, ValueError):
                _cooldown_s = 300
            _ff_entry = self._fill_failure_tracker.get(market_id)
            if _ff_entry and _ff_entry[0] >= 3:
                _elapsed = time.monotonic() - _ff_entry[1]
                if _elapsed < _cooldown_s:
                    logger.info("paper_fill_cooldown", market_id=market_id,
                                failures=_ff_entry[0], remaining_s=round(_cooldown_s - _elapsed))
                    return {
                        "success": False,
                        "error": f"Fill cooldown: {_ff_entry[0]} consecutive failures, {_cooldown_s - _elapsed:.0f}s remaining",
                    }
                else:
                    # Cooldown expired, reset tracker
                    del self._fill_failure_tracker[market_id]

        # S95: Alpha decay — exponential signal deterioration replaces S91 linear drift.
        # No threshold: applies proportionally to ALL latencies via exp(-ln2 * t / half_life).
        _event = event_data or {}
        if _realistic and side == "BUY" and latency_ms is not None and latency_ms > 0:
            _half_life = _event.get("alpha_decay_half_life_s",
                                    getattr(settings, "PAPER_ALPHA_DECAY_HALF_LIFE_S", 300))
            _decay = _alpha_decay_factor(latency_ms, _half_life)
            _decay_slip_bps = (1.0 - _decay) * 100  # max 100 bps at full decay
            _decay_slip = _decay_slip_bps / 10000.0 * (0.5 + random.random())
            if side == "BUY":
                price = min(0.999, price + _decay_slip)
            else:
                price = max(0.001, price - _decay_slip)
            if _decay_slip_bps > 1.0:
                logger.info("paper_alpha_decay", latency_ms=round(latency_ms, 0),
                            decay_factor=round(_decay, 4), slip_bps=round(_decay_slip * 10000, 1),
                            market_id=market_id)

        # S100: L2 Book Walk — use real order book depth when available.
        # Subtracts whale's consumed liquidity, then walks remaining asks to compute VWAP.
        # When successful, replaces heuristic slippage tiers (those are approximations of this).
        _book_walk_used = False
        _book_walk_fill_frac = 1.0
        _order_size_usd = size * price
        if (_realistic and side == "BUY"
                and getattr(settings, "PAPER_BOOK_WALK_ENABLED", False)
                and self._orderbook_tracker and token_id):
            _whale_usd = _event.get("whale_size_usd", 0)
            _whale_shares = _whale_usd / price if price > 0 else 0
            try:
                _book = await self._orderbook_tracker.snapshot_order_book(
                    token_id=token_id, condition_id=str(market_id))
                if _book and not _book.get("error"):
                    _asks = _book.get("asks", [])
                    _bw_result = _vwap_from_book(_asks, size, _whale_shares)
                    if _bw_result:
                        _bw_vwap, _bw_fill_frac, _bw_slip = _bw_result
                        price = max(0.001, min(0.999, _bw_vwap))
                        _book_walk_used = True
                        _book_walk_fill_frac = _bw_fill_frac
                        logger.info("paper_book_walk", market_id=market_id,
                                    vwap=round(_bw_vwap, 4), fill_frac=round(_bw_fill_frac, 3),
                                    slippage_cents=round(_bw_slip * 100, 2),
                                    whale_shares=round(_whale_shares, 1),
                                    ask_levels=len(_asks), bot_name=bot_name)
            except Exception as _bw_err:
                logger.debug("paper_book_walk_failed", error=str(_bw_err), market_id=market_id)

        # Apply size-dependent slippage. Set FIXED_SLIPPAGE_BPS>0 in env to override.
        # S100: Skip heuristic slippage when book walk already computed VWAP fill price.
        # S95: Pass price for boundary multiplier + add square-root market impact
        _fixed = getattr(settings, "FIXED_SLIPPAGE_BPS", 0)
        if _book_walk_used:
            slippage_bps = 0  # book walk already accounts for slippage
        elif _fixed > 0:
            slippage_bps = _fixed
        else:
            slippage_bps = _size_dependent_slippage_bps(_order_size_usd, price=price)
            # S95: Square-root market impact — significant for large orders on thin markets
            _sqrt_impact = _sqrt_market_impact_bps(_order_size_usd, volume, price)
            slippage_bps = int(slippage_bps + _sqrt_impact)

        # S95: Kyle's lambda adverse selection — high lambda markets get extra slippage
        _kyle_lambda = DEFAULT_LAMBDA
        if _realistic and side == "BUY" and getattr(settings, "PAPER_KYLE_LAMBDA_ENABLED", True):
            _kyle_lambda = await self._get_kyle_lambda(market_id)
            _lambda_slip_bps = int(_kyle_lambda * 15)  # lambda=0.5→+7bps, lambda=2.0→+30bps
            slippage_bps += _lambda_slip_bps

        # S95: Cross-scan cumulative impact — 2nd+ BUY on same market within 60s gets worse fills
        if _realistic and side == "BUY" and getattr(settings, "PAPER_CROSS_SCAN_IMPACT_ENABLED", True):
            _now_mono = time.monotonic()
            _scan_entry = self._scan_impact.get(market_id)
            if _scan_entry is not None:
                _cum_bps, _scan_ts = _scan_entry
                if (_now_mono - _scan_ts) < 60.0:
                    _cum_add = min(200, int(_cum_bps))  # cap at 200 bps
                    slippage_bps += _cum_add
                    if _cum_add > 5:
                        logger.info("paper_cross_scan_impact", market_id=market_id,
                                    cumulative_bps=_cum_add)

        # S95: Resolution proximity — escalating slippage near market resolution
        _hours_to_res = _event.get("lead_time_hours")
        _res_slip_mult = 1.0
        _res_fill_mult = 1.0
        if _realistic and side == "BUY" and getattr(settings, "PAPER_RESOLUTION_PROXIMITY_ENABLED", True):
            _res_slip_mult, _res_fill_mult = _resolution_proximity_penalty(_hours_to_res)
            if _res_slip_mult > 1.0:
                slippage_bps = int(slippage_bps * _res_slip_mult)
                logger.info("paper_resolution_proximity_slippage", market_id=market_id,
                            hours_to_res=round(_hours_to_res or 0, 1),
                            slip_mult=_res_slip_mult, fill_mult=_res_fill_mult)

        original_price = price
        _submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        price = _apply_slippage(price, side, slippage_bps)

        # S106: Slippage-eats-edge rejection — if estimated fill price erases the edge, skip.
        # Ported from WeatherBot's liquidity_guardian pattern. Applies to BUY orders only.
        # Edge = confidence - price. If slipped price >= confidence, no positive expectation.
        _slippage_edge_check = getattr(settings, "PAPER_SLIPPAGE_EDGE_CHECK", True)
        if (_realistic and side == "BUY"
                and _slippage_edge_check is True
                and confidence is not None and confidence > 0):
            _slipped_edge = confidence - price
            if _slipped_edge <= 0:
                logger.info("paper_slippage_eats_edge", market_id=market_id,
                            confidence=round(confidence, 4), slipped_price=round(price, 4),
                            original_price=round(original_price, 4),
                            slippage_bps=slippage_bps)
                # Track fill failure for cooldown
                _ff = self._fill_failure_tracker.get(market_id, (0, 0.0))
                self._fill_failure_tracker[market_id] = (_ff[0] + 1, time.monotonic())
                return {
                    "success": False,
                    "error": f"Slippage eats edge: conf={confidence:.4f} <= slipped_price={price:.4f}",
                }

        # S91: Fill probability + partial fills — BUY only (SELLs must always close positions)
        if _realistic and side == "BUY":
            _spread = (ask - bid) if (bid > 0 and ask > 0) else getattr(settings, "PAPER_DEFAULT_SPREAD", 0.04)
            _fill_prob = _fill_probability(price, _order_size_usd, _spread, volume)

            # S95: Kyle's lambda fill penalty — high AS markets harder to fill
            if getattr(settings, "PAPER_KYLE_LAMBDA_ENABLED", True):
                _as_penalty = max(0.3, 1.0 - _kyle_lambda * 0.3)
                _fill_prob *= _as_penalty

            # S95: Resolution proximity fill penalty
            if _res_fill_mult < 1.0:
                _fill_prob *= _res_fill_mult

            # S105 Fix 6: Taker-side filter — if the most recent trade's taker side
            # matches our order side, our resting order wouldn't have been hit.
            # For market/taker orders this is a softer penalty (0.5x) rather than a hard block,
            # since we're crossing the spread, not resting. Gated behind setting.
            # S105b: When taker_side data is NOT available, apply a flat statistical
            # discount (PAPER_TAKER_SIDE_FACTOR, default 0.55). ~45% of the time the
            # taker is on the same side as our order, reducing effective fill rate.
            if getattr(settings, "PAPER_TAKER_SIDE_FILTER", False):
                _taker_side = (_event.get("taker_side") or "").upper()
                _our_side = side.upper() if side else ""
                if _taker_side and _taker_side == _our_side:
                    _fill_prob *= 0.5
                    logger.debug("paper_taker_side_penalty", taker=_taker_side,
                                 ours=_our_side, fill_prob=round(_fill_prob, 3))
                elif not _taker_side:
                    # No taker-side data — apply flat statistical discount
                    _tsf = float(getattr(settings, "PAPER_TAKER_SIDE_FACTOR", 0.55))
                    _fill_prob *= _tsf

            _fill_prob = max(0.05, min(1.0, _fill_prob))

            if random.random() > _fill_prob:
                # S106: Track fill failure for cooldown
                _ff = self._fill_failure_tracker.get(market_id, (0, 0.0))
                self._fill_failure_tracker[market_id] = (_ff[0] + 1, time.monotonic())
                logger.info("paper_no_fill", fill_prob=round(_fill_prob, 3),
                            market_id=market_id, price=round(price, 4),
                            size_usd=round(_order_size_usd, 2),
                            consecutive_failures=_ff[0] + 1)
                return {
                    "success": False,
                    "error": f"Order not filled (fill probability {_fill_prob:.0%})",
                    "fill_probability": _fill_prob,
                }

            # Partial fill: S100 book walk gives deterministic fill fraction from real depth;
            # heuristic path draws from [fill_prob, 1.0] range.
            if _book_walk_used and _book_walk_fill_frac < 1.0:
                _fill_frac = _book_walk_fill_frac
            else:
                _fill_frac = min(1.0, _fill_prob + random.random() * (1.0 - _fill_prob) * 0.5)
            _filled_size = round(size * _fill_frac, 4)
            if _filled_size < 0.01:
                _filled_size = 0.0
            if _filled_size <= 0:
                return {"success": False, "error": "Partial fill too small"}
            if _filled_size < size:
                logger.info("paper_partial_fill", fill_pct=round(_fill_frac * 100, 1),
                            fill_prob=round(_fill_prob, 3), market_id=market_id,
                            requested=round(size, 2), filled=round(_filled_size, 2))
                size = _filled_size

            # S95: Cross-scan tracker update — record this BUY's impact for subsequent orders
            if getattr(settings, "PAPER_CROSS_SCAN_IMPACT_ENABLED", True):
                _impact_bps = _sqrt_market_impact_bps(_order_size_usd, volume, price)
                _now_mono = time.monotonic()
                _prev = self._scan_impact.get(market_id)
                if _prev and (_now_mono - _prev[1]) < 60.0:
                    self._scan_impact[market_id] = (_prev[0] + _impact_bps, _now_mono)
                else:
                    self._scan_impact[market_id] = (_impact_bps, _now_mono)
                # Prune stale entries (>60s old) every time we update
                _cutoff = _now_mono - 60.0
                if _prev and _prev[1] <= _cutoff:
                    pass  # Already replaced above (stale entry overwritten)
                if len(self._scan_impact) > 50:
                    self._scan_impact = {k: v for k, v in self._scan_impact.items() if v[1] > _cutoff}

        # S95: Most Polymarket markets charge 0% taker fee. Exceptions:
        # 15-min/5-min crypto markets (up to 156 bps), some sports (up to 88 bps).
        # PAPER_TAKER_FEE_BPS defaults to 0 (matching majority of markets).
        # System-wide TAKER_FEE_BPS (150) is preserved for exit strategy edge calcs.
        if order_type == "limit":
            fee_bps = getattr(settings, "MAKER_FEE_BPS", 0)
        else:
            fee_bps = getattr(settings, "PAPER_TAKER_FEE_BPS",
                              getattr(settings, "TAKER_FEE_BPS", 0))
        fee_rate = fee_bps / 10000.0

        trade_id = f"paper_{len(self.trades) + 1}_{datetime.now(timezone.utc).timestamp()}"
        notional = size * price
        fee = notional * fee_rate
        cost = notional
        realized_pnl: Optional[float] = None  # populated for SELL orders only

        if side == "BUY":
            total_cost = cost + fee
            if total_cost > self.cash:
                logger.warning("Paper trade rejected: insufficient cash", market_id=market_id, cost=round(total_cost, 2), cash=round(self.cash, 2), fee=round(fee, 4))
                return {
                    "success": False,
                    "error": f"Insufficient cash: need ${total_cost:.2f} (inc ${fee:.2f} fee), have ${self.cash:.2f}"
                }

            # Execute buy (notional + fee)
            self.cash -= total_cost
            
            # Update position — keyed by (bot_name, market_id) to prevent cross-bot contamination
            pos_key = (bot_name, market_id)
            if pos_key in self.positions:
                pos = self.positions[pos_key]
                # B5 FIX: if residual size is essentially zero (float leftover from prior close),
                # treat as a fresh open so entry_fee doesn't accumulate across BUY/SELL cycles.
                if pos.get("size", 0) <= 1e-6:
                    _token_side = original_side if original_side in ("YES", "NO") else ("YES" if side == "BUY" else "NO")
                    self.positions[pos_key] = {
                        "size": size, "avg_price": price,
                        "token_id": token_id, "side": _token_side, "entry_fee": fee,
                    }
                else:
                    # Average price (true averaging-up on an active position)
                    total_cost = pos["size"] * pos["avg_price"] + cost
                    total_size = pos["size"] + size
                    pos["avg_price"] = total_cost / total_size if total_size > 0 else price
                    pos["size"] = total_size
                    # Accumulate entry fees across averaging-up trades so realized_pnl on close is accurate
                    pos["entry_fee"] = pos.get("entry_fee", 0.0) + fee
            else:
                # Use original_side (YES/NO) if available, else infer from side
                _token_side = original_side if original_side in ("YES", "NO") else ("YES" if side == "BUY" else "NO")
                if original_side not in ("YES", "NO"):
                    logger.warning("paper_side_inferred", market_id=market_id, side=side,
                                   inferred=_token_side, bot_name=bot_name)
                self.positions[pos_key] = {
                    "size": size,
                    "avg_price": price,
                    "token_id": token_id,
                    "side": _token_side,
                    "entry_fee": fee,  # Track entry fee so restart cash restoration is accurate
                }
            # Cross-bot overlap detection
            _other_bots = [k[0] for k in self.positions if k[1] == market_id and k[0] != bot_name]
            if _other_bots:
                logger.info("paper_cross_bot_overlap", market_id=market_id,
                            bot_name=bot_name, other_bots=_other_bots)

            # S106: Reset fill-failure tracker on successful fill
            self._fill_failure_tracker.pop(market_id, None)

        else:  # SELL
            pos_key = (bot_name, market_id)
            held = self.positions.get(pos_key, {}).get("size", 0)
            if pos_key not in self.positions or held < size:
                # Float tolerance: if sizes match within 0.01%, treat as full close
                if held > 0 and abs(held - size) / max(held, size) < 1e-4:
                    size = held  # snap to actual position size
                else:
                    return {
                        "success": False,
                        "error": f"Insufficient position: need {size}, have {held}"
                    }

            # Execute sell (proceeds minus fee)
            proceeds = size * price
            self.cash += (proceeds - fee)

            # Track realized P&L (feeds DrawdownController and cash restoration on restart).
            # Include both exit fee AND cumulative entry fees so cumulative_realized_pnl
            # correctly restores cash after restart (entry fees were already deducted from cash at BUY).
            pos = self.positions[pos_key]
            avg_price = pos.get("avg_price") or 0.0
            _entry_fee_total = pos.get("entry_fee", 0.0)
            # Prorate entry fee by exit fraction so partial exits don't over-deduct
            _pos_size = pos.get("size", size)
            _exit_frac = min(1.0, size / _pos_size) if _pos_size > 1e-9 else 1.0
            _prorated_entry_fee = _entry_fee_total * _exit_frac
            realized_pnl = (price - avg_price) * size - fee - _prorated_entry_fee
            # Reduce remaining entry_fee so future exits get their fair share
            pos["entry_fee"] = _entry_fee_total - _prorated_entry_fee
            self.realized_pnl_today[bot_name] = self.realized_pnl_today.get(bot_name, 0.0) + realized_pnl

            # K7 FIX: Feed PerformanceTracker with trade outcomes
            if hasattr(self, "_performance_tracker") and self._performance_tracker is not None:
                try:
                    _now = datetime.now(timezone.utc)
                    await self._performance_tracker.record_trade_outcome(
                        trade_id=f"paper:{market_id}:{_now.strftime('%Y%m%d%H%M%S')}",
                        bot_name=bot_name,
                        market_id=market_id,
                        entry_price=avg_price,
                        exit_price=price,
                        entry_time=_now,
                        exit_time=_now,
                        profit=realized_pnl,
                        market_category=pos.get("category", "unknown"),
                        signal_source=pos.get("bot_name", bot_name),
                        market_regime="paper_trading",
                    )
                except Exception as _pt_err:
                    logger.warning("PerformanceTracker record failed (learning feedback lost): %s", _pt_err)

            # RL Trade Timing: fire outcome callback so RL agent can learn from this trade
            if self._rl_outcome_callback is not None:
                try:
                    self._rl_outcome_callback(market_id, realized_pnl, price, avg_price)
                except Exception as _rl_err:
                    logger.warning("RL outcome callback failed (agent cannot learn): %s", _rl_err)

            # Update position
            pos["size"] -= size
            # B5 FIX: use epsilon instead of strict <= 0 so float residuals (e.g. 1e-14)
            # from BUY/SELL size mismatch don't keep a ghost position alive. Ghost positions
            # cause entry_fee to accumulate across BUY/SELL cycles → realized_pnl grows -$0.70 →
            # -$20+ over 100+ trades on the same market (confirmed on market 572469, Feb 2026).
            if pos["size"] <= 1e-6:
                del self.positions[pos_key]
        
        # Record trade — store token-outcome side (YES/NO) not order direction (BUY)
        # so downstream PnL queries correctly distinguish YES vs NO bets.
        _db_side = original_side if original_side in ("YES", "NO") else side

        trade = PaperTrade(
            trade_id=trade_id,
            market_id=market_id,
            token_id=token_id,
            side=_db_side,
            size=size,
            price=price,
            timestamp=datetime.now(timezone.utc)
        )
        self.trades.append(trade)

        # S94: Queue DB writes for execution AFTER lock release.
        # In-memory state (cash, positions, pnl) is already mutated above.
        # DB persistence can happen without holding the lock, so retry sleeps
        # don't block other bots.  Saves 200-1500ms per trade.

        # Track correlation_id in memory to prevent double-fill during lock gap
        if correlation_id:
            self._pending_correlation_ids.add(correlation_id)

        # SELL trades are position exits (stop-loss, take-profit, model reversal).
        # Do NOT persist to paper_trades DB — exit P&L is already tracked on the
        # positions table (unrealized_pnl) by position_manager._execute_exit/stop_loss/
        # take_profit, and consecutive loss tracking uses risk_manager.record_trade_outcome()
        # directly. SELL paper_trades corrupted P&L queries across all bots.
        if side == "SELL":
            logger.info(
                "Paper exit executed (no DB record)",
                trade_id=trade_id,
                market_id=market_id,
                size=size,
                realized_pnl=round(realized_pnl or 0, 4),
            )
            # Queue EXIT event for post-lock execution
            if self.db and hasattr(self.db, "insert_trade_event"):
                self._pending_db_writes.append(
                    self._persist_exit_event(
                        bot_name, market_id, token_id, size, price, realized_pnl,
                        correlation_id, trade_id, model_version, model_name, event_data,
                    )
                )
        elif self.db and hasattr(self.db, "insert_paper_trade"):
            if getattr(self.db, "session_factory", None) is None:
                logger.debug(
                    "Paper trade NOT persisted: db.session_factory is None",
                    market_id=market_id, bot_name=bot_name,
                )
            else:
                # S104: Enrich event_data with fill quality metrics before DB write.
                # event_data is the same dict object passed by the caller — mutating in-place
                # ensures these keys land in the trade_events JSONB column.
                if event_data is not None and side == "BUY":
                    event_data["slippage_bps"] = round(abs(price - original_price) * 10000, 1)
                    event_data["fill_prob"] = round(_fill_prob, 4)
                    event_data["fill_frac"] = round(_fill_frac, 4)
                    event_data["book_walk"] = _book_walk_used
                    event_data["alpha_decay_bps"] = round(_decay_slip_bps, 1)
                    event_data["kyle_lambda_bps"] = _lambda_slip_bps
                    event_data["cross_scan_bps"] = _cum_add
                    event_data["res_prox_mult"] = round(_res_slip_mult, 2)

                # Queue BUY persistence (paper_trade + ENTRY event) for post-lock execution
                self._pending_db_writes.append(
                    self._persist_buy_entry(
                        trade_id, market_id, token_id, bot_name, _db_side, size, price,
                        confidence, correlation_id, latency_ms, _submitted_at,
                        model_version, model_name, event_data,
                    )
                )
        slippage_applied = round(abs(price - original_price) * 10000, 1)  # bps
        logger.info(
            "Paper trade executed",
            trade_id=trade_id,
            market_id=market_id,
            side=side,
            size=size,
            requested_price=round(original_price, 6),
            fill_price=round(price, 6),
            slippage_bps=slippage_applied,
            cash_remaining=round(self.cash, 2),
        )

        # S95: Adverse selection diagnostic — log fill details for post-hoc analysis.
        # Build AS curve by computing price moves at 1/5/30/60min after each BUY fill.
        # If fills cluster when price moves against us, we're being adversely selected.
        if side == "BUY" and _realistic:
            logger.info(
                "paper_fill_as_baseline",
                market_id=market_id,
                fill_price=round(price, 6),
                original_price=round(original_price, 6),
                size_usd=round(size * price, 2),
                spread=round((ask - bid) if (bid > 0 and ask > 0) else 0, 4),
                volume_24h=round(volume, 0),
                confidence=round(confidence or 0, 3),
                bot_name=bot_name,
                tod_hour=datetime.now(timezone.utc).hour,
            )

        _result = {
            "success": True,
            "order_id": trade_id,
            "trade_id": trade_id,  # R2: explicit trade_id for signal storage downstream
            "filled": size,
            "price": price,
            "requested_price": original_price,
            "slippage_bps": slippage_applied,
            "cash_remaining": self.cash,
        }
        # S104: Surface fill quality metrics for caller diagnostics
        if side == "BUY":
            _result["fill_probability"] = _fill_prob
            _result["fill_fraction"] = _fill_frac
            _result["book_walk_used"] = _book_walk_used
            _result["alpha_decay_bps"] = round(_decay_slip_bps, 1)
            _result["kyle_lambda_bps"] = _lambda_slip_bps
        return _result
    
    async def _persist_exit_event(
        self, bot_name, market_id, token_id, size, price, realized_pnl,
        correlation_id, trade_id, model_version, model_name, event_data,
    ):
        """S94: Emit EXIT trade_event — called AFTER lock release."""
        try:
            await self.db.insert_trade_event(
                event_type="EXIT",
                bot_name=bot_name,
                market_id=market_id,
                token_id=token_id,
                side="SELL",
                size=size,
                price=price,
                realized_pnl=realized_pnl,
                correlation_id=correlation_id,
                order_id=trade_id,
                model_version=model_version,
                model_name=model_name,
                event_data=event_data,
            )
        except Exception as e:
            logger.warning("trade_event_exit_emit_failed", error=str(e), market_id=market_id)
        finally:
            if correlation_id:
                self._pending_correlation_ids.discard(correlation_id)

    async def _persist_buy_entry(
        self, trade_id, market_id, token_id, bot_name, db_side, size, price,
        confidence, correlation_id, latency_ms, submitted_at,
        model_version, model_name, event_data,
    ):
        """S94: Persist paper_trade + ENTRY event — called AFTER lock release.

        H5/M9: Retry 3x with backoff to prevent ghost positions.
        Both writes run in parallel via asyncio.gather (Change 2).
        """
        try:
            for _attempt in range(3):
                try:
                    _filled_at = datetime.now(timezone.utc).replace(tzinfo=None)

                    # S94 Change 2: Run paper_trade + trade_event writes in parallel
                    _paper_coro = self.db.insert_paper_trade(
                        order_id=trade_id,
                        market_id=market_id,
                        token_id=token_id,
                        bot_name=bot_name,
                        side=db_side,
                        size=size,
                        price=price,
                        confidence=confidence,
                        correlation_id=correlation_id,
                        realized_pnl=None,
                        latency_ms=latency_ms,
                        status="filled",
                        submitted_at=submitted_at,
                        filled_at=_filled_at,
                    )

                    _event_coro = None
                    if hasattr(self.db, "insert_trade_event"):
                        _event_coro = self.db.insert_trade_event(
                            event_type="ENTRY",
                            bot_name=bot_name,
                            market_id=market_id,
                            token_id=token_id,
                            side=db_side,
                            size=size,
                            price=price,
                            confidence=confidence,
                            correlation_id=correlation_id,
                            order_id=trade_id,
                            model_version=model_version,
                            model_name=model_name,
                            event_data=event_data,
                        )

                    if _event_coro is not None:
                        _results = await asyncio.gather(_paper_coro, _event_coro, return_exceptions=True)
                        # Check paper_trade result — if it raised, re-raise for retry logic
                        if isinstance(_results[0], BaseException):
                            raise _results[0]
                        if isinstance(_results[1], BaseException):
                            logger.warning("trade_event_entry_emit_failed", error=str(_results[1]), market_id=market_id)
                    else:
                        await _paper_coro

                    break  # Success — exit retry loop
                except Exception as e:
                    err_str = str(e).lower()
                    if "unique" in err_str or "duplicate" in err_str or "uq_paper_trades" in err_str:
                        logger.info(
                            "Paper trade already exists (duplicate skipped)",
                            market_id=market_id, side=db_side, bot_name=bot_name,
                        )
                        break
                    if _attempt < 2:
                        await asyncio.sleep(0.5 * (_attempt + 1))
                        logger.warning(
                            "Paper trade persist retry %d/3", _attempt + 1,
                            market_id=market_id, error=str(e),
                        )
                    else:
                        logger.warning(
                            "Paper trade persist FAILED after 3 attempts — "
                            "position may reappear on restart",
                            market_id=market_id, side=db_side, error=str(e),
                        )
        finally:
            if correlation_id:
                self._pending_correlation_ids.discard(correlation_id)

    def get_portfolio_value(self, current_prices: Dict[str, float]) -> Dict:
        """
        Calculate current portfolio value.

        Args:
            current_prices: Dict mapping market_id to current price
        
        Returns:
            Dict with portfolio metrics
        """
        positions_value = 0.0
        
        for (_, market_id), position in self.positions.items():
            current_price = current_prices.get(market_id, position["avg_price"])
            positions_value += position["size"] * current_price
        
        total_value = self.cash + positions_value
        pnl = total_value - self.initial_capital
        pnl_pct = (pnl / self.initial_capital) * 100 if self.initial_capital > 0 else 0
        
        return {
            "cash": self.cash,
            "positions_value": positions_value,
            "total_value": total_value,
            "initial_capital": self.initial_capital,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "positions_count": len(self.positions),
            "trades_count": len(self.trades)
        }
    
    def get_trades(self) -> List[Dict]:
        """Get all paper trades"""
        return [
            {
                "trade_id": t.trade_id,
                "market_id": t.market_id,
                "side": t.side,
                "size": t.size,
                "price": t.price,
                "timestamp": t.timestamp.isoformat()
            }
            for t in self.trades
        ]
    
    def get_positions(self) -> Dict[str, Dict]:
        """Get current positions"""
        return self.positions.copy()
    
    def set_rl_outcome_callback(self, callback) -> None:
        """Register a callback fired on every SELL (realized P&L) for RL agent learning.

        Callback signature: callback(market_id: str, realized_pnl: float, exit_price: float, avg_entry_price: float)
        """
        self._rl_outcome_callback = callback

    def reset(self):
        """Reset paper trading account"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.pnl_history = []
        self.realized_pnl_today = {}
        self._pnl_reset_date = None
        logger.info("Paper trading account reset")
