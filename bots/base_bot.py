import asyncio
import json
import math
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from structlog import get_logger
from base_engine.base_engine import BaseEngine
from config.settings import settings

logger = get_logger()


class _LatencyTracker:
    """Lightweight per-scan-cycle stage timer for latency breakdown logging."""
    __slots__ = ("_marks",)

    def __init__(self):
        self._marks: list = []

    def mark(self, stage: str) -> None:
        self._marks.append((stage, time.monotonic()))

    def report(self) -> Dict[str, float]:
        if len(self._marks) < 2:
            return {}
        result = {}
        for i in range(1, len(self._marks)):
            key = f"{self._marks[i - 1][0]}>{self._marks[i][0]}"
            result[key] = round((self._marks[i][1] - self._marks[i - 1][1]) * 1000, 1)
        return result


# Map bot_name to settings key for SCAN_INTERVAL_*
_SCAN_INTERVAL_KEYS = {
    # Active bots (2026 roster)
    "ArbitrageBot": "ARBITRAGE",
    "MirrorBot": "MIRROR",
    "CrossPlatformArbBot": "CROSS_PLATFORM_ARB",
    "OracleBot": "ORACLE",
    "SportsBot": "SPORTS",
    "LLMForecasterBot": "LLM_FORECASTER",
    "WeatherBot": "WEATHER",
    # Sports betting bots — Migration 022
    "SportsInjuryBot": "SPORTS_INJURY",
    "SportsLiveBot":   "SPORTS_LIVE",
    "SportsArbBot":    "SPORTS_ARB",
    # Esports bots — Migration 024
    "EsportsBot":       "ESPORTS",
    "EsportsBotV2":     "ESPORTS_V2",
    "EsportsLiveBot":   "ESPORTS_LIVE",
    # Logical arbitrage bot
    "LogicalArbBot":    "LOGICAL_ARB",
}


def get_end_date_from_dict(d: dict) -> Optional[str]:
    """Extract end date from market dict, handling all 5 API naming variants."""
    return (d.get("end_date_iso") or d.get("endDateISO") or d.get("endDateIso")
            or d.get("endDate") or d.get("end_date"))


class BaseBot(ABC):
    def __init__(self, bot_name: str, base_engine: BaseEngine):
        self.bot_name = bot_name
        self.base_engine = base_engine
        self.running = False
        self.scan_task: Optional[asyncio.Task] = None
        self.trades_executed: int = 0
        # Session 47: Per-bot bankroll manager (independent Kelly sizing + capital allocation).
        # Replaces the shared risk_manager.calculate_position_size() with KELLY_ACTIVE_BOTS divisor.
        try:
            from base_engine.risk.bankroll_manager import BotBankrollManager
            self.bankroll = BotBankrollManager(
                bot_name=bot_name,
                order_gateway=getattr(base_engine, "order_gateway", None),
                db=getattr(base_engine, "db", None),
            )
        except Exception as e:
            logger.warning("BotBankrollManager init failed (using legacy Kelly): %s", e)
            self.bankroll = None
        # Phase 15: reuse single StrategicTimer when USE_SCAN_JITTER=True (lazy init in _get_scan_interval_seconds)
        self._strategic_timer: Optional[Any] = None
        # Latency tracker — reset at start of each scan cycle (Item 23)
        self._latency_tracker: Optional[_LatencyTracker] = None
        # Correlation ID for current scan cycle (Item 13)
        self._current_correlation_id: Optional[str] = None
        # R2: Stores signal metadata captured during apply_signal_enhancements().
        # Key = market_id string. Written to trade_signals table after place_order succeeds.
        self._pending_signal_meta: Dict[str, Dict] = {}
        # Session 51: Heartbeat counters — subclasses populate during scan_and_trade()
        self._last_scan_markets: int = 0
        self._last_scan_opportunities: int = 0
        self._last_scan_trades: int = 0
        # Phase 5.2: Whale priority queue — markets pushed here by Redis whale_alerts listener.
        # Drained at the START of each scan cycle so whale markets get immediate attention.
        self._whale_priority_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._whale_listener_task: Optional[asyncio.Task] = None
        # Phase 2: set when bot is idle (not in scan_and_trade), cleared while scanning.
        # Used by wait_for_idle() to allow graceful SIGTERM without mid-scan cancellation.
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # starts idle

    async def start(self):
        self.running = True
        logger.info("Bot started", bot_name=self.bot_name)
        # S217: Wallet-derived bankroll init. No-op for bots where
        # BOT_WALLET_BANKROLL_ENABLED is off (which is all of them by default
        # except per-bot opt-in like MirrorBot). Raises and aborts start() on
        # cold-start wallet-read failure for opted-in bots — that's intentional;
        # an opted-in bot must not run against fake config capital.
        if self.bankroll is not None:
            try:
                await self.bankroll.init_wallet_bankroll()
            except Exception as _bk_err:
                logger.critical(
                    "wallet_bankroll_init_failed",
                    bot_name=self.bot_name,
                    error=str(_bk_err),
                )
                self.running = False
                raise
        # BUG FIX: Add error handling for scan task to prevent silent failures
        self.scan_task = asyncio.create_task(self._scan_loop())
        self.scan_task.add_done_callback(self._task_error_handler)
        # Phase 5.2: Start whale alert Redis listener (no-op if Redis not connected)
        self._whale_listener_task = asyncio.create_task(self._whale_alert_listener())
        self._whale_listener_task.add_done_callback(self._task_error_handler)
    
    async def stop(self):
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()
            try:
                await self.scan_task
            except asyncio.CancelledError:
                pass
        if self._whale_listener_task:
            self._whale_listener_task.cancel()
            try:
                await self._whale_listener_task
            except asyncio.CancelledError:
                pass
        logger.info("Bot stopped", bot_name=self.bot_name)

    async def wait_for_idle(self, timeout: float = 25.0) -> None:
        """Wait until current scan_and_trade() completes. Called before stop() during graceful shutdown."""
        try:
            await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "wait_for_idle: timed out after %ss — forcing stop",
                timeout,
                bot_name=self.bot_name,
            )

    async def flush_state(self) -> None:
        """Hook for subclasses to flush in-memory state on graceful shutdown. No-op by default.
        Subclasses with write-through persistence (daily_counters, Redis) don't need this —
        base_engine.stop() already calls order_gateway._flush_daily_exposure()."""
        pass

    async def _rebuild_positions_from_events(self) -> Dict[str, Dict]:
        """
        Fallback: rebuild position state from trade_events if positions table is empty.
        Returns dict of {market_id:side: {market_id, side, net_quantity, avg_price}}.
        Subclasses call this from their _restore_state_on_startup() if needed.
        """
        db = getattr(self.base_engine, "db", None)
        if not db or not hasattr(db, "rebuild_positions_from_events"):
            return {}
        try:
            return await db.rebuild_positions_from_events(self.bot_name)
        except Exception as e:
            logger.warning("rebuild_positions_from_events failed for %s: %s", self.bot_name, e)
            return {}

    def get_feature_importance(self) -> Dict[str, float]:
        """Get current global feature importance scores.
        Returns dict of {feature_name: importance_score}. Bots can use this
        to understand which inputs are signal vs. noise."""
        return getattr(self.base_engine, "get_feature_importance", lambda: {})()

    async def store_pending_trade_signals(self, trade_id: str, market_id: str) -> None:
        """
        R2: Write signal context captured during apply_signal_enhancements() to trade_signals DB.
        Call this immediately after a paper trade is confirmed (place_order returns success).
        Runs fire-and-forget; failures are logged at DEBUG and never raise.

        Args:
            trade_id: The paper_trades.id of the just-placed trade.
            market_id: The market ID (used to look up pending signal meta).
        """
        meta = self._pending_signal_meta.pop(str(market_id), None)
        if not meta:
            return
        try:
            db = getattr(self.base_engine, "db", None)
            if db is None:
                return
            from base_engine.data.database import TradeSignal, _naive_utc
            from datetime import datetime, timezone
            row = TradeSignal(
                trade_id=str(trade_id),
                market_id=str(market_id),
                bot_name=self.bot_name,
                signal_direction=meta.get("signal_direction"),
                signal_confidence=meta.get("signal_confidence"),
                signal_source=meta.get("signal_source"),
                signal_multiplier=meta.get("signal_multiplier"),
                order_flow_direction=meta.get("order_flow_direction"),
                order_flow_multiplier=meta.get("order_flow_multiplier"),
                trends_signal=meta.get("trends_signal"),
                trends_multiplier=meta.get("trends_multiplier"),
            )
            async with db.get_session() as session:
                session.add(row)
                await session.commit()
            logger.debug(
                "R2 trade_signals stored: trade=%s market=%s source=%s dir=%s",
                trade_id, market_id, meta.get("signal_source"), meta.get("signal_direction"),
            )
        except Exception as exc:
            logger.debug("store_pending_trade_signals failed (non-critical): %s", exc)

    async def get_sentiment(
        self,
        market_id: str,
        price_data: Dict[str, Any],
        volume_data: Dict[str, Any],
        orderbook_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get centralized sentiment analysis for a market.
        Returns sentiment dict or None if analyzer unavailable."""
        analyzer = getattr(self.base_engine, "sentiment_analyzer", None)
        if not analyzer:
            return None
        try:
            return await analyzer.analyze_market_sentiment(
                market_id=market_id,
                price_data=price_data,
                volume_data=volume_data,
                orderbook_data=orderbook_data,
            )
        except Exception:
            return None

    def _get_scan_interval(self) -> float:
        """Per-bot scan interval from config (seconds). Use with _get_scan_interval_seconds() for jitter."""
        key = _SCAN_INTERVAL_KEYS.get(self.bot_name, "")
        if key:
            return float(getattr(
                settings,
                f"SCAN_INTERVAL_{key}",
                getattr(settings, "DEFAULT_SCAN_INTERVAL", 60),
            ))
        return float(getattr(settings, "BOT_SCAN_INTERVAL_SECONDS", getattr(settings, "DEFAULT_SCAN_INTERVAL", 60)))

    def _get_scan_interval_seconds(self) -> float:
        """Seconds to sleep until next scan. Phase 15: reuse single StrategicTimer when USE_SCAN_JITTER=true."""
        base = self._get_scan_interval()
        if getattr(settings, "USE_SCAN_JITTER", False):
            try:
                if self._strategic_timer is None:
                    from base_engine.analysis.game_theory import StrategicTimer
                    jitter_pct = getattr(settings, "SCAN_JITTER_PCT", 0.2)
                    self._strategic_timer = StrategicTimer(
                        base_interval_seconds=base,
                        jitter_pct=jitter_pct,
                        burst_probability=0.02,
                    )
                return self._strategic_timer.next_interval()
            except Exception as e:
                logger.debug("strategic timer jitter calculation failed: %s", e)
        return base

    async def on_price_update(self, event: Dict[str, Any]) -> None:
        """
        Handle real-time price updates from WebSocket via EventBus.

        Default implementation: caches latest price per market so the next
        scan_and_trade() cycle can use it instead of polling the DB.
        Override in subclasses for immediate reaction (e.g. SportsBot).

        Event payload: {"market_id", "token_id", "price", "timestamp", "_ws_recv_t"}
        """
        market_id = event.get("market_id")
        price = event.get("price")
        if not market_id or price is None:
            return
        # Signal latency: WS receipt → bot handler (identical for paper and live)
        _ws_recv_t = event.get("_ws_recv_t", 0.0)
        if _ws_recv_t > 0:
            _signal_ms = (time.monotonic() - _ws_recv_t) * 1000
            if _signal_ms > getattr(settings, "WS_SIGNAL_LATENCY_ALERT_MS", 50):
                logger.warning("WS signal latency", bot_name=self.bot_name,
                               market_id=market_id, signal_ms=round(_signal_ms, 1))
            try:
                from base_engine.monitoring.metrics_collector import WS_SIGNAL_LATENCY
                WS_SIGNAL_LATENCY.labels(bot_name=self.bot_name).observe(_signal_ms / 1000.0)
            except Exception:
                pass  # Metrics are best-effort
        # Store latest WS price in a per-bot cache (avoids DB poll on next scan)
        if not hasattr(self, "_ws_price_cache"):
            self._ws_price_cache: Dict[str, float] = {}
        self._ws_price_cache[market_id] = float(price)

    def get_ws_price(self, market_id: str) -> Optional[float]:
        """Return the latest WebSocket price for a market, or None if unavailable."""
        cache = getattr(self, "_ws_price_cache", None)
        if cache:
            return cache.get(market_id)
        return None

    def mark_latency(self, stage: str) -> None:
        """Mark a latency stage in the current scan cycle tracker.
        Safe to call even when tracker is None (outside scan context)."""
        _tracker = getattr(self, "_latency_tracker", None)
        if _tracker is not None:
            _tracker.mark(stage)

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        confidence: float,
        prediction: Optional[float] = None,
        event_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Place order via base_engine. Returns early if bot was stopped (graceful shutdown)."""
        if not self.running:
            logger.debug("Skipping order, bot stopped", bot_name=self.bot_name)
            return {"success": False, "error": "Bot stopped"}
        self.mark_latency("order_start")
        _order_t0 = time.monotonic()
        # P0.3: Plumb intended_size_usd from last calculate_bot_position_size() call.
        _intended_usd = getattr(self, "_last_intended_size_usd", None)
        self._last_intended_size_usd = None  # clear after reading
        if _intended_usd:
            if event_data is None:
                event_data = {}
            event_data = dict(event_data)
            event_data["intended_size_usd"] = _intended_usd
        result = await self.base_engine.place_order(
            bot_name=self.bot_name,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            confidence=confidence,
            prediction=prediction,
            correlation_id=getattr(self, "_current_correlation_id", None),
            event_data=event_data,
        )
        _order_ms = (time.monotonic() - _order_t0) * 1000
        self.mark_latency("order_done")
        if result.get("success"):
            self.trades_executed += 1
            logger.info("Order placed", bot_name=self.bot_name, market_id=market_id, side=side, latency_ms=round(_order_ms, 1))
            # S145: Auto-store pending signal metadata — every bot benefits automatically
            _trade_id = result.get("trade_id") or result.get("order_id")
            if _trade_id and self._pending_signal_meta.get(str(market_id)):
                try:
                    await asyncio.wait_for(
                        self.store_pending_trade_signals(str(_trade_id), str(market_id)),
                        timeout=2.0,
                    )
                except Exception:
                    logger.debug("signal_store_failed", bot_name=self.bot_name, market_id=market_id)
        return result

    # ── Shared utilities: extracted from duplicate code across bots ──

    @staticmethod
    def validate_price(price_raw, market_id: str = "") -> Optional[float]:
        """Validate a Polymarket price (0 < p <= 1, not NaN/Inf). Returns float or None."""
        try:
            price = float(price_raw)
            if price <= 0 or price > 1:
                return None
            if math.isnan(price) or math.isinf(price):
                return None
            return price
        except (ValueError, TypeError):
            return None

    @staticmethod
    def hours_until_resolution(market_data: Dict) -> Optional[float]:
        """Parse end date from market data and return hours until resolution, or None."""
        try:
            end_raw = get_end_date_from_dict(market_data)
            if not end_raw:
                return None
            if isinstance(end_raw, (int, float)):
                end_dt = datetime.fromtimestamp(end_raw, tz=timezone.utc)
            elif isinstance(end_raw, str):
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            elif isinstance(end_raw, datetime):
                end_dt = end_raw if end_raw.tzinfo else end_raw.replace(tzinfo=timezone.utc)
            else:
                return None
            return (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        except Exception as e:
            logger.debug("hours_until_resolution parsing failed: %s", e)
            return None

    def should_skip_near_resolution(self, market_data: Dict, threshold_hours: float = 6.0) -> bool:
        """Return True if market is within threshold_hours of resolution (convergence zone)."""
        h = self.hours_until_resolution(market_data)
        if h is not None and h < threshold_hours:
            logger.debug("Market too close to resolution (%.1f h < %.0f h), skipping", h, threshold_hours)
            return True
        return False

    async def apply_signal_enhancements(
        self,
        market_id: str,
        token_id: str,
        direction: str,
        confidence: float,
        market_data: Optional[Dict] = None,
    ) -> float:
        """
        Apply signal, order flow, and Google Trends confidence adjustments.
        Phase 9: run the three fetches in parallel with asyncio.gather.
        """
        async def _signals_mult() -> tuple:
            """Returns (multiplier, best_signal_dict_or_None)."""
            if not getattr(settings, "USE_SIGNALS_IN_BOTS", True) or not getattr(self.base_engine, "signal_ingestion", None):
                return 1.0, None
            try:
                signals = await self.base_engine.signal_ingestion.get_signals_for_market(market_id, limit=5)
                if not signals:
                    return 1.0, None
                best = max(signals, key=lambda s: float(s.get("priority_score", 0) or 0))
                sig_dir = (best.get("direction") or "").upper()
                if not sig_dir:
                    return 1.0, best
                direction_matches = sig_dir == direction

                _effectiveness = getattr(self.base_engine, "signal_effectiveness", None)
                if _effectiveness is not None:
                    _source = (best.get("source") or "unknown").lower()
                    _category = (
                        (market_data or {}).get("category") or
                        (market_data or {}).get("category_slug") or
                        ""
                    )
                    m = await _effectiveness.get_multiplier(
                        source=_source,
                        category=str(_category).lower(),
                        direction_matches=direction_matches,
                    )
                    return m, best
                if direction_matches:
                    return 1.2, best
                return 0.6, best
            except Exception as e:
                logger.debug("Signal fetch failed for %s: %s", market_id, e)
                return 1.0, None

        async def _flow_mult() -> tuple:
            """Returns (multiplier, flow_dict_or_None)."""
            if not getattr(settings, "USE_ORDER_FLOW_IN_BOTS", True) or not getattr(self.base_engine, "trade_flow_analyzer", None):
                return 1.0, None
            try:
                flow = await self.base_engine.trade_flow_analyzer.get_flow_signal(token_id)
                if not flow:
                    return 1.0, None
                flow_dir = flow.get("direction", "")
                if (flow_dir == "bullish" and direction == "YES") or (flow_dir == "bearish" and direction == "NO"):
                    return 1.1, flow
                if (flow_dir == "bearish" and direction == "YES") or (flow_dir == "bullish" and direction == "NO"):
                    return 0.85, flow
                return 1.0, flow
            except Exception as e:
                logger.debug("Order flow fetch failed for %s: %s", market_id, e)
                return 1.0, None

        async def _trends_mult() -> tuple:
            """Returns (multiplier, trend_dict_or_None)."""
            if not getattr(settings, "USE_GOOGLE_TRENDS", True) or not getattr(self.base_engine, "google_trends", None):
                return 1.0, None
            try:
                question = (market_data or {}).get("question") or (market_data or {}).get("title") or ""
                if not question:
                    return 1.0, None
                trend = await self.base_engine.google_trends.get_market_signal(question)
                t_sig = trend.get("signal", "neutral")
                if (t_sig == "bullish" and direction == "YES") or (t_sig == "bearish" and direction == "NO"):
                    return 1.05, trend
                return 1.0, trend
            except Exception as e:
                logger.debug("Google Trends fetch failed for %s: %s", market_id, e)
                return 1.0, None

        # H1 FIX: Wrap each external service call with a 5s timeout before gather.
        # Without this, a single hung service (signal_ingestion, trade_flow, google_trends)
        # blocks the entire opportunity analysis and order placement path indefinitely.
        _svc_timeout = getattr(settings, "SIGNAL_SERVICE_TIMEOUT_SECONDS", 5)

        # R2: Use extended versions that return (multiplier, metadata) tuples for storage.
        # We wrap the existing helpers to also capture raw signal context.
        _sig_meta: dict = {}
        _flow_meta: dict = {}
        _trends_meta: dict = {}

        async def _signals_mult_tracked() -> float:
            mult, best_sig = await _signals_mult()
            if best_sig:
                _sig_meta["direction"] = (best_sig.get("direction") or "").upper() or None
                _sig_meta["source"] = (best_sig.get("source") or "unknown").lower()
                _sig_meta["confidence"] = float(best_sig.get("priority_score") or 0.5)
                _sig_meta["multiplier"] = mult
            return mult

        async def _flow_mult_tracked() -> float:
            mult, flow_data = await _flow_mult()
            if flow_data:
                _flow_meta["direction"] = flow_data.get("direction", "neutral")
                _flow_meta["multiplier"] = mult
            return mult

        async def _trends_mult_tracked() -> float:
            mult, trend_data = await _trends_mult()
            if trend_data:
                _trends_meta["signal"] = trend_data.get("signal", "neutral")
                _trends_meta["multiplier"] = mult
            return mult

        mults = await asyncio.gather(
            asyncio.wait_for(_signals_mult_tracked(), timeout=_svc_timeout),
            asyncio.wait_for(_flow_mult_tracked(), timeout=_svc_timeout),
            asyncio.wait_for(_trends_mult_tracked(), timeout=_svc_timeout),
            return_exceptions=True,
        )
        # Phase 0: Use MultiplierAggregator instead of unbounded multiplicative stacking.
        # Old code: `for m in mults: confidence *= m` — could crush to near-zero.
        # New code: collect all named multipliers, apply composite clamp [0.3, 2.0].
        from base_engine.learning.multiplier_aggregator import MultiplierAggregator
        _mult_names = ["signal", "flow", "trends"]
        agg = MultiplierAggregator()
        for i, m in enumerate(mults):
            if isinstance(m, BaseException):
                logger.debug("Signal enhancement error (skipping): %s", m)
                continue
            agg.add(_mult_names[i] if i < len(_mult_names) else f"enhancement_{i}", m)
        composite = agg.compute()
        confidence *= composite
        confidence = min(1.0, max(0.0, confidence))

        # R2: Store signal metadata for this market so it can be written to trade_signals
        # after a trade is placed. Bots call store_pending_trade_signals(trade_id, market_id).
        self._pending_signal_meta[str(market_id)] = {
            "signal_direction": _sig_meta.get("direction"),
            "signal_confidence": _sig_meta.get("confidence"),
            "signal_source": _sig_meta.get("source"),
            "signal_multiplier": _sig_meta.get("multiplier"),
            "order_flow_direction": _flow_meta.get("direction"),
            "order_flow_multiplier": _flow_meta.get("multiplier"),
            "trends_signal": _trends_meta.get("signal"),
            "trends_multiplier": _trends_meta.get("multiplier"),
        }

        return confidence

    async def calculate_bot_position_size(
        self, confidence: float, price: float,
        calibration_quality: Optional[Dict] = None,
        market_vol: float = 0.0,
        category: str = "",
        conformal_interval: Optional[tuple] = None,
    ) -> float:
        """Position sizing via per-bot BotBankrollManager (Session 47).
        Falls back to legacy risk_manager.calculate_position_size() if bankroll not available.
        Pass calibration_quality from prediction result to enable calibration-aware sizing.
        Pass category for category-specific Kelly fractions.
        Pass conformal_interval (p_low, p_high) for conservative Kelly sizing (Session 82)."""
        try:
            self._last_intended_size_usd: Optional[float] = None  # P0.3: reset each call
            # Session 47: Use per-bot bankroll manager when available
            if self.bankroll is not None:
                size_usd, _intended_usd = await self.bankroll.get_bet_size(
                    confidence=confidence,
                    price=price,
                    calibration_quality=calibration_quality,
                    category=category,
                    conformal_interval=conformal_interval,
                )
                self._last_intended_size_usd = _intended_usd  # P0.3: stash for place_order
                # Convert USD to shares for compatibility with downstream code
                if price > 0 and size_usd > 0:
                    return size_usd / price
                return 0.0

            # Legacy path: shared risk_manager Kelly with KELLY_ACTIVE_BOTS divisor
            capital = await self._get_allocated_capital()
            return await self.base_engine.risk_manager.calculate_position_size(
                bot_name=self.bot_name,
                confidence=confidence,
                available_capital=float(capital),
                price=price,
                calibration_quality=calibration_quality,
                market_vol=market_vol,
            )
        except Exception as e:
            # T10 FIX: Fallback was $100 * confidence, bypassing all risk limits.
            # Now returns 0.0 (refuse to trade) — risk manager failure = no trade.
            logger.error("Risk manager failed — refusing to size trade (T10 safety)", bot_name=self.bot_name, error=str(e))
            return 0.0

    async def _get_allocated_capital(self) -> float:
        """Per-bot capital allocation based on historical PnL. Phase 12: 60s TTL cache."""
        import time
        total = float(getattr(settings, "TOTAL_CAPITAL", 10000.0))
        if not getattr(settings, "USE_CAPITAL_ALLOCATOR", False):
            return total
        cache = getattr(self, "_capital_cache", None)
        if cache is None:
            self._capital_cache = {}
        key = self.bot_name
        now = time.monotonic()
        if key in self._capital_cache:
            val, ex = self._capital_cache[key]
            if now < ex:
                return val
            del self._capital_cache[key]
        try:
            metrics = await self.get_metrics()
            pnl = metrics.get("total_pnl", 0.0)
            if total <= 0:
                return total
            pnl_pct = pnl / total
            multiplier = 1.0 + max(-0.5, min(0.5, pnl_pct * 5))
            allocated = total * multiplier
            self._capital_cache[key] = (allocated, now + 60.0)
            logger.debug(
                "%s capital allocation: pnl=%.2f mult=%.2f allocated=%.2f",
                self.bot_name, pnl, multiplier, allocated,
            )
            return allocated
        except Exception as e:
            logger.debug("Capital allocator fallback: %s", e)
            return total

    async def get_metrics(self) -> Dict[str, Any]:
        """Per-bot metrics: trades_executed, trades_won, total_pnl from positions."""
        out: Dict[str, Any] = {
            "trades_executed": self.trades_executed,
            "trades_won": 0,
            "total_pnl": 0.0,
        }
        db = getattr(self.base_engine, "db", None)
        if db and hasattr(db, "get_bot_metrics"):
            try:
                bot_metrics = await db.get_bot_metrics(self.bot_name)
                out["trades_won"] = bot_metrics.get("trades_won", 0)
                out["total_pnl"] = bot_metrics.get("total_pnl", 0.0)
            except Exception as e:
                logger.debug("get_bot_metrics db fetch failed: %s", e)
        return out
    
    async def _record_heartbeat(self, scan_duration_ms: float) -> None:
        """Upsert bot_heartbeats row after a successful scan cycle (Session 51)."""
        db = getattr(self.base_engine, "db", None)
        if not db or not db.session_factory:
            return
        try:
            from sqlalchemy import text
            async with db.get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO bot_heartbeats
                            (bot_name, last_scan_at, scan_duration_ms, markets_scanned,
                             opportunities_found, trades_executed, consecutive_errors, updated_at)
                        VALUES (:bn, NOW(), :ms, :mk, :op, :tr, 0, NOW())
                        ON CONFLICT (bot_name) DO UPDATE SET
                            last_scan_at = NOW(),
                            scan_duration_ms = :ms,
                            markets_scanned = :mk,
                            opportunities_found = :op,
                            trades_executed = :tr,
                            consecutive_errors = 0,
                            updated_at = NOW()
                    """),
                    {"bn": self.bot_name, "ms": scan_duration_ms,
                     "mk": self._last_scan_markets, "op": self._last_scan_opportunities,
                     "tr": self._last_scan_trades},
                )
                await session.commit()
        except Exception as e:
            logger.debug("heartbeat upsert failed (non-fatal): %s", e)

    async def _record_heartbeat_error(self, consecutive_errors: int) -> None:
        """Update bot_heartbeats with error count (Session 51)."""
        db = getattr(self.base_engine, "db", None)
        if not db or not db.session_factory:
            return
        try:
            from sqlalchemy import text
            async with db.get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO bot_heartbeats (bot_name, last_scan_at, consecutive_errors, updated_at)
                        VALUES (:bn, NOW(), :errs, NOW())
                        ON CONFLICT (bot_name) DO UPDATE SET
                            consecutive_errors = :errs,
                            updated_at = NOW()
                    """),
                    {"bn": self.bot_name, "errs": consecutive_errors},
                )
                await session.commit()
        except Exception:
            pass

    async def _scan_loop(self):
        # BUG FIX: Add failure counting and exponential backoff
        # Root cause: Error handling is too simple - doesn't distinguish between transient
        # and permanent failures, keeps retrying on permanent failures
        # Impact: Bot wastes resources, log spam from repeated errors
        # Fix: Add failure counting and exponential backoff, or circuit breaker pattern
        consecutive_failures = 0
        max_consecutive_failures = getattr(settings, "BOT_MAX_CONSECUTIVE_ERRORS", 10)

        # Stagger first scan by bot name hash to avoid thundering herd on DB pool.
        # With 12 DB connections and 4+ bots starting simultaneously, all bots hit
        # kill switch DB check + scan_and_trade DB queries at once, exhausting the pool.
        _jitter = (hash(self.bot_name) % 20) + 5  # 5-24 seconds
        logger.info("Scan loop starting (first scan in %ds)", _jitter, bot_name=self.bot_name)
        await asyncio.sleep(_jitter)

        while self.running:
            try:
                # Kill switch check with 10s timeout — must never hang the scan loop.
                # Kill switch now uses get_raw_session() (bypasses semaphore), but
                # this timeout is defense-in-depth against any future regression.
                _ks_engaged = False
                try:
                    async def _check_kill_switch():
                        _mlks = getattr(self.base_engine, "multi_kill_switch", None)
                        if _mlks is not None:
                            try:
                                if not await _mlks.should_trade(self.bot_name):
                                    return True  # Kill switch engaged
                            except Exception:
                                pass  # Fall through to basic kill switch
                            else:
                                return False  # Multi-KS passed, not engaged
                        _ks = getattr(self.base_engine, "kill_switch", None)
                        if _ks is not None:
                            return await _ks.is_engaged()
                        return False

                    _ks_engaged = await asyncio.wait_for(_check_kill_switch(), timeout=10)
                except asyncio.TimeoutError:
                    # S159: Fail-CLOSED on UNKNOWN state. BUT a recent cached kill-switch
                    # state is NOT "unknown": the kill switch tolerates a 30s propagation
                    # delay by design (its TTL cache), so a transient slow DB check on the
                    # scan loop should reuse the last-known state instead of skipping the
                    # scan. Fail closed ONLY when there is no cached state at all.
                    # Scope: scan loop ONLY. The execution path (order_gateway /
                    # execution_engine) keeps the authoritative live check unchanged.
                    _cached_allow = None
                    _mlks = getattr(self.base_engine, "multi_kill_switch", None)
                    if _mlks is not None and hasattr(_mlks, "cached_should_trade"):
                        try:
                            _cached_allow = _mlks.cached_should_trade(self.bot_name)
                        except Exception:
                            _cached_allow = None
                    else:
                        _ks = getattr(self.base_engine, "kill_switch", None)
                        if _ks is not None and hasattr(_ks, "cached_engaged"):
                            try:
                                _ce = _ks.cached_engaged()
                                _cached_allow = (not _ce) if _ce is not None else None
                            except Exception:
                                _cached_allow = None
                    if _cached_allow is True:
                        _cache_age = None
                        _ks = getattr(self.base_engine, "kill_switch", None)
                        if _ks is not None and hasattr(_ks, "cache_age_seconds"):
                            try:
                                _cache_age = _ks.cache_age_seconds()
                            except Exception:
                                _cache_age = None
                        # kill_switch_cache_fallback=True is a PROXY INDICATOR for DB-pool
                        # pressure: frequent firing means the pool is still being exhausted
                        # (e.g. the esports engine-leak root fix has not landed / not worked).
                        logger.warning(
                            "Kill switch check timed out (10s) — reusing cached DISENGAGED state, continuing scan",
                            bot_name=self.bot_name,
                            kill_switch_cache_fallback=True,
                            cache_age_seconds=_cache_age,
                        )
                        _ks_engaged = False
                    else:
                        logger.warning(
                            "Kill switch check timed out (10s) — no safe cached state, failing closed, skipping scan",
                            bot_name=self.bot_name,
                            kill_switch_cache_fallback=False,
                        )
                        _ks_engaged = True
                except Exception as e:
                    logger.warning("Kill switch check failed — failing closed", bot_name=self.bot_name, error=str(e))
                    _ks_engaged = True

                if _ks_engaged:
                    logger.debug("Scan paused: kill switch engaged", bot_name=self.bot_name)
                    await asyncio.sleep(10)
                    continue
                # Phase 5.2: Drain whale priority queue before scan — log whale markets so
                # bots can optionally consume self._whale_priority_markets in scan_and_trade().
                _whale_priority: List[str] = []
                while not self._whale_priority_queue.empty():
                    try:
                        _whale_priority.append(self._whale_priority_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if _whale_priority:
                    logger.info(
                        "Whale-priority markets queued: %d markets",
                        len(_whale_priority),
                        markets=_whale_priority[:5],
                        bot_name=self.bot_name,
                    )
                # Expose via attribute so scan_and_trade() can consume if desired
                self._whale_priority_markets: List[str] = _whale_priority

                _scan_t0 = time.monotonic()
                _corr_id = str(uuid.uuid4())[:8]
                _bound = logger.bind(correlation_id=_corr_id, bot=self.bot_name)
                logger.info("Scan cycle starting", bot_name=self.bot_name)
                # S129: Clear signal metadata from prior cycle — entries not consumed
                # by a trade are stale. Without this, the dict grows unbounded (~37MB/day).
                self._pending_signal_meta.clear()
                self._current_correlation_id = _corr_id
                self._latency_tracker = _LatencyTracker()
                self._latency_tracker.mark("scan_start")
                # C2 FIX REMOVED (2026-06-02, EB — see EB_COORDINATION_BASE_BOT_811_CHERRYPICK.md).
                # The outer asyncio.wait_for() was itself the primary pool-corruption source:
                # on a slow scan it fired CancelledError into a mid-flight asyncpg op →
                # protocol corruption ("cannot switch to state N") → poisoned pooled
                # connection → SET statement_timeout then fails on reuse → subsequent queries
                # hang with NO bound → scan_and_trade wedged >900s → per-bot scan-stall
                # watchdog restart loop (observed live 2026-06-01/02). Every DB call in
                # scan_and_trade() routes through _SemaphoreSession/get_raw_session, which sets
                # the 30s server-side statement_timeout (database.py:203) — so the original
                # event-loop-hang concern is bounded per-query without a corrupting client-side
                # cancel. Any exception (incl. a clean 30s QueryCanceledError) propagates to the
                # consecutive-failures handler below, exactly as other scan errors always have.
                self._idle_event.clear()
                try:
                    await self.scan_and_trade()
                finally:
                    self._idle_event.set()
                self._latency_tracker.mark("scan_done")
                _scan_ms = (time.monotonic() - _scan_t0) * 1000
                # Log per-stage breakdown (INFO when slow, DEBUG otherwise)
                _breakdown = self._latency_tracker.report()
                if _breakdown:
                    if _scan_ms > 1000:
                        _bound.info("Latency breakdown", **_breakdown)
                    else:
                        _bound.debug("Latency breakdown", **_breakdown)
                if _scan_ms > getattr(settings, "ORDER_LATENCY_ALERT_MS", 5000):
                    logger.warning("Slow scan cycle", bot_name=self.bot_name, scan_ms=round(_scan_ms, 1))
                else:
                    logger.info("Scan cycle done", bot_name=self.bot_name, scan_ms=round(_scan_ms, 1))
                consecutive_failures = 0  # Reset on success
                # Report healthy scan to state machine so fleet tier can recover.
                _sm = getattr(self, "state_machine", None)
                if _sm is not None:
                    _sm.record_health_ok()
                # Session 51: record heartbeat for silent bot detection
                await self._record_heartbeat(scan_duration_ms=_scan_ms)

                # Optional burst: if StrategicTimer says burst, run one extra scan at half interval
                # Timer is initialized once in _get_scan_interval_seconds() with both
                # jitter_pct and burst_probability params.
                if getattr(settings, "USE_SCAN_JITTER", False) and self._strategic_timer is not None:
                    try:
                        if self._strategic_timer.should_burst():
                            logger.debug("%s: burst scan triggered", self.bot_name)
                            await asyncio.sleep(self._get_scan_interval() * 0.5)
                            # C2 FIX REMOVED (2026-06-02, EB) — same rationale as the main
                            # scan above: no corrupting client-side cancel; the per-query 30s
                            # server-side statement_timeout is the bound.
                            self._idle_event.clear()
                            try:
                                await self.scan_and_trade()
                            finally:
                                self._idle_event.set()
                    except Exception as e:
                        logger.debug("burst scan failed: %s", e)

                # Sleep only the REMAINDER of the interval (not full interval after scan completes)
                # Fixes: 8s scan + 60s sleep = 68s cycle → now correctly targets 60s true cadence
                _scan_elapsed = (time.monotonic() - _scan_t0)
                _interval = self._get_scan_interval_seconds()
                await asyncio.sleep(max(0.0, _interval - _scan_elapsed))
            except Exception as e:
                consecutive_failures += 1
                # Report error to state machine for fleet health tracking.
                _sm = getattr(self, "state_machine", None)
                if _sm is not None:
                    _sm.record_error(is_fatal=(consecutive_failures >= max_consecutive_failures))
                # Session 51: record error heartbeat
                await self._record_heartbeat_error(consecutive_failures)
                logger.error(
                    "Bot scan error",
                    bot_name=self.bot_name,
                    failure=consecutive_failures,
                    max_failures=max_consecutive_failures,
                    error=str(e),
                )

                # Alert on persistent errors (3+ consecutive failures)
                _alerting = getattr(self.base_engine, "alerting_system", None)
                if _alerting is not None and consecutive_failures >= 3:
                    try:
                        from base_engine.monitoring.alerting import AlertSeverity
                        _sev = AlertSeverity.CRITICAL if consecutive_failures >= max_consecutive_failures else AlertSeverity.WARNING
                        await _alerting.send_alert(
                            title=f"{self.bot_name} persistent errors",
                            message=f"{consecutive_failures}/{max_consecutive_failures} consecutive failures. Last: {str(e)[:200]}",
                            severity=_sev,
                            source=f"bot.{self.bot_name}",
                            metadata={"failures": consecutive_failures, "max": max_consecutive_failures},
                        )
                    except Exception:
                        pass

                # Exponential backoff: wait longer after repeated failures
                # Cap at 60 seconds to prevent excessive delays
                backoff_seconds = min(60, 2 ** min(consecutive_failures, 6))
                await asyncio.sleep(backoff_seconds)

                # Stop bot if too many consecutive failures (likely permanent issue)
                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "Bot stopped after max consecutive failures — check logs for root cause",
                        bot_name=self.bot_name,
                        max_failures=max_consecutive_failures,
                    )
                    self.running = False
                    break
    
    @abstractmethod
    async def scan_and_trade(self):
        pass
    
    @abstractmethod
    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        pass
    
    def _task_error_handler(self, task):
        """Handle errors from background tasks to prevent silent failures."""
        try:
            if task.cancelled():
                logger.info("Background task cancelled", bot_name=self.bot_name)
                return
            exception = task.exception()
            if exception:
                logger.error(
                    "Background task failed",
                    bot_name=self.bot_name,
                    task_name=task.get_name(),
                    error=str(exception),
                    exc_info=exception,
                )
        except Exception as e:
            logger.error("Error in task error handler", bot_name=self.bot_name, error=str(e))

    async def _whale_alert_listener(self) -> None:
        """Phase 5.2: Subscribe to Redis whale_alerts channel.
        Pushes market_ids to _whale_priority_queue so scan_loop drains them first.
        Reconnects with exponential backoff on failure. No-op when Redis is unavailable."""
        _backoff = 5.0
        _max_backoff = 60.0
        _stable_since = 0.0
        while self.running:
            try:
                _cache = getattr(self.base_engine, "cache", None)
                if _cache is None:
                    return
                pubsub = await _cache.subscribe("whale_alerts")
                if pubsub is None:
                    return
                logger.info("Whale alert listener started", bot_name=self.bot_name)
                _stable_since = time.monotonic()
                async for message in pubsub.listen():
                    if not self.running:
                        return
                    # Reset backoff after 60s of stable listening
                    if time.monotonic() - _stable_since > 60.0:
                        _backoff = 5.0
                    if message.get("type") != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        mid = str(data.get("market_id") or "")
                        if mid and not self._whale_priority_queue.full():
                            self._whale_priority_queue.put_nowait(mid)
                            logger.debug(
                                "Whale priority enqueued: market=%s value_usd=%.0f",
                                mid, float(data.get("value_usd", 0)),
                                bot_name=self.bot_name,
                            )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Whale alert listener error, retrying in %.0fs: %s",
                               _backoff, e, bot_name=self.bot_name)
                await asyncio.sleep(_backoff)
                _backoff = min(_backoff * 2, _max_backoff)