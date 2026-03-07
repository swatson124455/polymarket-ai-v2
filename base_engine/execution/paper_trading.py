"""
Paper Trading Mode
==================
Test strategies with real data, fake money.
Simulates order execution without real trades with realistic slippage.
When db is provided, each trade is persisted to paper_trades for resolution backfill and hypothetical P&L.
"""
import random
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from config.settings import settings

logger = get_logger()


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
        self.positions: Dict[str, Dict] = {}  # market_id -> position
        self.trades: List[PaperTrade] = []
        self.pnl_history: List[Dict] = []
        self.enabled = False
        self.db = db  # Database instance for persisting to paper_trades
        # BUG-3 fix: Track realized P&L for DrawdownController integration
        self.realized_pnl_today: float = 0.0  # Accumulated realized P&L for current day
        self._pnl_reset_date: Optional[datetime] = None  # Date of last reset
        # RL Trade Timing: callback fired on realized P&L (sell trades)
        self._rl_outcome_callback = None
        # Protect cash/positions/pnl from concurrent bot updates (race condition fix)
        import asyncio
        self._trade_lock = asyncio.Lock()
    
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
                        # Only seed if not already in positions (don't overwrite active trades)
                        if mid not in self.positions:
                            self.positions[mid] = {
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

        # Lock protects cash, positions, and realized_pnl from concurrent bot updates
        async with self._trade_lock:
            return await self._place_order_locked(
                market_id, token_id, side, size, price, bot_name, confidence, original_side, order_type, correlation_id, latency_ms
            )

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
    ) -> Dict:
        """Inner order handler — called under self._trade_lock."""
        # Auto-reset daily P&L at day boundary (UTC)
        today = datetime.now(timezone.utc).date()
        if self._pnl_reset_date is None or self._pnl_reset_date != today:
            if self._pnl_reset_date is not None:
                logger.info("Daily P&L reset", previous_pnl=round(self.realized_pnl_today, 2))
            self.realized_pnl_today = 0.0
            self._pnl_reset_date = today

        # Apply realistic slippage (FIXED_SLIPPAGE_BPS default: 50 bps = 0.5%)
        slippage_bps = getattr(settings, "FIXED_SLIPPAGE_BPS", 50)
        original_price = price
        price = _apply_slippage(price, side, slippage_bps)

        # Apply maker/taker fee (Polymarket: maker=0%, taker=1.5%)
        if order_type == "limit":
            fee_bps = getattr(settings, "MAKER_FEE_BPS", 0)
        else:
            fee_bps = getattr(settings, "TAKER_FEE_BPS", 150)
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
            
            # Update position
            if market_id in self.positions:
                pos = self.positions[market_id]
                # B5 FIX: if residual size is essentially zero (float leftover from prior close),
                # treat as a fresh open so entry_fee doesn't accumulate across BUY/SELL cycles.
                if pos.get("size", 0) <= 1e-6:
                    _token_side = original_side if original_side in ("YES", "NO") else ("YES" if side == "BUY" else "NO")
                    self.positions[market_id] = {
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
                self.positions[market_id] = {
                    "size": size,
                    "avg_price": price,
                    "token_id": token_id,
                    "side": _token_side,
                    "entry_fee": fee,  # Track entry fee so restart cash restoration is accurate
                }
        
        else:  # SELL
            held = self.positions.get(market_id, {}).get("size", 0)
            if market_id not in self.positions or held < size:
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
            pos = self.positions[market_id]
            avg_price = pos.get("avg_price") or 0.0
            _entry_fee_total = pos.get("entry_fee", 0.0)
            realized_pnl = (price - avg_price) * size - fee - _entry_fee_total
            self.realized_pnl_today += realized_pnl

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
                del self.positions[market_id]
        
        # Record trade — store token-outcome side (YES/NO) not order direction (BUY)
        # so downstream PnL queries correctly distinguish YES vs NO bets.
        _db_side = side  # SELL stays SELL for exit trades
        if side != "SELL" and original_side in ("YES", "NO"):
            _db_side = original_side

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
        if self.db and hasattr(self.db, "insert_paper_trade"):
            try:
                await self.db.insert_paper_trade(
                    order_id=trade_id,
                    market_id=market_id,
                    token_id=token_id,
                    bot_name=bot_name,
                    side=_db_side,
                    size=size,
                    price=price,
                    confidence=confidence,
                    correlation_id=correlation_id,
                    realized_pnl=realized_pnl,  # None for BUY, computed value for SELL
                    latency_ms=latency_ms,
                )
            except Exception as e:
                # C4 FIX: Elevate to WARNING. At DEBUG, this was invisible — position was closed
                # in memory but not in DB, so on restart seed_positions_from_db() reloads the
                # ghost position causing double-position bugs and incorrect cash balances.
                logger.warning("Paper trade persist failed — position may reappear on restart: %s", e)
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
        return {
            "success": True,
            "order_id": trade_id,
            "trade_id": trade_id,  # R2: explicit trade_id for signal storage downstream
            "filled": size,
            "price": price,
            "requested_price": original_price,
            "slippage_bps": slippage_applied,
            "cash_remaining": self.cash,
        }
    
    def get_portfolio_value(self, current_prices: Dict[str, float]) -> Dict:
        """
        Calculate current portfolio value.
        
        Args:
            current_prices: Dict mapping market_id to current price
        
        Returns:
            Dict with portfolio metrics
        """
        positions_value = 0.0
        
        for market_id, position in self.positions.items():
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
        self.realized_pnl_today = 0.0
        self._pnl_reset_date = None
        logger.info("Paper trading account reset")
