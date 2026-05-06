"""
OrderGateway - Single path for all live orders.
Ensures kill switch, risk limits, liquidity checks, and trade coordinator are applied before execution.
All components (CopyTradingEngine, SmartOrderRouter, AdvancedOrderManager, OMS, bots)
should place orders through this gateway.
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from structlog import get_logger
from config.settings import settings

logger = get_logger()


class OrderGateway:
    """
    Unified order path: kill switch -> risk check -> liquidity check -> coordinator reserve -> execute -> confirm/release.
    """

    def __init__(
        self,
        kill_switch,
        risk_manager,
        trade_coordinator,
        execution_engine,
        liquidity_guardian=None,
        adverse_selection_tracker=None,
        orderbook_analyzer=None,
        smart_order_placer=None,
        paper_trading_engine=None,
        cascade_detector=None,
        dynamic_position_sizing=None,
        drawdown_controller=None,
        multi_kill_switch=None,
        rl_agent=None,
        db=None,
    ):
        self.kill_switch = kill_switch
        self.risk_manager = risk_manager
        self.trade_coordinator = trade_coordinator
        self.execution_engine = execution_engine
        self.liquidity_guardian = liquidity_guardian
        self.adverse_selection_tracker = adverse_selection_tracker
        self.orderbook_analyzer = orderbook_analyzer
        self.smart_order_placer = smart_order_placer
        self.paper_trading_engine = paper_trading_engine
        self.cascade_detector = cascade_detector
        self.dynamic_position_sizing = dynamic_position_sizing
        self.drawdown_controller = drawdown_controller
        self.multi_kill_switch = multi_kill_switch
        self.rl_agent = rl_agent
        self.db = db  # optional Database handle for daily_counters persistence
        # S120: Pending order tracker for live fill confirmation via UserOrderWebSocket
        self._pending_orders: Dict[str, Dict[str, Any]] = {}  # order_id -> {market_id, token_id, side, size, price, bot_name, submitted_at, correlation_id}
        self._pending_order_timeout_s: float = float(getattr(settings, "ORDER_FILL_TIMEOUT_S", 60.0))
        self._market_index: Optional[Dict[str, Dict[str, Any]]] = None  # Set by base_engine after construction
        self._market_index_by_cid: Dict[str, Dict[str, Any]] = {}  # S100: condition_id index, set by base_engine
        self._bot_names_used: Set[str] = set()  # For shutdown: release reservations for all bots in this process
        # S115: OrderBookTracker for pre-trade book walk (wired by base_engine)
        self._orderbook_tracker = None
        # In-memory position tracker for ms-latency reactive path
        self._open_position_markets: Dict[str, Set[str]] = {}  # bot_name -> set of market_ids
        # In-memory exposure tracker: avoids 3 DB queries in risk_manager.check_risk_limits()
        self._position_exposure: Dict[str, Dict[str, float]] = {}  # bot_name -> {market_id: entry_value_usd}
        self._position_details: Dict[str, Dict[str, Any]] = {}  # T8: "bot:market" -> {side, size, price, predicted_prob}
        self._total_exposure_usd: float = 0.0  # sum of all open position values
        self._daily_exposure_usd: Dict[str, float] = {}  # bot_name -> daily exposure
        self._daily_exposure_date: Optional[str] = None  # date string for daily reset

    def has_open_position(self, bot_name: str, market_id: str) -> bool:
        """O(1) in-memory check for reactive path. No DB call."""
        return str(market_id) in self._open_position_markets.get(bot_name, set())

    def get_position_count(self, bot_name: str) -> int:
        """O(1) in-memory count of open positions for a bot."""
        return len(self._open_position_markets.get(bot_name, set()))

    def get_total_exposure_usd(self) -> float:
        """O(1) in-memory total exposure across all bots."""
        return self._total_exposure_usd

    def get_bot_exposure_usd(self, bot_name: str) -> float:
        """O(n) sum of open position values for a specific bot."""
        return sum(self._position_exposure.get(bot_name, {}).values())

    def _maybe_reset_daily(self) -> None:
        """S133: Atomic day boundary reset — set date FIRST to prevent double-clear."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_exposure_date != today:
            self._daily_exposure_date = today  # Set BEFORE clear — second caller sees new date, skips
            self._daily_exposure_usd.clear()

    def get_daily_exposure_usd(self, bot_name: str) -> float:
        """O(1) in-memory daily exposure for a bot. Resets at day boundary."""
        self._maybe_reset_daily()
        return self._daily_exposure_usd.get(bot_name, 0.0)

    def _can_exit(self, market_id: str) -> bool:
        """Check if tokens for this market can be sold. Blocks NegRisk multi-outcome markets
        where tokens may be unsellable through normal CLOB orders."""
        if not self._market_index:
            return True  # No index loaded yet, allow (conservative: don't block cold-start)
        market = self._market_index.get(str(market_id), {})
        neg_risk = market.get("neg_risk") or market.get("negRisk") or False
        outcome_count = market.get("outcome_count", 2) or 2
        if neg_risk and outcome_count > 2:
            return False
        return True

    def get_all_open_positions_snapshot(self) -> List[Dict[str, Any]]:
        """Return snapshot of all open positions for CVaR computation. No DB call."""
        # T8 FIX: Use actual position data instead of hardcoded placeholders
        positions = []
        for bot_name, markets in self._position_exposure.items():
            for mid, val in markets.items():
                # Use stored position details if available, otherwise approximate
                details = self._position_details.get(f"{bot_name}:{mid}", {})
                positions.append({
                    "market_id": mid,
                    "side": details.get("side", "YES"),
                    "size": details.get("size", val),
                    "price": details.get("price", 0.5),
                    "predicted_prob": details.get("predicted_prob", 0.5),
                    "value_usd": val,
                })
        return positions

    def _track_position_open(self, bot_name: str, market_id: str, size: float = 0, price: float = 0,
                              side: str = "YES", predicted_prob: float = 0.5) -> None:
        """Record that bot_name now has an open position on market_id."""
        self._open_position_markets.setdefault(bot_name, set()).add(str(market_id))
        value = size * price if size > 0 and price > 0 else 0.0
        if value > 0:
            self._position_exposure.setdefault(bot_name, {})[str(market_id)] = value
            self._total_exposure_usd += value
            self._maybe_reset_daily()
            self._daily_exposure_usd[bot_name] = self._daily_exposure_usd.get(bot_name, 0.0) + value
        # T8 FIX: Store actual position details for CVaR snapshot
        if not hasattr(self, "_position_details"):
            self._position_details = {}
        self._position_details[f"{bot_name}:{market_id}"] = {
            "side": side, "size": size, "price": price, "predicted_prob": predicted_prob,
        }

    def _track_position_close(self, bot_name: str, market_id: str) -> None:
        """C3 FIX: Remove a closed position from all in-memory exposure trackers.
        _track_position_open() is called for both BUY and SELL; without this complementary
        method, _total_exposure_usd grows monotonically and risk limits become unreachable,
        eventually preventing all new trades."""
        mid = str(market_id)
        bot_markets = self._open_position_markets.get(bot_name)
        if bot_markets:
            bot_markets.discard(mid)
        bot_exposure = self._position_exposure.get(bot_name)
        if bot_exposure and mid in bot_exposure:
            removed_value = bot_exposure.pop(mid)
            self._total_exposure_usd = max(0.0, self._total_exposure_usd - removed_value)
        if hasattr(self, "_position_details"):
            self._position_details.pop(f"{bot_name}:{mid}", None)

    async def _on_order_filled(self, payload: Dict[str, Any]) -> None:
        """S120: Handle order_filled event from UserOrderWebSocket via EventBus.
        Confirms actual fill size/price and logs latency. Position already tracked
        optimistically at submission time — this validates the fill."""
        order_id = payload.get("id")
        if not order_id or order_id not in self._pending_orders:
            return
        pending = self._pending_orders.pop(order_id)
        filled_size = float(payload.get("size") or pending["size"])
        filled_price = float(payload.get("price") or pending["price"])
        latency_ms = round((time.monotonic() - pending["submitted_at"]) * 1000, 1)
        logger.info(
            "order_fill_confirmed",
            order_id=order_id,
            bot_name=pending["bot_name"],
            market_id=pending["market_id"],
            side=pending["side"],
            requested_size=pending["size"],
            filled_size=filled_size,
            filled_price=filled_price,
            fill_latency_ms=latency_ms,
        )
        if filled_size < pending["size"] * 0.99:
            logger.info(
                "partial_fill_detected",
                order_id=order_id,
                filled=filled_size,
                requested=pending["size"],
                remaining=round(pending["size"] - filled_size, 6),
            )

    async def _reap_stale_orders(self) -> None:
        """S120: Cancel orders that exceeded fill timeout. Called periodically from BaseEngine."""
        now = time.monotonic()
        stale = [
            (oid, info) for oid, info in self._pending_orders.items()
            if now - info["submitted_at"] > self._pending_order_timeout_s
        ]
        for order_id, info in stale:
            logger.warning(
                "order_fill_timeout",
                order_id=order_id,
                bot_name=info["bot_name"],
                market_id=info["market_id"],
                age_s=round(now - info["submitted_at"], 1),
            )
            if self.execution_engine and hasattr(self.execution_engine, "clob_adapter") and self.execution_engine.clob_adapter:
                try:
                    cancelled = await self.execution_engine.clob_adapter.cancel_order(order_id)
                    if cancelled:
                        logger.info("order_cancelled_stale", order_id=order_id)
                except Exception as e:
                    logger.warning("cancel_stale_order_failed", order_id=order_id, error=str(e))
            self._pending_orders.pop(order_id, None)

    async def mark_positions_halted(self) -> int:
        """B1: Mark all open positions as halted when kill switch engages.

        Prevents bots from re-entering halted positions on restart. Returns
        the count of positions updated (0 if DB unavailable or none open).
        """
        if not self.db or not self.db.session_factory:
            return 0
        try:
            from sqlalchemy import text as _sa_text
            async with self.db.get_session() as session:
                result = await session.execute(
                    _sa_text("UPDATE positions SET status = 'halted' WHERE status = 'open'")
                )
                await session.commit()
                return result.rowcount or 0
        except Exception as e:
            logger.error("mark_positions_halted failed: %s", e)
            return 0

    async def seed_positions_from_db(self, db) -> None:
        """Load open positions from DB at startup so the in-memory tracker is warm."""
        try:
            from sqlalchemy import select
            from base_engine.data.database import Position
            async with db.get_session() as session:
                result = await session.execute(
                    select(Position.bot_id, Position.market_id, Position.size, Position.entry_price)
                    .where(Position.status == "open")
                    .where(Position.side != "SELL")  # SELL rows = exit attempts, not open capital
                )
                count = 0
                total_exp = 0.0
                for row in result.all():
                    bot = row[0] or "unknown"
                    mid = str(row[1]) if row[1] else ""
                    size = float(row[2] or 0)
                    entry_price = float(row[3] or 0)
                    if mid:
                        self._open_position_markets.setdefault(bot, set()).add(mid)
                        value = size * entry_price
                        if value > 0:
                            self._position_exposure.setdefault(bot, {})[mid] = value
                            total_exp += value
                        count += 1
                self._total_exposure_usd = total_exp
            logger.info("OrderGateway: seeded %d open positions (exposure $%.2f) from DB", count, total_exp)
        except Exception as e:
            logger.warning("OrderGateway: position seed from DB failed (non-critical): %s", e)

    async def _restore_daily_exposure(self) -> None:
        """Seed _daily_exposure_usd from daily_counters table on startup.

        Called once from base_engine.start() after seed_positions_from_db().
        Uses absolute-set semantics: reads the last flushed total for each bot.
        See migration 036_daily_counters.sql for write-pattern documentation.

        Fail-open: any exception leaves _daily_exposure_usd empty — same as
        current restart behaviour (no regression).
        """
        if not self.db:
            return
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT bot_id, counter_value
                    FROM daily_counters
                    WHERE counter_date = CURRENT_DATE
                      AND counter_name = 'daily_exposure_usd'
                """))
                rows = result.fetchall()
            for bot_id, value in rows:
                self._daily_exposure_usd[bot_id] = float(value)
            logger.info("order_gateway_daily_exposure_restored", count=len(rows))
        except Exception as exc:
            logger.debug("order_gateway_daily_exposure_restore_failed", error=str(exc))

    async def _flush_daily_exposure(self) -> None:
        """Persist current _daily_exposure_usd totals to daily_counters.

        Called on SIGTERM (from base_engine.stop()) AND by the periodic flush loop
        in base_engine.start() every 60 seconds. The periodic flush bounds worst-case
        data loss on hard kills (SIGKILL, OOM) to ~60 seconds of exposure state.

        Uses absolute-set semantics: writes the current authoritative in-memory total.
        See migration 036_daily_counters.sql for write-pattern documentation.
        """
        if not self.db or not self._daily_exposure_usd:
            return
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import text
                for bot_id, value in self._daily_exposure_usd.items():
                    await session.execute(text("""
                        INSERT INTO daily_counters
                            (bot_id, counter_date, counter_name, counter_value, updated_at)
                        VALUES (:bot_id, CURRENT_DATE, 'daily_exposure_usd', :value, NOW())
                        ON CONFLICT (bot_id, counter_date, counter_name)
                        DO UPDATE SET
                            counter_value = EXCLUDED.counter_value,
                            updated_at    = NOW()
                    """), {"bot_id": bot_id, "value": value})
                await session.commit()
            logger.info("order_gateway_daily_exposure_flushed",
                        count=len(self._daily_exposure_usd))
        except Exception as exc:
            logger.warning("order_gateway_daily_exposure_flush_failed", error=str(exc))

    async def reconcile_exposure_from_db(self, db) -> None:
        """Rebuild in-memory exposure trackers from DB ground truth.

        Called periodically (every 5 min) to correct drift caused by:
        - SELL order DB write failures (position closed in-memory but DB write failed)
        - Manual DB position deletions
        - Any other cause of in-memory vs DB divergence
        """
        try:
            from sqlalchemy import select
            from base_engine.data.database import Position
            async with db.get_session() as session:
                result = await session.execute(
                    select(Position.bot_id, Position.market_id, Position.size, Position.entry_price, Position.side)
                    .where(Position.status == "open")
                    .where(Position.side != "SELL")  # SELL rows = exit attempts, not open capital
                )
                rows = result.all()

            new_open: Dict[str, Set[str]] = {}
            new_exposure: Dict[str, Dict[str, float]] = {}
            new_details: Dict[str, dict] = {}
            total_exp = 0.0

            for row in rows:
                bot = row[0] or "unknown"
                mid = str(row[1]) if row[1] else ""
                size = float(row[2] or 0)
                entry_price = float(row[3] or 0)
                side = row[4] or "YES"
                if not mid:
                    continue
                new_open.setdefault(bot, set()).add(mid)
                value = size * entry_price
                if value > 0:
                    # Accumulate per (bot, market_id) in case of multiple YES/NO rows
                    prev = new_exposure.setdefault(bot, {}).get(mid, 0.0)
                    new_exposure[bot][mid] = prev + value
                    total_exp += value
                # Rebuild position details so mid-life exit and CVaR can use them
                new_details[f"{bot}:{mid}"] = {
                    "side": side, "size": size, "price": entry_price, "predicted_prob": 0.5,
                }

            old_exp = self._total_exposure_usd
            self._open_position_markets = new_open
            self._position_exposure = new_exposure
            self._position_details = new_details
            self._total_exposure_usd = total_exp

            drift = old_exp - total_exp
            if abs(drift) > 1.0:
                logger.info(
                    "OrderGateway: exposure reconciled (drift=%.2f old=%.2f new=%.2f positions=%d)",
                    drift, old_exp, total_exp, len(rows),
                )
        except Exception as e:
            logger.warning("OrderGateway: exposure reconciliation failed (non-critical): %s", e)

    async def place_order(
        self,
        bot_name: str,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        confidence: float = 0.0,
        prediction: Optional[float] = None,
        order_type: str = "market",
        correlation_id: Optional[str] = None,
        bid: float = 0.0,
        ask: float = 0.0,
        event_data: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Place order through kill switch, risk, and coordinator. Returns same shape as execution_engine.place_order.
        """
        self._bot_names_used.add(bot_name)

        # Multi-layer kill switch: bot-level + portfolio-level + system-level
        if self.multi_kill_switch is not None:
            try:
                if not await self.multi_kill_switch.should_trade(bot_name):
                    logger.warning("Order blocked: multi-layer kill switch", bot_name=bot_name, market_id=market_id)
                    _db_rej = self.db or (getattr(self.paper_trading_engine, "db", None) if self.paper_trading_engine else None)
                    if _db_rej and hasattr(_db_rej, "insert_shadow_fill"):
                        try:
                            await _db_rej.insert_shadow_fill(
                                bot_name=bot_name, market_id=market_id, token_id=token_id,
                                side=side, order_size_shares=size,
                                order_size_usd=round(size * price, 4),
                                signal_price=price, confidence=confidence,
                                trade_executed=False, correlation_id=correlation_id,
                                rejection_type="kill_switch", event_data=event_data,
                            )
                        except Exception as _sf_err:
                            logger.critical("shadow_fill_insert_failed", error=str(_sf_err), bot_name=bot_name, market_id=market_id, rejection_type="kill_switch")
                    return {"success": False, "error": "Kill switch engaged (multi-layer)"}
            except Exception as e:
                logger.warning("Multi kill switch check failed, falling back to basic: %s", e)
                # Fall through to basic kill switch check below

        if self.kill_switch is not None:
            if await self.kill_switch.is_engaged():
                logger.warning("Order blocked: kill switch engaged", bot_name=bot_name, market_id=market_id)
                _db_rej = self.db or (getattr(self.paper_trading_engine, "db", None) if self.paper_trading_engine else None)
                if _db_rej and hasattr(_db_rej, "insert_shadow_fill"):
                    try:
                        await _db_rej.insert_shadow_fill(
                            bot_name=bot_name, market_id=market_id, token_id=token_id,
                            side=side, order_size_shares=size,
                            order_size_usd=round(size * price, 4),
                            signal_price=price, confidence=confidence,
                            trade_executed=False, correlation_id=correlation_id,
                            rejection_type="kill_switch", event_data=event_data,
                        )
                    except Exception as _sf_err:
                        logger.critical("shadow_fill_insert_failed", error=str(_sf_err), bot_name=bot_name, market_id=market_id, rejection_type="kill_switch")
                return {"success": False, "error": "Kill switch engaged"}

        # S172 1E-b: Pre-trade market validation — resolve aliases, warn on unknown
        if self._market_index is not None:
            _known = market_id in self._market_index
            if not _known and self._market_index_by_cid:
                _by_cid = self._market_index_by_cid.get(market_id)
                if _by_cid:
                    _canonical = str(_by_cid.get("id", market_id))
                    logger.debug("gateway_alias_resolved", alias=market_id[:20], canonical=_canonical[:20], bot_name=bot_name)
                    market_id = _canonical
                    _known = True
            if not _known:
                logger.warning("gateway_unknown_market", market_id=market_id[:20], bot_name=bot_name,
                               note="market not in index — proceeding but may fail at execution")

        # Canary deployment: graduated capital scaling (0=off, 1=5%, 2=25%, 3=50%, 4=100%)
        _canary = getattr(settings, "CANARY_STAGE", 0)
        if _canary > 0:
            _canary_pcts = {1: 0.05, 2: 0.25, 3: 0.50, 4: 1.0}
            _canary_mult = _canary_pcts.get(_canary, 1.0)
            size = size * _canary_mult

        # Determine if this is a SELL (exit/close) order — exits bypass most pre-trade filters
        _is_sell = side.upper() == "SELL"

        # Bug C fix: skip SELL orders with zero size (would create phantom trade records)
        if _is_sell and size <= 0:
            logger.warning(
                "Skipping SELL order with zero size — no position to close",
                bot_name=bot_name, market_id=market_id, token_id=token_id,
            )
            return {"success": False, "error": "SELL order size is 0 — no position to close"}

        # S94: RTDS fast-path flag — skip heavyweight checks for copy trades
        _rtds_fast = (
            not _is_sell
            and correlation_id
            and str(correlation_id).startswith("rtds:")
            and getattr(settings, "MIRROR_RTDS_FAST_PATH", False)
        )

        # Drawdown controller: graduated position reduction during losing streaks
        # S94: Skip for RTDS fast-path (MirrorBot has own daily caps + position limits)
        if self.drawdown_controller is not None and not _is_sell and not _rtds_fast:
            try:
                paper_engine = self.paper_trading_engine
                portfolio = {}
                if paper_engine and paper_engine.enabled:
                    portfolio = {
                        "starting_capital": getattr(settings, "TOTAL_CAPITAL", 10000.0),
                        "realized_pnl_today": getattr(paper_engine, "realized_pnl_today", {}).get(bot_name, 0.0),
                    }
                dd_status = await self.drawdown_controller.check_drawdown_status(portfolio)
                dd_multiplier = dd_status.get("position_multiplier", 1.0)
                dd_state = dd_status.get("status", "normal")
                if dd_state == "halted":
                    logger.warning(
                        "Order blocked: drawdown halt", bot_name=bot_name, market_id=market_id,
                        drawdown=dd_status.get("drawdown"), message=dd_status.get("message"),
                    )
                    return {"success": False, "error": dd_status.get("message", "Drawdown halt")}
                if dd_multiplier < 1.0:
                    original_size = size
                    size = size * dd_multiplier
                    logger.info(
                        "Drawdown position reduction",
                        bot_name=bot_name, market_id=market_id,
                        state=dd_state, multiplier=dd_multiplier,
                        original_size=round(original_size, 2), reduced_size=round(size, 2),
                    )
            except Exception as e:
                logger.warning("Drawdown check failed (non-blocking, trade proceeds without drawdown guard): %s", e)

        # L4: Adverse selection sizing — reduce position size for markets with high adverse selection
        # S94: Skip for RTDS fast-path
        if not _is_sell and not _rtds_fast and getattr(settings, "L4_ADVERSE_SIZING_ENABLED", True):
            try:
                adverse_mult = await self._get_adverse_sizing_mult(market_id)
                if adverse_mult < 1.0:
                    original_size = size
                    size = size * adverse_mult
                    logger.info(
                        "L4 adverse sizing: %s adverse_mult=%.2f size=%.2f→%.2f",
                        market_id, adverse_mult, original_size, size,
                    )
            except Exception as e:
                logger.warning("L4 adverse sizing check failed (trading at full size): %s", e)

        # RL Trade Timing Agent (optional pre-filter: should we trade now, wait, or skip?)
        if self.rl_agent and getattr(settings, "RL_TRADE_TIMING_ENABLED", False) and not _is_sell:
            try:
                rl_market_state = {
                    "confidence": confidence,
                    "spread": self._get_spread_for_rl(market_id),
                    "volatility": self._get_volatility_for_rl(market_id),
                    "regime": self._get_regime_for_rl(),
                    "hour": datetime.now(timezone.utc).hour,
                    "market_id": market_id,
                }
                rl_action, rl_q_value = await self.rl_agent.decide(rl_market_state)
                if rl_action == 2:  # SKIP
                    logger.debug("RL agent: SKIP", market_id=market_id, q_value=round(rl_q_value, 3))
                    return {"success": False, "error": "rl_timing_skip", "q_value": rl_q_value}
                elif rl_action == 1:  # WAIT
                    logger.debug("RL agent: WAIT", market_id=market_id, q_value=round(rl_q_value, 3))
                    return {"success": False, "error": "rl_timing_wait", "q_value": rl_q_value}
                # action == 0: TRADE_NOW — continue pipeline
            except Exception as e:
                logger.debug("RL agent error (non-fatal, proceeding): %s", e)

        # NegRisk defense: block BUY on multi-outcome negRisk markets (tokens may be unsellable)
        if side.upper() != "SELL":
            if not self._can_exit(market_id):
                logger.warning("Order blocked: NegRisk multi-outcome market (tokens may be unsellable)",
                               bot_name=bot_name, market_id=market_id)
                return {"success": False, "error": "NegRisk multi-outcome market — cannot verify sell path"}

        # ── Component-level latency tracking (identical for paper + live) ──
        _t_risk_start = time.monotonic()

        # Skip risk limits for SELL orders (closing positions) — they need to exit regardless
        # S94: Skip for RTDS fast-path (MirrorBot has own risk: position caps, category caps,
        # exposure caps, daily caps in _execute_mirror_trade + _can_open_position)
        if self.risk_manager is not None and not _is_sell and not _rtds_fast:
            try:
                # S97: WeatherBot skips CVaR Monte Carlo — has own group/city exposure limits
                # S133: EsportsBot skips CVaR — has own per-game/tournament/team exposure caps
                # S213: EsportsBotV2 also skips — same exposure-cap rationale as EsportsBot
                # (parity with the S210 354c84e wiring fix completing in S213)
                _skip_cvar = (bot_name in ("WeatherBot", "EsportsBot", "EsportsBotV2"))
                risk_check = await self.risk_manager.check_risk_limits(
                    bot_name, market_id, size, price, confidence, prediction=prediction,
                    skip_cvar=_skip_cvar,
                )
                if not risk_check.get("allowed", True):
                    reasons = risk_check.get("reasons", [])
                    # Classify rejection reasons to distinguish cap hits from other limit types
                    size_reasons = [r for r in reasons if "Position $" in r and "exceeds max" in r]
                    exposure_reasons = [r for r in reasons if "Total exposure" in r and "exceeds max" in r]
                    non_size_reasons = [r for r in reasons if r not in size_reasons and r not in exposure_reasons]
                    if not non_size_reasons and (size_reasons or exposure_reasons):
                        # P0.A: Hard reject — silent clamp removed. Risk cap must be respected exactly.
                        msg = "; ".join(reasons) if reasons else "Risk cap exceeded"
                        logger.critical(
                            "order_risk_cap_hard_rejected",
                            bot_name=bot_name,
                            market_id=market_id,
                            requested_size=round(size, 4),
                            requested_value_usd=round(size * price, 2),
                            reasons=reasons,
                        )
                        # P0.18: Record cap rejection in shadow_fills for P0.20 coverage tracking.
                        _db_rej = self.db or (getattr(self.paper_trading_engine, "db", None) if self.paper_trading_engine else None)
                        if _db_rej and hasattr(_db_rej, "insert_shadow_fill"):
                            try:
                                await _db_rej.insert_shadow_fill(
                                    bot_name=bot_name, market_id=market_id, token_id=token_id,
                                    side=side, order_size_shares=size,
                                    order_size_usd=round(size * price, 4),
                                    signal_price=price, confidence=confidence,
                                    trade_executed=False, correlation_id=correlation_id,
                                    rejection_type="risk_cap", event_data=event_data,
                                )
                            except Exception as _sf_err:
                                logger.critical("shadow_fill_insert_failed", error=str(_sf_err), bot_name=bot_name, market_id=market_id, rejection_type="risk_cap")
                        return {"success": False, "error": msg, "reasons": reasons, "rejection_type": "risk_cap"}
                    else:
                        msg = "; ".join(reasons) if reasons else "Risk limits exceeded"
                        logger.warning("Order blocked: risk limits", bot_name=bot_name, market_id=market_id, reasons=reasons)
                        return {"success": False, "error": msg, "reasons": reasons}
            except Exception as e:
                logger.warning("Order blocked: risk check failed", bot_name=bot_name, error=str(e))
                return {"success": False, "error": f"Risk check failed: {e}"}

        _t_risk_end = time.monotonic()

        # Cascade + liquidity checks: run in parallel (were sequential: 30-100ms)
        # Liquidity check blocks in all modes (paper = production).
        _cascade_enabled = getattr(settings, "CASCADE_CHECK_ENABLED", False) and self.cascade_detector is not None
        _liquidity_enabled = self.liquidity_guardian is not None

        if _cascade_enabled or _liquidity_enabled:
            async def _cascade_check():
                if not _cascade_enabled:
                    return None
                # Skip cascade API call for RTDS fast-copy trades (saves 50-100ms).
                if (correlation_id and str(correlation_id).startswith("rtds:")
                        and getattr(settings, "MIRROR_SKIP_LIQUIDITY_RTDS", False)):
                    return None
                # S97: Skip cascade for WeatherBot — sole weather trader, no cascade risk
                if bot_name == "WeatherBot":
                    return None
                return await self.cascade_detector.detect(market_id, window_hours=6)

            async def _liquidity_check():
                if not _liquidity_enabled:
                    return None
                # Session 82: Skip liquidity API call for RTDS fast-copy trades (saves 100-300ms).
                # Gated by MIRROR_SKIP_LIQUIDITY_RTDS=true AND correlation_id prefix "rtds:".
                if (correlation_id and str(correlation_id).startswith("rtds:")
                        and getattr(settings, "MIRROR_SKIP_LIQUIDITY_RTDS", False)):
                    return None
                # S180 (2H-3): EB blanket skip removed. Previously all EsportsBot
                # variants bypassed check_liquidity entirely. Now they hit both the
                # existing 3% slippage gate and the new per-bot depth gate (2H-1/2).
                # Rollback if too restrictive: set LIQUIDITY_DEPTH_MULT_EB=0 in .env.
                # S141: Skip liquidity check for MirrorBot SELL exits — dead markets
                # have zero bids, blocking stop-loss exits indefinitely. Paper fill
                # model handles simulation; in live mode the CLOB rejects if illiquid.
                if _is_sell and bot_name == "MirrorBot":
                    return None
                # Look up condition_id from market index for CLOB API order book query
                _cid = ""
                if self._market_index:
                    _mdata = self._market_index.get(str(market_id))
                    if _mdata:
                        _cid = str(_mdata.get("conditionId") or _mdata.get("condition_id") or "")
                # S180 (2H-3): per-bot depth multiplier selection. Blocks trades
                # larger than (top-5 orderbook sum / multiplier). Multiplier=0 disables.
                if bot_name == "WeatherBot":
                    _depth_mult = settings.LIQUIDITY_DEPTH_MULT_WB
                elif bot_name == "MirrorBot":
                    _depth_mult = settings.LIQUIDITY_DEPTH_MULT_MB
                elif bot_name in ("EsportsBot", "EsportsLiveBot", "EsportsSeriesBot", "EsportsBotV2"):
                    _depth_mult = settings.LIQUIDITY_DEPTH_MULT_EB
                else:
                    _depth_mult = settings.LIQUIDITY_DEPTH_DEFAULT
                return await self.liquidity_guardian.check_liquidity(
                    market_id=market_id, token_id=token_id, trade_size=size, side=side,
                    condition_id=_cid, depth_multiplier=_depth_mult,
                )

            try:
                cascade_result, liq_result = await asyncio.gather(
                    _cascade_check(), _liquidity_check(), return_exceptions=True,
                )
            except Exception:
                cascade_result, liq_result = None, None

            if not isinstance(cascade_result, BaseException) and cascade_result is not None:
                if cascade_result.get("cascade_active"):
                    logger.warning("Order skipped: cascade active", market_id=market_id, bot_name=bot_name)
                    return {"success": False, "error": "Cascade active (order skipped)"}

            if not isinstance(liq_result, BaseException) and liq_result is not None:
                if not liq_result.get("can_execute", True):
                    rec = liq_result.get("recommendation", "abort")
                    reason = liq_result.get("reason", "liquidity check failed")
                    logger.warning("Order blocked: liquidity", market_id=market_id, reason=reason, rec=rec)
                    return {"success": False, "error": f"Liquidity check: {reason} ({rec})"}

        # Pre-validation: check paper trading balance before reserving position
        # YES/NO/BUY all require cash (buying tokens); only SELL is closing a position
        if getattr(settings, "SIMULATION_MODE", False) and self.paper_trading_engine and self.paper_trading_engine.enabled:
            order_cost = size * price
            if side.upper() != "SELL" and order_cost > self.paper_trading_engine.cash:
                logger.debug(
                    "Order pre-rejected: insufficient paper cash (need $%.2f, have $%.2f)",
                    order_cost, self.paper_trading_engine.cash,
                )
                return {"success": False, "error": f"Insufficient paper cash: need ${order_cost:.2f}, have ${self.paper_trading_engine.cash:.2f}"}

        # NegRisk can_exit pre-check: warn if selling a NegRisk market (exits may need conversion)
        if _is_sell and self._market_index:
            _mdata_nr = self._market_index.get(str(market_id))
            if _mdata_nr:
                _neg_risk = _mdata_nr.get("neg_risk") or _mdata_nr.get("negRisk") or False
                _outcome_count = int(_mdata_nr.get("outcome_count") or _mdata_nr.get("outcomeCount") or 2)
                if _neg_risk and _outcome_count > 2:
                    logger.warning(
                        "NegRisk market exit: may require NegRiskAdapter conversion",
                        market_id=market_id, outcome_count=_outcome_count,
                    )

        # Reserve under bot_name so CryptoBot/PoliticalBot are separate entities (each can hold same market+side).
        # SELLs (exits) get a longer timeout — failing to exit is worse than failing to enter.
        # On resource-constrained VPS (high CPU steal), DB advisory locks can take >5s to acquire.
        # S94: RTDS BUY trades skip coordinator — MirrorBot has in-memory dedup via _open_positions,
        # and the 9 other bots are disabled. Saves 72-464ms coord_ms per BUY.
        # IMPORTANT: Re-enable when other bots are activated (set MIRROR_SKIP_COORDINATOR_BUY=false).
        _t_coord_start = time.monotonic()
        _coord_timeout = 15.0 if _is_sell else 5.0
        _skip_coord = (
            not _is_sell
            and correlation_id
            and str(correlation_id).startswith("rtds:")
            and getattr(settings, "MIRROR_SKIP_COORDINATOR_BUY", False)
        )
        # S97: Skip coordinator for WeatherBot BUYs — sole weather trader, no contention
        if not _is_sell and bot_name == "WeatherBot" and getattr(settings, "WEATHER_SKIP_COORDINATOR_BUY", True):
            _skip_coord = True
        if self.trade_coordinator is not None and not _skip_coord:
            try:
                _reserved = await asyncio.wait_for(
                    self.trade_coordinator.reserve_position(market_id, side, token_id, reserving_bot_id=bot_name),
                    timeout=_coord_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Order blocked: trade coordinator reserve timed out after %.0fs",
                    _coord_timeout, market_id=market_id, side=side,
                )
                return {"success": False, "error": "Trade coordinator reserve timed out"}
            if not _reserved:
                logger.warning("Order blocked: position already taken or could not reserve", market_id=market_id, side=side)
                return {"success": False, "error": "Position already taken or could not reserve"}

        # Adverse selection gate: reject trades where spread < 2x estimated adverse selection cost
        if self.adverse_selection_tracker and side.upper() != "SELL":
            try:
                as_cost = getattr(self.adverse_selection_tracker, "get_adverse_selection_cost", lambda mid: None)(market_id)
                if as_cost is not None and as_cost > 0:
                    # Minimum profitable spread must be 2x adverse selection cost
                    min_spread = 2.0 * as_cost
                    # Use taker fee as a proxy for effective spread if no orderbook
                    effective_spread = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0
                    if effective_spread < min_spread:
                        logger.info(
                            "Order blocked: adverse selection too high",
                            market_id=market_id, as_cost=round(as_cost, 4),
                            min_spread=round(min_spread, 4), effective_spread=round(effective_spread, 4),
                        )
                        return {"success": False, "error": f"Adverse selection cost ({as_cost:.4f}) too high for spread ({effective_spread:.4f})"}
            except Exception:
                pass  # Non-fatal: proceed without AS gate

        # OrderBookAnalyzer: improve limit price when available
        effective_price = price
        if self.orderbook_analyzer and order_type == "limit" and prediction is not None:
            try:
                # Get orderbook snapshot from execution engine or client
                client = getattr(self.execution_engine, "client", None)
                if client and hasattr(client, "get_order_book"):
                    book = await client.get_order_book(market_id)
                    analysis = self.orderbook_analyzer.analyze(book)
                    if analysis and analysis.get("midpoint"):
                        # Use SmartOrderPlacer for intelligent limit price
                        if self.smart_order_placer:
                            effective_price = self.smart_order_placer.compute_limit_price(
                                predicted_prob=prediction,
                                current_price=price,
                                side=side,
                                urgency=0.5,
                            )
                            logger.info(
                                "OrderBook price improvement",
                                original=price,
                                improved=effective_price,
                                spread=analysis.get("spread_pct", 0),
                                market_id=market_id,
                            )
            except Exception as e:
                logger.warning("OrderBook analysis failed (using original price): %s", e)

        # Tick size enforcement: Polymarket uses 0.01 (1 cent) tick increments.
        # Round price to nearest valid tick to prevent invalid orders in live mode.
        effective_price = round(effective_price, 2)

        # S115: Pre-trade book walk + edge check — applies to BOTH paper and live.
        # Snapshots real L2 book, computes VWAP, rejects if edge eroded.
        # Records every signal to shadow_fills for retroactive P&L analysis.
        _is_buy = str(side).upper() != "SELL"
        _shadow_book_snapshot = None
        _shadow_best_ask = None
        _shadow_best_bid = None
        _shadow_spread = 0.0
        _shadow_depth_best = 0.0
        _shadow_total_depth = 0.0
        _shadow_vwap = effective_price
        _shadow_slippage = 0.0
        _shadow_fill_frac = 1.0
        _shadow_book_walk_used = False
        # P0.3/P0.3b: intended-walk results and sizing (from event_data set by base_bot.place_order)
        _intended_vwap: Optional[float] = None
        _intended_slippage: Optional[float] = None
        _intended_fill_frac: Optional[float] = None
        _intended_walk_error: Optional[str] = None
        _intended_size_usd_for_write: Optional[float] = (event_data or {}).get("intended_size_usd")
        _intended_size_shares_for_write: Optional[float] = (
            _intended_size_usd_for_write / effective_price
            if _intended_size_usd_for_write and effective_price > 0 else None
        )

        if self._orderbook_tracker and token_id:
            try:
                _book = await self._orderbook_tracker.snapshot_order_book(
                    token_id=token_id, condition_id=str(market_id))
                if _book and not _book.get("error"):
                    _raw_asks = _book.get("asks", [])
                    _raw_bids = _book.get("bids", [])
                    if _raw_asks:
                        try:
                            _shadow_best_ask = float(_raw_asks[0].get("price", 0))
                        except (ValueError, TypeError):
                            pass
                    if _raw_bids:
                        try:
                            _shadow_best_bid = float(_raw_bids[0].get("price", 0))
                        except (ValueError, TypeError):
                            pass
                    if _shadow_best_ask and _shadow_best_bid:
                        _shadow_spread = _shadow_best_ask - _shadow_best_bid

                    if _is_buy:
                        # BUY: walk ask side
                        from base_engine.execution.paper_trading import _vwap_from_book
                        _shadow_book_snapshot = _raw_asks[:20]
                        for _lvl in _raw_asks:
                            try:
                                _shadow_total_depth += float(_lvl.get("price", 0)) * float(_lvl.get("size", 0))
                            except (ValueError, TypeError):
                                pass
                        if _raw_asks:
                            try:
                                _shadow_depth_best = float(_raw_asks[0].get("price", 0)) * float(_raw_asks[0].get("size", 0))
                            except (ValueError, TypeError):
                                pass
                        _event = event_data or {}
                        _whale_usd = _event.get("whale_size_usd", 0)
                        _whale_shares = _whale_usd / effective_price if effective_price > 0 else 0
                        _bw = _vwap_from_book(_raw_asks, size, _whale_shares)
                        if _bw:
                            _shadow_vwap, _shadow_fill_frac, _shadow_slippage = _bw
                            _shadow_book_walk_used = True
                    else:
                        # S121: SELL — walk bid side for realistic exit slippage
                        from base_engine.execution.paper_trading import _vwap_from_bids
                        _shadow_book_snapshot = _raw_bids[:20]
                        for _lvl in _raw_bids:
                            try:
                                _shadow_total_depth += float(_lvl.get("price", 0)) * float(_lvl.get("size", 0))
                            except (ValueError, TypeError):
                                pass
                        if _raw_bids:
                            try:
                                _shadow_depth_best = float(_raw_bids[0].get("price", 0)) * float(_raw_bids[0].get("size", 0))
                            except (ValueError, TypeError):
                                pass
                        _bw = _vwap_from_bids(_raw_bids, size)
                        if _bw:
                            _shadow_vwap, _shadow_fill_frac, _shadow_slippage = _bw
                            _shadow_book_walk_used = True
                    # P0.3: Walk at intended Kelly size (pre-cap) for counterfactual analysis.
                    _intended_size_usd_val = (event_data or {}).get("intended_size_usd")
                    if _shadow_book_walk_used and _intended_size_usd_val and effective_price > 0:
                        _intended_shares = _intended_size_usd_val / effective_price
                        try:
                            if _is_buy:
                                _bw_int = _vwap_from_book(_raw_asks, _intended_shares, _whale_shares)
                            else:
                                _bw_int = _vwap_from_bids(_raw_bids, _intended_shares)
                            if _bw_int:
                                _intended_vwap, _intended_fill_frac, _intended_slippage = _bw_int
                        except Exception as _bw_int_err:
                            _intended_walk_error = str(_bw_int_err)
            except Exception as _bw_err:
                logger.debug("order_gateway_book_walk_failed", error=str(_bw_err), market_id=market_id)

        # S120: Edge-at-VWAP gate — reject if real book price erases edge.
        # Skip for WeatherBot: weather CLOB books have structural 99¢ asks with
        # real depth at 5-30¢. The confidence-vs-price check (designed for markets
        # where best_ask ≈ fair value) doesn't work when best_ask is an outlier.
        # WeatherBot's own edge checks in _analyze_group() + compute_edges() are
        # the real gate. The paper engine's VWAP fill gives realistic execution.
        if _is_buy and confidence is not None and confidence > 0 and _shadow_book_walk_used and bot_name != "WeatherBot":
            _edge_at_fill = confidence - _shadow_vwap
            if _shadow_spread > 0.80:
                _edge_at_fill = -1.0  # force rejection — dead/illiquid market
                logger.info("order_dead_market_spread", market_id=market_id,
                            spread=round(_shadow_spread, 4),
                            best_bid=_shadow_best_bid, best_ask=_shadow_best_ask,
                            bot_name=bot_name)
            if _edge_at_fill <= 0:
                logger.info("order_edge_eroded", market_id=market_id,
                            confidence=round(confidence, 4),
                            best_ask=round(_shadow_best_ask, 4),
                            signal_price=round(effective_price, 4),
                            edge_at_fill=round(_edge_at_fill, 4),
                            bot_name=bot_name,
                            mode="paper" if getattr(settings, "SIMULATION_MODE", False) else "live")
                # Record shadow fill for rejected trade
                _db = self.db or (self.paper_trading_engine.db if self.paper_trading_engine else None)
                if _db and hasattr(_db, "insert_shadow_fill"):
                    _scan_start = (event_data or {}).get("scan_start_mono")
                    _latency = (time.monotonic() - _scan_start) * 1000 if _scan_start else None
                    try:
                        await _db.insert_shadow_fill(
                            bot_name=bot_name, market_id=market_id, token_id=token_id,
                            side="BUY", order_size_shares=size,
                            order_size_usd=size * effective_price,
                            signal_price=effective_price, confidence=confidence,
                            edge_at_signal=(confidence - effective_price),
                            latency_ms=_latency,
                            book_snapshot=_shadow_book_snapshot,
                            best_ask=_shadow_best_ask, best_bid=_shadow_best_bid,
                            spread=_shadow_spread,
                            depth_at_best_usd=_shadow_depth_best,
                            total_depth_usd=_shadow_total_depth,
                            vwap_fill_price=_shadow_vwap,
                            book_walk_slippage=_shadow_slippage,
                            fill_fraction=_shadow_fill_frac,
                            edge_at_vwap=_edge_at_fill,
                            trade_executed=False, execution_price=None,
                            correlation_id=correlation_id,
                            model_name=None, event_data=event_data,
                            vwap_at_intended=_intended_vwap,
                            slippage_at_intended=_intended_slippage,
                            fill_frac_at_intended=_intended_fill_frac,
                            intended_walk_error=_intended_walk_error,
                            intended_size_usd=_intended_size_usd_for_write,
                            intended_size_shares=_intended_size_shares_for_write,
                        )
                    except Exception as _sf_err:
                        logger.critical("shadow_fill_insert_failed", error=str(_sf_err), bot_name=bot_name, market_id=market_id, rejection_type="edge_eroded")
                if self.trade_coordinator is not None:
                    try:
                        await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                    except Exception:
                        pass
                return {"success": False, "error": f"Edge eroded: conf={confidence:.4f} <= vwap={_shadow_vwap:.4f}"}

        # Paper trading: full pipeline (risk, coordinator) then record order instead of CLOB
        if getattr(settings, "SIMULATION_MODE", False) and self.paper_trading_engine and self.paper_trading_engine.enabled:
            # In binary prediction markets, YES/NO indicate which token to buy, not trade direction.
            # Both YES and NO are BUY orders (buying that side's token).
            # SELL only happens when closing an existing position.
            paper_side = "SELL" if str(side).upper() == "SELL" else "BUY"
            logger.info("Paper trade attempt", market_id=market_id, side=paper_side, size=round(size, 2), price=round(effective_price, 6), bot_name=bot_name, cash=round(self.paper_trading_engine.cash, 2))
            # S115: Pass book walk results to paper engine so it fills at VWAP
            if _shadow_book_walk_used:
                if event_data is None:
                    event_data = {}
                event_data["_shadow_book_walk_used"] = True
                event_data["_shadow_vwap"] = _shadow_vwap
                event_data["_shadow_fill_frac"] = _shadow_fill_frac
                event_data["_shadow_slippage"] = _shadow_slippage
                event_data["_shadow_book_snapshot"] = _shadow_book_snapshot
                event_data["_shadow_best_ask"] = _shadow_best_ask
                event_data["_shadow_best_bid"] = _shadow_best_bid
                event_data["_shadow_spread"] = _shadow_spread
                event_data["_shadow_depth_best"] = _shadow_depth_best
                event_data["_shadow_total_depth"] = _shadow_total_depth
                # P0.5: Pass intended-walk results so paper_trading can write them to shadow_fills.
                event_data["_intended_vwap"] = _intended_vwap
                event_data["_intended_slippage"] = _intended_slippage
                event_data["_intended_fill_frac"] = _intended_fill_frac
                event_data["_intended_walk_error"] = _intended_walk_error
            try:
                _t_coord_end = time.monotonic()
                t0 = time.monotonic()
                # S91: Look up 24h volume for fill probability model
                # S100: Look up bestBid/bestAsk for realistic spread in fill model
                _paper_volume = 0.0
                _paper_bid = bid      # preserve caller-supplied values if nonzero
                _paper_ask = ask
                if self._market_index:
                    # S100: check both numeric id and condition_id (MirrorBot uses 0x hashes)
                    _mdata_vol = (
                        self._market_index.get(str(market_id))
                        or self._market_index_by_cid.get(str(market_id))
                    )
                    if _mdata_vol:
                        _paper_volume = float(_mdata_vol.get("volume") or _mdata_vol.get("volume24hr") or 0)
                        if _paper_bid <= 0.0:
                            _paper_bid = float(_mdata_vol.get("bestBid") or _mdata_vol.get("best_bid") or 0)
                        if _paper_ask <= 0.0:
                            _paper_ask = float(_mdata_vol.get("bestAsk") or _mdata_vol.get("best_ask") or 0)
                        # S100: fallback — derive spread from tokens array (API scan data)
                        if _paper_bid <= 0.0 and _paper_ask <= 0.0:
                            try:
                                _tokens = _mdata_vol.get("tokens") or []
                                if len(_tokens) >= 2:
                                    _p0 = float(_tokens[0].get("price") or 0)
                                    _p1 = float(_tokens[1].get("price") or 0)
                                    if _p0 > 0 and _p1 > 0:
                                        _spread = abs(1.0 - _p0 - _p1)
                                        _paper_bid = effective_price - _spread / 2
                                        _paper_ask = effective_price + _spread / 2
                            except (ValueError, TypeError, IndexError):
                                pass
                # S107 Fix 3: fallback volume from event_data when market_index has no data
                if _paper_volume <= 0.0:
                    _paper_volume = float((event_data or {}).get("volume_24h") or 0)
                # S100: Extract signal latency from event_data for alpha decay
                _scan_start = (event_data or {}).get("scan_start_mono")
                _signal_latency_ms = None
                if _scan_start is not None:
                    _signal_latency_ms = (time.monotonic() - _scan_start) * 1000
                # S133: Pass on_buy_fill callback so exposure is tracked UNDER
                # the paper engine's _trade_lock — prevents stale-read race where
                # another bot reads daily_exposure before this trade is reflected.
                result = await self.paper_trading_engine.place_order(
                    market_id=market_id,
                    token_id=token_id,
                    side=paper_side,
                    size=size,
                    price=effective_price,
                    bot_name=bot_name,
                    confidence=confidence,
                    original_side=str(side).upper(),
                    order_type=order_type,
                    correlation_id=correlation_id,
                    latency_ms=_signal_latency_ms,
                    bid=_paper_bid,
                    ask=_paper_ask,
                    volume=_paper_volume,
                    event_data=event_data,
                    on_buy_fill=self._track_position_open,
                )
                latency_ms = (time.monotonic() - t0) * 1000
                if not result.get("success"):
                    logger.warning("Paper trade FAILED", market_id=market_id, bot_name=bot_name, error=result.get("error", "unknown"), result=str(result)[:200])
                # Latency logging: identical for paper and live (paper trading IS regular trading)
                if getattr(settings, "LOG_ORDER_LATENCY", True):
                    _risk_ms = round((_t_risk_end - _t_risk_start) * 1000, 1)
                    _coord_ms = round((_t_coord_end - _t_coord_start) * 1000, 1)
                    _exec_ms = round(latency_ms, 1)
                    _total_ms = round((_t_risk_end - _t_risk_start + _t_coord_end - _t_coord_start) * 1000 + latency_ms, 1)
                    logger.info(
                        "Order latency",
                        bot_name=bot_name,
                        market_id=market_id,
                        latency_ms=round(latency_ms, 1),
                        success=result.get("success", False),
                    )
                    logger.info(
                        "Order latency breakdown",
                        bot_name=bot_name,
                        market_id=market_id,
                        risk_ms=_risk_ms,
                        coord_ms=_coord_ms,
                        exec_ms=_exec_ms,
                        total_ms=_total_ms,
                    )
                    alert_ms = getattr(settings, "ORDER_LATENCY_ALERT_MS", 5000)
                    if latency_ms > alert_ms:
                        logger.warning(
                            "Order latency exceeded threshold",
                            bot_name=bot_name,
                            market_id=market_id,
                            latency_ms=round(latency_ms, 1),
                            threshold_ms=alert_ms,
                        )
                # Feed Prometheus (identical for paper and live)
                try:
                    from base_engine.monitoring.metrics_collector import metrics_collector, ORDER_PIPELINE_LATENCY
                    metrics_collector.record_trade(bot_name, str(side), result.get("success", False), latency_ms / 1000.0)
                    ORDER_PIPELINE_LATENCY.labels(bot_name=bot_name, component="risk").observe(_risk_ms / 1000.0)
                    ORDER_PIPELINE_LATENCY.labels(bot_name=bot_name, component="coord").observe(_coord_ms / 1000.0)
                    ORDER_PIPELINE_LATENCY.labels(bot_name=bot_name, component="exec").observe(_exec_ms / 1000.0)
                except Exception:
                    pass  # Metrics are best-effort
                # Use actual filled size (may differ from requested size on
                # partial fills in paper trading).  Falls back to requested
                # size when result has no "filled" key.
                _filled_size = result.get("filled", size) if result.get("success") else size
                if self.trade_coordinator is not None:
                    if result.get("success") and _filled_size > 0:
                        try:
                            await self.trade_coordinator.confirm_position(market_id, side, _filled_size, effective_price, source_bot=bot_name, bot_id=bot_name, token_id=token_id)
                        except Exception as _conf_err:
                            # Theoretical hardening: paper trade succeeded (cash deducted) but DB
                            # confirmation failed. In-memory position exists; 5-min reconcile will
                            # correct any drift. Log so operator can investigate.
                            logger.warning(
                                "confirm_position failed after successful paper trade (reconcile will correct): "
                                "market=%s bot=%s error=%s",
                                market_id, bot_name, _conf_err,
                            )
                        if _is_sell:
                            self._track_position_close(bot_name, market_id)  # C3 FIX
                        # S133: BUY tracking now happens inside paper engine's _trade_lock
                        # via on_buy_fill callback — no post-return tracking needed.
                    elif result.get("success") and _filled_size <= 0:
                        # S107: Paper engine returned success but filled 0 tokens (e.g. idempotent
                        # duplicate). Do NOT create a position — release reservation instead.
                        logger.warning("Paper trade success but filled_size=0, skipping position creation",
                                       bot_name=bot_name, market_id=market_id, side=side)
                        try:
                            await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                        except Exception:
                            pass
                    else:
                        try:
                            await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                        except Exception as _rel_err:  # H2 FIX: release_reservation itself can raise
                            logger.warning("Failed to release coordinator reservation: %s", _rel_err)
                elif result.get("success") and _filled_size > 0:
                    if _is_sell:
                        self._track_position_close(bot_name, market_id)  # C3 FIX
                    # S133: BUY tracking via on_buy_fill callback (inside lock)
                return result
            except Exception as e:
                if self.trade_coordinator is not None:
                    try:
                        await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                    except Exception as _rel_err:  # H2 FIX: must not let release mask the original error
                        logger.warning("Failed to release coordinator reservation after paper order error: %s", _rel_err)
                logger.error("Paper order failed", bot_name=bot_name, market_id=market_id, error=str(e), exc_info=True)
                return {"success": False, "error": str(e)}

        try:
            _t_coord_end = time.monotonic()
            t0 = time.monotonic()
            # S121: Retry transient CLOB failures (rate limits, nonce, timeouts)
            result = await self._execute_with_retry(
                bot_name=bot_name,
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=size,
                price=effective_price,
                confidence=confidence,
                correlation_id=correlation_id,
            )
            latency_ms = (time.monotonic() - t0) * 1000

            # Latency logging: identical for paper and live (paper trading IS regular trading)
            if getattr(settings, "LOG_ORDER_LATENCY", True):
                _risk_ms = round((_t_risk_end - _t_risk_start) * 1000, 1)
                _coord_ms = round((_t_coord_end - _t_coord_start) * 1000, 1)
                _exec_ms = round(latency_ms, 1)
                _total_ms = round((_t_risk_end - _t_risk_start + _t_coord_end - _t_coord_start) * 1000 + latency_ms, 1)
                logger.info(
                    "Order latency",
                    bot_name=bot_name,
                    market_id=market_id,
                    latency_ms=round(latency_ms, 1),
                    success=result.get("success", False),
                    canary_stage=_canary,
                )
                logger.info(
                    "Order latency breakdown",
                    bot_name=bot_name,
                    market_id=market_id,
                    risk_ms=_risk_ms,
                    coord_ms=_coord_ms,
                    exec_ms=_exec_ms,
                    total_ms=_total_ms,
                    canary_stage=_canary,
                )
                alert_ms = getattr(settings, "ORDER_LATENCY_ALERT_MS", 5000)
                if latency_ms > alert_ms:
                    logger.warning(
                        "Order latency exceeded threshold",
                        bot_name=bot_name,
                        market_id=market_id,
                        latency_ms=round(latency_ms, 1),
                        threshold_ms=alert_ms,
                    )
            # Feed Prometheus (identical for paper and live)
            try:
                from base_engine.monitoring.metrics_collector import metrics_collector, ORDER_PIPELINE_LATENCY
                metrics_collector.record_trade(bot_name, str(side), result.get("success", False), latency_ms / 1000.0)
                ORDER_PIPELINE_LATENCY.labels(bot_name=bot_name, component="risk").observe(_risk_ms / 1000.0)
                ORDER_PIPELINE_LATENCY.labels(bot_name=bot_name, component="coord").observe(_coord_ms / 1000.0)
                ORDER_PIPELINE_LATENCY.labels(bot_name=bot_name, component="exec").observe(_exec_ms / 1000.0)
            except Exception:
                pass  # Metrics are best-effort

            if self.trade_coordinator is not None:
                if result.get("success"):
                    await self.trade_coordinator.confirm_position(market_id, side, size, effective_price, source_bot=bot_name, bot_id=bot_name, token_id=token_id)
                    if _is_sell:
                        self._track_position_close(bot_name, market_id)  # C3 FIX
                    else:
                        self._track_position_open(bot_name, market_id, size, effective_price, side=side)
                    if self.adverse_selection_tracker:
                        self.adverse_selection_tracker.record_fill(
                            market_id=market_id,
                            side=side,
                            fill_price=float(price),
                            fill_time=datetime.now(timezone.utc),
                            order_type=order_type or "market",
                            source_bot=bot_name,
                        )
                    # S120: Track pending order for fill confirmation via WebSocket
                    _order_id = result.get("order_id")
                    if _order_id and not _is_sell:
                        self._pending_orders[_order_id] = {
                            "market_id": market_id,
                            "token_id": token_id,
                            "side": side,
                            "size": size,
                            "price": effective_price,
                            "bot_name": bot_name,
                            "submitted_at": time.monotonic(),
                            "correlation_id": correlation_id,
                        }
                else:
                    try:
                        await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                    except Exception as _rel_err:  # H2 FIX: release can raise, don't propagate
                        logger.warning("Failed to release coordinator reservation: %s", _rel_err)
            elif result.get("success"):
                if _is_sell:
                    self._track_position_close(bot_name, market_id)  # C3 FIX
                else:
                    self._track_position_open(bot_name, market_id, size, effective_price)

            # S115: Record shadow fill for live executed BUY trades
            if result.get("success") and _is_buy and _shadow_book_walk_used:
                _db = self.db or (self.paper_trading_engine.db if self.paper_trading_engine else None)
                if _db and hasattr(_db, "insert_shadow_fill"):
                    _scan_start = (event_data or {}).get("scan_start_mono")
                    _latency = (time.monotonic() - _scan_start) * 1000 if _scan_start else None
                    _live_fill_price = float(result.get("price", effective_price))
                    try:
                        await _db.insert_shadow_fill(
                            bot_name=bot_name, market_id=market_id, token_id=token_id,
                            side="BUY", order_size_shares=size,
                            order_size_usd=size * _live_fill_price,
                            signal_price=effective_price, confidence=confidence,
                            edge_at_signal=(confidence - effective_price) if confidence else None,
                            latency_ms=_latency,
                            book_snapshot=_shadow_book_snapshot,
                            best_ask=_shadow_best_ask, best_bid=_shadow_best_bid,
                            spread=_shadow_spread,
                            depth_at_best_usd=_shadow_depth_best,
                            total_depth_usd=_shadow_total_depth,
                            vwap_fill_price=_shadow_vwap,
                            book_walk_slippage=_shadow_slippage,
                            fill_fraction=_shadow_fill_frac,
                            edge_at_vwap=(confidence - _shadow_vwap) if confidence else None,
                            trade_executed=True, execution_price=_live_fill_price,
                            correlation_id=correlation_id,
                            model_name=None, event_data=event_data,
                            vwap_at_intended=_intended_vwap,
                            slippage_at_intended=_intended_slippage,
                            fill_frac_at_intended=_intended_fill_frac,
                            intended_walk_error=_intended_walk_error,
                            intended_size_usd=_intended_size_usd_for_write,
                            intended_size_shares=_intended_size_shares_for_write,
                        )
                    except Exception as _sf_err:
                        logger.critical("shadow_fill_insert_failed", error=str(_sf_err), bot_name=bot_name, market_id=market_id)

            return result
        except Exception as e:
            if self.trade_coordinator is not None:
                try:
                    await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                except Exception as _rel_err:  # H2 FIX: release must not mask the original error
                    logger.warning("Failed to release coordinator reservation after execution error: %s", _rel_err)
            logger.error("Order execution failed", bot_name=bot_name, market_id=market_id, error=str(e), exc_info=True)
            return {"success": False, "error": str(e)}

    # ── S121: Live order retry with exponential backoff ────────────

    # Transient error patterns — safe to retry
    _TRANSIENT_PATTERNS = ("rate limit", "429", "503", "timeout", "nonce", "too many requests", "service unavailable")
    # Permanent error patterns — fail immediately
    _PERMANENT_PATTERNS = ("market closed", "delisted", "invalid", "insufficient", "not found", "expired", "cancelled")

    async def _execute_with_retry(
        self,
        bot_name: str,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        confidence: float,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """Execute a live CLOB order with retry for transient failures.

        Retries up to LIVE_ORDER_MAX_RETRIES times with exponential backoff
        (1s, 2s, 4s by default) for transient errors (rate limits, nonce,
        timeouts). Fails immediately for permanent errors (market closed,
        delisted, insufficient balance).
        """
        max_retries = getattr(settings, "LIVE_ORDER_MAX_RETRIES", 3)
        base_s = getattr(settings, "LIVE_ORDER_RETRY_BASE_S", 1.0)

        last_error = None
        for attempt in range(max_retries):
            try:
                result = await self.execution_engine.place_order(
                    bot_name=bot_name,
                    market_id=market_id,
                    token_id=token_id,
                    side=side,
                    size=size,
                    price=price,
                    confidence=confidence,
                    skip_position_update=True,
                    correlation_id=correlation_id,
                )
                # Check for soft failure (success=False in result, not exception)
                if result.get("success"):
                    return result

                err_str = str(result.get("error", "")).lower()
                if any(p in err_str for p in self._PERMANENT_PATTERNS):
                    logger.info("live_order_permanent_reject",
                                bot_name=bot_name, market_id=market_id,
                                error=result.get("error"), attempt=attempt + 1)
                    return result

                last_error = result.get("error", "unknown")
                if attempt < max_retries - 1:
                    delay = base_s * (2 ** attempt)
                    logger.warning("live_order_retry",
                                   bot_name=bot_name, market_id=market_id,
                                   attempt=attempt + 1, max_retries=max_retries,
                                   error=last_error, delay_s=delay)
                    await asyncio.sleep(delay)
                else:
                    return result

            except Exception as e:
                err_str = str(e).lower()
                if any(p in err_str for p in self._PERMANENT_PATTERNS):
                    logger.info("live_order_permanent_exception",
                                bot_name=bot_name, market_id=market_id,
                                error=str(e), attempt=attempt + 1)
                    raise

                last_error = str(e)
                if attempt < max_retries - 1:
                    delay = base_s * (2 ** attempt)
                    logger.warning("live_order_retry_exception",
                                   bot_name=bot_name, market_id=market_id,
                                   attempt=attempt + 1, max_retries=max_retries,
                                   error=last_error, delay_s=delay)
                    await asyncio.sleep(delay)
                else:
                    raise

        # Should not reach here, but safety net
        return {"success": False, "error": f"All {max_retries} retries exhausted: {last_error}"}

    # ── RL Trade Timing helpers (lightweight, no DB calls) ────────────

    def _get_spread_for_rl(self, market_id: str) -> float:
        """Estimate bid-ask spread from market index. Returns 0.05 (5%) as fallback."""
        if not self._market_index:
            return 0.05
        mdata = self._market_index.get(str(market_id))
        if not mdata:
            return 0.05
        # Try best_bid / best_ask from orderbook tracker or cached data
        try:
            best_bid = float(mdata.get("bestBid") or mdata.get("best_bid") or 0)
            best_ask = float(mdata.get("bestAsk") or mdata.get("best_ask") or 0)
            if best_bid > 0 and best_ask > 0:
                return best_ask - best_bid
        except (ValueError, TypeError):
            pass
        # Fallback: use tokens array price spread if available
        try:
            tokens = mdata.get("tokens") or []
            if len(tokens) >= 2:
                p0 = float(tokens[0].get("price") or 0)
                p1 = float(tokens[1].get("price") or 0)
                if p0 > 0 and p1 > 0:
                    # In a binary market, YES+NO ~ 1.0, spread ~ |1 - YES - NO|
                    return abs(1.0 - p0 - p1)
        except (ValueError, TypeError, IndexError):
            pass
        return 0.05

    def _get_volatility_for_rl(self, market_id: str) -> float:
        """Estimate recent price volatility from market index. Returns 0.02 as fallback."""
        if not self._market_index:
            return 0.02
        mdata = self._market_index.get(str(market_id))
        if not mdata:
            return 0.02
        # Try price_change or volatility fields if enriched by metadata enricher
        try:
            vol = float(mdata.get("volatility") or mdata.get("price_volatility") or 0)
            if vol > 0:
                return vol
        except (ValueError, TypeError):
            pass
        # Fallback: estimate from price change percentage
        try:
            pct_change = float(mdata.get("price_change_pct") or mdata.get("priceChangePct") or 0)
            if pct_change != 0:
                return abs(pct_change) / 100.0
        except (ValueError, TypeError):
            pass
        return 0.02

    def _get_regime_for_rl(self) -> str:
        """Get current market regime from prediction engine cache. Returns 'calm' as fallback."""
        # Check if paper_trading_engine has cached regime from prediction engine
        try:
            pe = getattr(self, "_prediction_engine_ref", None)
            if pe and hasattr(pe, "_last_regime"):
                regime = pe._last_regime
                if regime in ("calm", "volatile", "trending"):
                    return regime
        except Exception:
            pass
        return "calm"

    # ── L4: Adverse selection adaptive sizing ────────────────────────────

    # In-memory cache: market_id -> (adverse_mult, expiry_monotonic)
    _adverse_cache: Dict[str, tuple] = {}
    _ADVERSE_CACHE_TTL = 1800  # 30 minutes

    async def _get_adverse_sizing_mult(self, market_id: str) -> float:
        """
        L4: Compute position sizing multiplier based on adverse selection history.

        Markets with high adverse moves after fills get smaller positions.
        Formula: adverse_mult = max(0.5, 1.0 - avg_adverse * 5)
        Clamped to [0.5, 1.0]. Cached for 30 minutes.
        """
        now = time.monotonic()
        cached = self._adverse_cache.get(market_id)
        if cached and now < cached[1]:
            return cached[0]

        # Look up database reference from paper_trading_engine or execution_engine
        db = None
        if self.paper_trading_engine and hasattr(self.paper_trading_engine, "db"):
            db = self.paper_trading_engine.db
        elif hasattr(self, "_db_ref"):
            db = self._db_ref

        if db is None or not getattr(db, "session_factory", None):
            return 1.0

        try:
            stats = await db.get_adverse_move_stats(market_id)
            if stats is None or stats.get("n_fills", 0) < 3:
                # Cold-start: not enough data, use neutral multiplier
                mult = 1.0
            else:
                avg_adverse = abs(stats.get("avg_adverse_300s", 0.0))
                mult = max(0.5, 1.0 - avg_adverse * 5.0)
        except Exception:
            mult = 1.0

        self._adverse_cache[market_id] = (mult, now + self._ADVERSE_CACHE_TTL)
        return mult
