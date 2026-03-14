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
        self._market_index: Optional[Dict[str, Dict[str, Any]]] = None  # Set by base_engine after construction
        self._bot_names_used: Set[str] = set()  # For shutdown: release reservations for all bots in this process
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

    def get_daily_exposure_usd(self, bot_name: str) -> float:
        """O(1) in-memory daily exposure for a bot. Resets at day boundary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_exposure_date != today:
            self._daily_exposure_usd.clear()
            self._daily_exposure_date = today
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
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_exposure_date != today:
                self._daily_exposure_usd.clear()
                self._daily_exposure_date = today
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
                    select(Position.bot_id, Position.market_id, Position.size, Position.entry_price)
                    .where(Position.status == "open")
                    .where(Position.side != "SELL")  # SELL rows = exit attempts, not open capital
                )
                rows = result.all()

            new_open: Dict[str, Set[str]] = {}
            new_exposure: Dict[str, Dict[str, float]] = {}
            total_exp = 0.0

            for row in rows:
                bot = row[0] or "unknown"
                mid = str(row[1]) if row[1] else ""
                size = float(row[2] or 0)
                entry_price = float(row[3] or 0)
                if not mid:
                    continue
                new_open.setdefault(bot, set()).add(mid)
                value = size * entry_price
                if value > 0:
                    # Accumulate per (bot, market_id) in case of multiple YES/NO rows
                    prev = new_exposure.setdefault(bot, {}).get(mid, 0.0)
                    new_exposure[bot][mid] = prev + value
                    total_exp += value

            old_exp = self._total_exposure_usd
            self._open_position_markets = new_open
            self._position_exposure = new_exposure
            self._position_details = {}  # reset supplementary details; repopulated by new trades
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
                    return {"success": False, "error": "Kill switch engaged (multi-layer)"}
            except Exception as e:
                logger.warning("Multi kill switch check failed, falling back to basic: %s", e)
                # Fall through to basic kill switch check below

        if self.kill_switch is not None:
            if await self.kill_switch.is_engaged():
                logger.warning("Order blocked: kill switch engaged", bot_name=bot_name, market_id=market_id)
                return {"success": False, "error": "Kill switch engaged"}

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

        # Drawdown controller: graduated position reduction during losing streaks
        if self.drawdown_controller is not None and not _is_sell:
            try:
                paper_engine = self.paper_trading_engine
                portfolio = {}
                if paper_engine and paper_engine.enabled:
                    portfolio = {
                        "starting_capital": getattr(settings, "TOTAL_CAPITAL", 10000.0),
                        "realized_pnl_today": getattr(paper_engine, "realized_pnl_today", 0.0),
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
        if not _is_sell and getattr(settings, "L4_ADVERSE_SIZING_ENABLED", True):
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
        if self.risk_manager is not None and not _is_sell:
            try:
                risk_check = await self.risk_manager.check_risk_limits(
                    bot_name, market_id, size, price, confidence, prediction=prediction
                )
                if not risk_check.get("allowed", True):
                    reasons = risk_check.get("reasons", [])
                    # Auto-clamp: if the ONLY issue is position size or exposure, reduce size to fit
                    size_reasons = [r for r in reasons if "Position $" in r and "exceeds max" in r]
                    exposure_reasons = [r for r in reasons if "Total exposure" in r and "exceeds max" in r]
                    non_size_reasons = [r for r in reasons if r not in size_reasons and r not in exposure_reasons]
                    if not non_size_reasons and (size_reasons or exposure_reasons) and price > 0:
                        # Clamp to the tightest limit (0.99x to avoid floating-point edge hitting exact limit)
                        max_pos_usd = getattr(settings, "RISK_MAX_POSITION_SIZE_USD", 100.0)
                        max_total_usd = getattr(settings, "RISK_MAX_TOTAL_EXPOSURE_USD", 500.0)
                        clamped_value = min(max_pos_usd, max_total_usd) * 0.99
                        clamped_size = clamped_value / price
                        if clamped_size >= 1.0:  # Only trade if at least 1 unit
                            logger.info(
                                "Order size clamped to risk limit",
                                bot_name=bot_name,
                                market_id=market_id,
                                original_size=size,
                                clamped_size=round(clamped_size, 2),
                                max_value_usd=clamped_value,
                            )
                            size = clamped_size
                            # Re-check with clamped size
                            risk_check2 = await self.risk_manager.check_risk_limits(
                                bot_name, market_id, size, price, confidence, prediction=prediction
                            )
                            if not risk_check2.get("allowed", True):
                                reasons2 = risk_check2.get("reasons", [])
                                msg = "; ".join(reasons2) if reasons2 else "Risk limits exceeded (after clamp)"
                                logger.warning("Order blocked after clamp: risk limits", bot_name=bot_name, market_id=market_id, reasons=reasons2)
                                return {"success": False, "error": msg, "reasons": reasons2}
                        else:
                            msg = "; ".join(reasons) if reasons else "Risk limits exceeded"
                            logger.warning("Order blocked: risk limits (clamped size too small)", bot_name=bot_name, market_id=market_id, reasons=reasons)
                            return {"success": False, "error": msg, "reasons": reasons}
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
                return await self.cascade_detector.detect(market_id, window_hours=6)

            async def _liquidity_check():
                if not _liquidity_enabled:
                    return None
                # Session 82: Skip liquidity API call for RTDS fast-copy trades (saves 100-300ms).
                # Gated by MIRROR_SKIP_LIQUIDITY_RTDS=true AND correlation_id prefix "rtds:".
                if (correlation_id and str(correlation_id).startswith("rtds:")
                        and getattr(settings, "MIRROR_SKIP_LIQUIDITY_RTDS", False)):
                    return None
                # Look up condition_id from market index for CLOB API order book query
                _cid = ""
                if self._market_index:
                    _mdata = self._market_index.get(str(market_id))
                    if _mdata:
                        _cid = str(_mdata.get("conditionId") or _mdata.get("condition_id") or "")
                return await self.liquidity_guardian.check_liquidity(
                    market_id=market_id, token_id=token_id, trade_size=size, side=side,
                    condition_id=_cid,
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
        _t_coord_start = time.monotonic()
        _coord_timeout = 15.0 if _is_sell else 5.0
        if self.trade_coordinator is not None:
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

        # Paper trading: full pipeline (risk, coordinator) then record order instead of CLOB
        if getattr(settings, "SIMULATION_MODE", False) and self.paper_trading_engine and self.paper_trading_engine.enabled:
            # In binary prediction markets, YES/NO indicate which token to buy, not trade direction.
            # Both YES and NO are BUY orders (buying that side's token).
            # SELL only happens when closing an existing position.
            paper_side = "SELL" if str(side).upper() == "SELL" else "BUY"
            logger.info("Paper trade attempt", market_id=market_id, side=paper_side, size=round(size, 2), price=round(effective_price, 6), bot_name=bot_name, cash=round(self.paper_trading_engine.cash, 2))
            try:
                _t_coord_end = time.monotonic()
                t0 = time.monotonic()
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
                    latency_ms=None,  # placeholder — set after timing
                    bid=bid,
                    ask=ask,
                    event_data=event_data,
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
                if self.trade_coordinator is not None:
                    if result.get("success"):
                        try:
                            await self.trade_coordinator.confirm_position(market_id, side, size, effective_price, source_bot=bot_name, bot_id=bot_name)
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
                        else:
                            self._track_position_open(bot_name, market_id, size, effective_price, side=side)
                    else:
                        try:
                            await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                        except Exception as _rel_err:  # H2 FIX: release_reservation itself can raise
                            logger.warning("Failed to release coordinator reservation: %s", _rel_err)
                elif result.get("success"):
                    if _is_sell:
                        self._track_position_close(bot_name, market_id)  # C3 FIX
                    else:
                        self._track_position_open(bot_name, market_id, size, effective_price, side=side)
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
            result = await self.execution_engine.place_order(
                bot_name=bot_name,
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=size,
                price=effective_price,
                confidence=confidence,
                skip_position_update=True,
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

            if self.trade_coordinator is not None:
                if result.get("success"):
                    await self.trade_coordinator.confirm_position(market_id, side, size, effective_price, source_bot=bot_name, bot_id=bot_name)
                    if _is_sell:
                        self._track_position_close(bot_name, market_id)  # C3 FIX
                    else:
                        self._track_position_open(bot_name, market_id, size, effective_price)
                    if self.adverse_selection_tracker:
                        self.adverse_selection_tracker.record_fill(
                            market_id=market_id,
                            side=side,
                            fill_price=float(price),
                            fill_time=datetime.now(timezone.utc),
                            order_type=order_type or "market",
                            source_bot=bot_name,
                        )
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
            return result
        except Exception as e:
            if self.trade_coordinator is not None:
                try:
                    await self.trade_coordinator.release_reservation(market_id, side, bot_id=bot_name)
                except Exception as _rel_err:  # H2 FIX: release must not mask the original error
                    logger.warning("Failed to release coordinator reservation after execution error: %s", _rel_err)
            logger.error("Order execution failed", bot_name=bot_name, market_id=market_id, error=str(e), exc_info=True)
            return {"success": False, "error": str(e)}

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
