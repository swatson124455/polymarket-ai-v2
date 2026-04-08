"""
Paper Trading Mode
==================
Test strategies with real data, fake money.
Fills at VWAP from real L2 orderbook when available, signal price otherwise.
Records every trade signal to shadow_fills for retroactive P&L analysis.
When db is provided, each trade is persisted to paper_trades for resolution backfill.
"""
import asyncio
import time
from typing import Callable, Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from structlog import get_logger
from config.settings import settings

logger = get_logger()

# S157: Failure code constants — used by bots for reason-specific cooldowns.
# Avoids fragile string parsing of error messages.
FAIL_BOOK_DEPLETED = "book_depleted"
FAIL_SLIPPAGE = "slippage"
FAIL_CASH = "insufficient_cash"
FAIL_POSITION = "insufficient_position"
FAIL_DUPLICATE = "duplicate"
FAIL_PARTIAL = "partial_fill"


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


def _vwap_from_bids(
    bids: List[Dict],
    order_size_shares: float,
) -> Optional[Tuple[float, float, float]]:
    """S121: Walk L2 bid side to compute VWAP fill price for SELL orders.

    Mirror of _vwap_from_book but walks bids descending (best bid first).
    Models realistic exit slippage — live SELLs walk down the bid side.

    Args:
        bids: List of {"price": str|float, "size": str|float}.
        order_size_shares: Order size in shares to sell.

    Returns:
        (vwap_price, fill_fraction, slippage_vs_best_bid) or None if no depth.
        slippage is always >= 0 (best_bid - vwap, since VWAP <= best_bid).
    """
    if not bids or order_size_shares <= 0:
        return None

    # Parse and sort descending by price (best bid first)
    levels: List[Tuple[float, float]] = []
    for level in bids:
        try:
            p = float(level.get("price", level.get("p", 0)))
            s = float(level.get("size", level.get("s", 0)))
            if p > 0 and s > 0:
                levels.append((p, s))
        except (ValueError, TypeError, AttributeError):
            continue
    if not levels:
        return None
    levels.sort(key=lambda x: x[0], reverse=True)

    best_bid = levels[0][0]

    # Walk bid side top-down to fill sell order
    remaining_order = order_size_shares
    filled_shares = 0.0
    total_proceeds = 0.0
    for price, size in levels:
        fill_at_level = min(remaining_order, size)
        filled_shares += fill_at_level
        total_proceeds += fill_at_level * price
        remaining_order -= fill_at_level
        if remaining_order <= 0.001:
            break

    if filled_shares <= 0:
        return None

    vwap = total_proceeds / filled_shares
    fill_fraction = filled_shares / order_size_shares
    slippage = best_bid - vwap  # >= 0 since VWAP <= best_bid

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
        # S115: L2 order book tracker for VWAP fills (wired by base_engine)
        self._orderbook_tracker = None
        # S121: Per-scan book depletion — tracks consumed liquidity so consecutive
        # fills on same token see progressively worse depth. Auto-expires after 60s.
        # Key: (token_id, "ask"|"bid") -> (depleted_levels: List[Tuple[price, size]], mono_time)
        self._scan_book_state: Dict[tuple, tuple] = {}
        self._BOOK_DEPLETION_TTL_S = 60.0

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
        on_buy_fill: Optional[Callable] = None,
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
                "error": "Paper trading not enabled",
                "fail_code": "paper_disabled",
            }

        # S94: Lock protects cash, positions, and realized_pnl from concurrent bot updates.
        # DB writes are queued under lock but EXECUTED after lock release so retry sleeps
        # don't block other bots.  ~200-1500ms latency reduction per trade.
        async with self._trade_lock:
            result = await self._place_order_locked(
                market_id, token_id, side, size, price, bot_name, confidence, original_side, order_type, correlation_id, latency_ms,
                bid=bid, ask=ask, volume=volume, model_version=model_version, model_name=model_name, event_data=event_data,
                on_buy_fill=on_buy_fill,
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
        on_buy_fill: Optional[Callable] = None,
    ) -> Dict:
        """Inner order handler — called under self._trade_lock."""
        # S115: Fill fraction from book walk (1.0 = full fill, <1.0 = thin book)
        _fill_frac = 1.0

        # Auto-reset daily P&L at day boundary (UTC)
        today = datetime.now(timezone.utc).date()
        if self._pnl_reset_date is None or self._pnl_reset_date != today:
            if self._pnl_reset_date is not None:
                logger.info("Daily P&L reset", previous_pnl=self.realized_pnl_today)
            self.realized_pnl_today = {}
            self._pnl_reset_date = today
            self._scan_book_state = {}  # S121: clear depletion state at day boundary

        # S94: In-memory idempotency fast-check (covers gap between lock release and DB write)
        # S107: Return success=False so order_gateway does NOT call confirm_position
        # again (the original trade already created the position). Previous behavior
        # returned success=True + filled=0, which created ghost positions (size=0).
        if correlation_id and correlation_id in self._pending_correlation_ids:
            logger.info("paper_trade_idempotent_memory", correlation_id=correlation_id, market_id=market_id)
            return {"success": False, "idempotent": True, "order_id": "pending", "error": "duplicate: already pending", "fail_code": FAIL_DUPLICATE}

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

        # S115: Use VWAP from order_gateway's book walk (passed via event_data)
        # to set realistic fill price. Edge check already done by order_gateway.
        _event = event_data or {}
        _order_size_usd = size * price
        original_price = price
        _submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)

        _book_walk_used = False
        _fill_frac = 1.0
        _book_walk_slippage = 0.0
        _book_snapshot = _event.get("_shadow_book_snapshot")
        _best_ask = _event.get("_shadow_best_ask")
        _best_bid = _event.get("_shadow_best_bid")
        _spread = _event.get("_shadow_spread", 0.0)
        _depth_at_best_usd = _event.get("_shadow_depth_best", 0.0)
        _total_depth_usd = _event.get("_shadow_total_depth", 0.0)

        # Use VWAP from order_gateway if available (BUY walks asks, SELL walks bids)
        # S121: Check for depleted book state from prior fills in this scan cycle.
        # If a prior fill consumed liquidity on this token, re-walk the depleted book
        # instead of using the gateway's VWAP (which saw the original snapshot).
        _book_side = "ask" if side == "BUY" else "bid"
        _depletion_key = (token_id, _book_side) if token_id else None
        _used_depleted_book = False

        if _event.get("_shadow_book_walk_used") and _book_snapshot and _depletion_key:
            _depleted = self._scan_book_state.get(_depletion_key)
            if _depleted and (time.monotonic() - _depleted[1]) < self._BOOK_DEPLETION_TTL_S:
                # Re-walk the depleted book instead of using gateway's VWAP
                _depleted_levels = _depleted[0]
                if side == "BUY":
                    _bw = _vwap_from_book(
                        [{"price": p, "size": s} for p, s in _depleted_levels],
                        size,
                    )
                else:
                    _bw = _vwap_from_bids(
                        [{"price": p, "size": s} for p, s in _depleted_levels],
                        size,
                    )
                if _bw:
                    price = max(0.001, min(0.999, _bw[0]))
                    _book_walk_used = True
                    _fill_frac = _bw[1]
                    _book_walk_slippage = _bw[2]
                    _used_depleted_book = True
                    logger.info("paper_book_walk_depleted", market_id=market_id,
                                vwap=round(price, 4), fill_frac=round(_fill_frac, 3),
                                slippage_cents=round(_book_walk_slippage * 100, 2),
                                bot_name=bot_name, walk_side=_book_side)
                else:
                    # Depleted book has no remaining depth
                    return {"success": False, "error": "Insufficient book depth (depleted by prior fills)", "fail_code": FAIL_BOOK_DEPLETED}

        if not _used_depleted_book and _event.get("_shadow_book_walk_used"):
            _shadow_vwap = _event.get("_shadow_vwap")
            _shadow_fill_frac = _event.get("_shadow_fill_frac", 1.0)
            _shadow_slippage = _event.get("_shadow_slippage", 0.0)
            if _shadow_vwap is not None:
                price = max(0.001, min(0.999, _shadow_vwap))
                _book_walk_used = True
                _fill_frac = _shadow_fill_frac
                _book_walk_slippage = _shadow_slippage
                logger.info("paper_book_walk", market_id=market_id,
                            vwap=round(price, 4), fill_frac=round(_fill_frac, 3),
                            slippage_cents=round(_book_walk_slippage * 100, 2),
                            bot_name=bot_name, walk_side=_book_side)

        # S121: Latency drift penalty — models adverse price movement during execution delay.
        # BUY: price drifts up (worse). SELL: price drifts down (worse).
        # Only applies when latency_ms is provided and PAPER_LATENCY_DRIFT_BPS_PER_SEC > 0.
        _drift_bps_per_sec = getattr(settings, "PAPER_LATENCY_DRIFT_BPS_PER_SEC", 0)
        if latency_ms is not None and _drift_bps_per_sec > 0:
            _latency_s = latency_ms / 1000.0
            _drift = _latency_s * _drift_bps_per_sec / 10000.0
            _price_before_drift = price
            if side == "BUY":
                price = min(0.999, price + _drift)
            else:
                price = max(0.001, price - _drift)
            if abs(price - _price_before_drift) > 0.00001:
                logger.info("paper_latency_drift", market_id=market_id,
                            latency_ms=round(latency_ms, 1),
                            drift_bps=round(_drift * 10000, 1),
                            price_before=round(_price_before_drift, 6),
                            price_after=round(price, 6), side=side)

        # S147: Preserve original order USD cap after VWAP adjustment.
        # Book walk can raise the fill price above the sizing price, causing
        # size * VWAP to exceed the intended USD (e.g., $300 cap becomes $367).
        # Trim shares so the fill stays within the original order USD.
        if side == "BUY" and _book_walk_used and price > original_price and _order_size_usd > 0:
            _new_notional = size * price
            if _new_notional > _order_size_usd * 1.005:  # 0.5% tolerance for rounding
                size = _order_size_usd / price
                logger.info("paper_vwap_size_trim", market_id=market_id,
                            original_usd=round(_order_size_usd, 2),
                            vwap_usd=round(_new_notional, 2),
                            trimmed_size=round(size, 4),
                            bot_name=bot_name)

        # Relative slippage guard — reject fills with excessive price impact
        _slip_abs = abs(price - original_price)
        if _book_walk_used and _slip_abs >= 0.005:  # Skip sub-half-cent moves
            _slip_pct = _slip_abs / max(original_price, 0.01)
            _max_slip = 0.10 if original_price >= 0.20 else 0.20
            if _slip_pct > _max_slip:
                return {
                    "success": False,
                    "error": f"Slippage {_slip_pct:.1%} exceeds {_max_slip:.0%} limit "
                             f"(original={original_price:.4f}, fill={price:.4f})",
                    "fail_code": FAIL_SLIPPAGE,
                }

        # Partial fill from book depth
        if _fill_frac < 1.0:
            _filled_size = round(size * _fill_frac, 4)
            if _filled_size < 0.01:
                _filled_size = 0.0
            if _filled_size <= 0:
                return {"success": False, "error": "Insufficient book depth for fill", "fail_code": FAIL_PARTIAL}
            if _filled_size < size:
                logger.info("paper_partial_fill", fill_pct=round(_fill_frac * 100, 1),
                            market_id=market_id, requested=round(size, 2),
                            filled=round(_filled_size, 2))
                size = _filled_size

        # Most Polymarket markets charge 0% taker fee. Exceptions:
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
                    "error": f"Insufficient cash: need ${total_cost:.2f} (inc ${fee:.2f} fee), have ${self.cash:.2f}",
                    "fail_code": FAIL_CASH,
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
                        "error": f"Insufficient position: need {size}, have {held}",
                        "fail_code": FAIL_POSITION,
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
        # S129: Cap in-memory trade list to prevent unbounded growth (~2MB/day).
        # Older trades are already persisted to paper_trades DB; only recent
        # entries are needed for in-memory P&L snapshots.
        if len(self.trades) > 2000:
            self.trades = self.trades[-1000:]

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
            # Enrich EXIT event_data with entry_price for P&L reconstruction
            if event_data is not None:
                event_data["entry_price"] = round(avg_price, 6) if avg_price else 0.0
            # S121: Enrich SELL event_data with book walk metrics (mirrors BUY enrichment)
            if event_data is not None and _book_walk_used:
                event_data["slippage_bps"] = round(abs(price - original_price) * 10000, 1)
                event_data["fill_frac"] = round(_fill_frac, 4)
                event_data["book_walk"] = True
                event_data["book_walk_slippage"] = round(_book_walk_slippage, 6)
                event_data["best_bid"] = round(_best_bid, 4) if _best_bid else None
                event_data["spread"] = round(_spread, 4)
                event_data["depth_at_best_usd"] = round(_depth_at_best_usd, 2)
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
                        bot_name, market_id, token_id, _db_side, size, price, realized_pnl,
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
                # S115/S121: Enrich event_data with real book data before DB write.
                if event_data is not None:
                    event_data["slippage_bps"] = round(abs(price - original_price) * 10000, 1)
                    event_data["fill_frac"] = round(_fill_frac, 4)
                    event_data["book_walk"] = _book_walk_used
                    event_data["book_walk_slippage"] = round(_book_walk_slippage, 6)
                    if side == "BUY":
                        event_data["best_ask"] = round(_best_ask, 4) if _best_ask else None
                    else:
                        event_data["best_bid"] = round(_best_bid, 4) if _best_bid else None
                    event_data["spread"] = round(_spread, 4)
                    event_data["depth_at_best_usd"] = round(_depth_at_best_usd, 2)

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

        # S115: Record shadow fill for every executed BUY trade
        if side == "BUY":
            await self._record_shadow_fill(
                bot_name=bot_name, market_id=market_id, token_id=token_id,
                side=side, order_size_shares=size, order_size_usd=size * price,
                signal_price=original_price, confidence=confidence,
                latency_ms=latency_ms, book_snapshot=_book_snapshot,
                best_ask=_best_ask, best_bid=_best_bid, spread=_spread,
                depth_at_best_usd=_depth_at_best_usd, total_depth_usd=_total_depth_usd,
                vwap_fill_price=price, book_walk_slippage=_book_walk_slippage,
                fill_fraction=_fill_frac, trade_executed=True,
                execution_price=price, correlation_id=correlation_id,
                model_name=model_name, event_data=event_data,
            )

        # S121: Update book depletion state — subtract filled shares from book
        # so the next fill on same token in this scan sees reduced depth.
        if _book_walk_used and _depletion_key and _book_snapshot:
            self._update_book_depletion(_depletion_key, _book_snapshot, size, _book_side)

        # S133: Invoke on_buy_fill callback inside the lock so exposure is tracked
        # before any concurrent bot reads daily_exposure.
        if side == "BUY" and on_buy_fill is not None:
            try:
                _token_side_for_cb = original_side if original_side in ("YES", "NO") else "YES"
                on_buy_fill(bot_name, market_id, size, price, side=_token_side_for_cb,
                            predicted_prob=confidence or 0.5)
            except Exception as _cb_err:
                logger.warning("on_buy_fill callback failed (non-critical): %s", _cb_err)

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
        # S115: Surface fill quality metrics for caller diagnostics
        if side == "BUY":
            _result["fill_fraction"] = _fill_frac
            _result["book_walk_used"] = _book_walk_used
        return _result
    
    async def _record_shadow_fill(
        self, *, bot_name, market_id, token_id, side,
        order_size_shares, order_size_usd, signal_price, confidence,
        latency_ms, book_snapshot, best_ask, best_bid, spread,
        depth_at_best_usd, total_depth_usd, vwap_fill_price,
        book_walk_slippage, fill_fraction, trade_executed,
        execution_price, correlation_id, model_name, event_data,
    ):
        """S115: Record shadow fill row for retroactive P&L analysis."""
        if not self.db or not hasattr(self.db, "insert_shadow_fill"):
            return
        try:
            _edge_at_signal = (confidence - signal_price) if confidence else None
            _edge_at_vwap = (confidence - vwap_fill_price) if confidence else None
            await self.db.insert_shadow_fill(
                bot_name=bot_name,
                market_id=market_id,
                token_id=token_id,
                side=side,
                order_size_shares=order_size_shares,
                order_size_usd=order_size_usd,
                signal_price=signal_price,
                confidence=confidence,
                edge_at_signal=_edge_at_signal,
                latency_ms=latency_ms,
                book_snapshot=book_snapshot,
                best_ask=best_ask,
                best_bid=best_bid,
                spread=spread,
                depth_at_best_usd=depth_at_best_usd,
                total_depth_usd=total_depth_usd,
                vwap_fill_price=vwap_fill_price,
                book_walk_slippage=book_walk_slippage,
                fill_fraction=fill_fraction,
                edge_at_vwap=_edge_at_vwap,
                trade_executed=trade_executed,
                execution_price=execution_price,
                correlation_id=correlation_id,
                model_name=model_name,
                event_data=event_data,
            )
        except Exception as _sf_err:
            logger.debug("shadow_fill_record_failed", error=str(_sf_err), market_id=market_id)

    async def _persist_exit_event(
        self, bot_name, market_id, token_id, side, size, price, realized_pnl,
        correlation_id, trade_id, model_version, model_name, event_data,
    ):
        """S94: Emit EXIT trade_event — called AFTER lock release."""
        try:
            await self.db.insert_trade_event(
                event_type="EXIT",
                bot_name=bot_name,
                market_id=market_id,
                token_id=token_id,
                side=side,
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

    def _update_book_depletion(
        self,
        depletion_key: tuple,
        book_snapshot: list,
        filled_shares: float,
        book_side: str,
    ) -> None:
        """S121: Subtract filled shares from book and store depleted state.

        After a fill, the next fill on the same token within 60s will see
        the depleted book instead of the original snapshot.
        """
        # Get current depleted levels or parse from snapshot
        existing = self._scan_book_state.get(depletion_key)
        if existing and (time.monotonic() - existing[1]) < self._BOOK_DEPLETION_TTL_S:
            levels = list(existing[0])  # copy
        else:
            # Parse snapshot into (price, size) tuples
            levels = []
            for lvl in book_snapshot:
                try:
                    p = float(lvl.get("price", lvl.get("p", 0)))
                    s = float(lvl.get("size", lvl.get("s", 0)))
                    if p > 0 and s > 0:
                        levels.append((p, s))
                except (ValueError, TypeError, AttributeError):
                    continue

        # Sort: ascending for asks, descending for bids
        if book_side == "ask":
            levels.sort(key=lambda x: x[0])
        else:
            levels.sort(key=lambda x: x[0], reverse=True)

        # Subtract filled shares from levels (consume from best price first)
        remaining = filled_shares
        new_levels = []
        for p, s in levels:
            if remaining > 0:
                consumed = min(remaining, s)
                remaining -= consumed
                leftover = s - consumed
                if leftover > 0.001:
                    new_levels.append((p, leftover))
            else:
                new_levels.append((p, s))

        self._scan_book_state[depletion_key] = (new_levels, time.monotonic())

    def reset(self):
        """Reset paper trading account"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.pnl_history = []
        self.realized_pnl_today = {}
        self._pnl_reset_date = None
        self._scan_book_state = {}
        logger.info("Paper trading account reset")
