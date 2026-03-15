from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta, timezone
import math
import os
import time
from structlog import get_logger
from base_engine.data.database import Database, Position
from base_engine.utils.validation import validate_price, validate_confidence, validate_size, validate_numeric, safe_divide
from base_engine.exceptions import DatabaseError
from config.settings import settings

logger = get_logger()

# PipelineGate result cache TTL — 300s because ingestion loop guarantees data freshness;
# checking every 60s was adding 20-50ms DB hit to every trade within the minute window.
PIPELINE_GATE_CACHE_TTL = 300.0


class RiskManager:
    """Risk limits and loss guardrails. Triggers kill switch when daily/weekly loss exceeded."""

    def __init__(self, db: Database, kill_switch: Optional[Any] = None, alerting: Optional[Any] = None):
        self.db = db
        self.kill_switch = kill_switch
        self.alerting = alerting
        self.daily_exposure = {}
        self.position_counts = {}
        self._pipeline_gate_cache: Optional[Tuple[bool, Optional[Any]]] = None  # (passed, gate_result)
        self._pipeline_gate_cache_until: float = 0.0
        self._correlation_risk: Optional[Any] = None  # Set via set_correlation_risk()
        self._order_gateway: Optional[Any] = None  # Set via set_order_gateway() for in-memory fast path
        # Cache for risk_state (60s TTL) — avoids DB query on every trade
        self._risk_state_cache: Optional[Dict[str, Any]] = None
        self._risk_state_cache_until: float = 0.0
        _RISK_STATE_CACHE_TTL = 60.0
        # Conservative drawdown default: 0.05 until first HealthScheduler check runs (~60s).
        # Prevents full Kelly sizing during the first 30-60s after startup when the bot
        # may already be in a drawdown that hasn't been fetched from DB yet.
        self._cached_drawdown_pct: float = 0.05
        # Market volume cache: market_id → (combined_volume, expiry_monotonic)
        # TTL=3600s — market volume doesn't change faster than hourly in practice.
        self._market_vol_cache: Dict[str, Tuple[float, float]] = {}
        # Consecutive loss tracking: bot_name → count of consecutive losing closed trades.
        # Reset to 0 on any winning trade. Used by MAX_CONSECUTIVE_LOSSES guardrail.
        self._consecutive_losses: Dict[str, int] = {}

    def set_kill_switch(self, kill_switch: Any) -> None:
        """Wire kill switch after init (avoids circular init order)."""
        self.kill_switch = kill_switch

    def set_correlation_risk(self, correlation_risk: Any) -> None:
        """Wire CorrelationRiskManager for CVaR tail-risk checks."""
        self._correlation_risk = correlation_risk

    def set_order_gateway(self, order_gateway: Any) -> None:
        """Wire OrderGateway for in-memory position/exposure lookups (avoids 3+ DB queries)."""
        self._order_gateway = order_gateway

    def record_trade_outcome(self, bot_name: str, was_profitable: bool) -> None:
        """Update consecutive loss counter. Call after any closed trade with known P&L.
        Win resets the streak to 0; loss increments it. Used by MAX_CONSECUTIVE_LOSSES guardrail.
        """
        if was_profitable:
            self._consecutive_losses[bot_name] = 0
        else:
            self._consecutive_losses[bot_name] = self._consecutive_losses.get(bot_name, 0) + 1
        streak = self._consecutive_losses.get(bot_name, 0)
        if streak >= 2:
            logger.warning(
                "Consecutive loss streak",
                bot_name=bot_name,
                streak=streak,
                max=getattr(settings, "MAX_CONSECUTIVE_LOSSES", 0),
            )

    async def _get_market_volume(self, market_id: str) -> float:
        """Return combined volume+liquidity for a market, using a 1-hour TTL cache.
        Used by the universal volume gate in check_risk_limits(). Fails open (returns inf)
        on any DB error so a lookup failure never blocks a trade.
        """
        now = time.monotonic()
        cached = self._market_vol_cache.get(market_id)
        if cached and now < cached[1]:
            return cached[0]
        try:
            from sqlalchemy import text as _text
            async with self.db.get_session() as _s:
                row = await _s.execute(
                    _text("SELECT COALESCE(volume,0)+COALESCE(liquidity,0) FROM markets WHERE id=:mid LIMIT 1"),
                    {"mid": market_id},
                )
                val = row.scalar()
            result = float(val) if val is not None else float("inf")
        except Exception:
            result = float("inf")  # fail open — don't block on DB error
        self._market_vol_cache[market_id] = (result, now + 3600.0)
        return result

    async def pre_warm_risk_caches(self, market_ids: List[str]) -> None:
        """Batch-populate oracle manipulation and volume caches for a set of market IDs.

        Called once before a scan loop to avoid O(N) sequential DB queries when
        per-market caches expire.  Falls back silently on any error — individual
        check_risk_limits() calls will still hit the DB per-market as before.
        """
        if not market_ids or not self.db or not getattr(self.db, "session_factory", None):
            return

        now = time.monotonic()

        # --- Oracle manipulation cache: warm expired keys -----------------
        if not hasattr(self, "_oracle_risk_cache"):
            self._oracle_risk_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
        expired_oracle = [
            mid for mid in market_ids
            if mid not in self._oracle_risk_cache or now >= self._oracle_risk_cache[mid][1]
        ]
        if expired_oracle:
            try:
                from sqlalchemy import text as _text
                async with self.db.get_session() as session:
                    rows = await session.execute(
                        _text(
                            "SELECT market_id, "
                            "COALESCE(SUM(size * entry_price), 0), "
                            "COUNT(*) "
                            "FROM positions "
                            "WHERE market_id = ANY(:mids) AND status = 'open' "
                            "GROUP BY market_id"
                        ),
                        {"mids": expired_oracle},
                    )
                    found = {}
                    uma_bond_cost = 5000.0
                    for row in rows.fetchall():
                        mid, oi, cnt = str(row[0]), float(row[1]), int(row[2])
                        risk_ratio = (oi / uma_bond_cost) / 10 if oi > 0 else 0
                        risk_score = min(1.0, risk_ratio)
                        result = {
                            "market_id": mid,
                            "open_interest": oi,
                            "position_count": cnt,
                            "manipulation_cost": uma_bond_cost,
                            "risk_score": risk_score,
                            "is_risky": risk_score > 0.5,
                        } if oi > 0 else None
                        self._oracle_risk_cache[mid] = (result, now + 60.0)
                        found[mid] = True
                    # Markets with no open positions → cache None
                    for mid in expired_oracle:
                        if mid not in found:
                            self._oracle_risk_cache[mid] = (None, now + 60.0)
            except Exception as e:
                logger.debug("pre_warm oracle cache failed (non-blocking): %s", e)

        # --- Market volume cache: warm expired keys -----------------------
        expired_vol = [
            mid for mid in market_ids
            if mid not in self._market_vol_cache or now >= self._market_vol_cache[mid][1]
        ]
        if expired_vol:
            try:
                from sqlalchemy import text as _text
                async with self.db.get_session() as session:
                    rows = await session.execute(
                        _text(
                            "SELECT id, COALESCE(volume, 0) + COALESCE(liquidity, 0) "
                            "FROM markets WHERE id = ANY(:mids)"
                        ),
                        {"mids": expired_vol},
                    )
                    found_vol = {}
                    for row in rows.fetchall():
                        mid, vol = str(row[0]), float(row[1])
                        self._market_vol_cache[mid] = (vol, now + 3600.0)
                        found_vol[mid] = True
                    # Markets not in DB → fail open (inf)
                    for mid in expired_vol:
                        if mid not in found_vol:
                            self._market_vol_cache[mid] = (float("inf"), now + 3600.0)
            except Exception as e:
                logger.debug("pre_warm volume cache failed (non-blocking): %s", e)

    async def _get_open_positions_for_cvar(self) -> List[Dict[str, Any]]:
        """Fetch open positions formatted for CVaR computation.
        FAST PATH: Uses OrderGateway in-memory snapshot when available.
        """
        # Fast path: use in-memory position data from OrderGateway
        og = self._order_gateway
        if og is not None:
            return og.get_all_open_positions_snapshot()

        # DB fallback
        positions = []
        if not self.db or not self.db.session_factory:
            return positions
        try:
            async with self.db.get_session() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(Position).where(Position.status == "open")
                )
                for row in result.scalars().all():
                    positions.append({
                        "market_id": row.market_id,
                        "side": row.side or "YES",
                        "size": float(row.size or 0),
                        "price": float(row.entry_price or 0.5),
                        "predicted_prob": 0.5,
                    })
        except Exception as e:
            logger.debug("Failed to fetch positions for CVaR: %s", e)
        return positions
    
    async def check_risk_limits(
        self,
        bot_name: str,
        market_id: str,
        size: float,
        price: float,
        confidence: float,
        prediction: Optional[float] = None,
    ) -> Dict[str, Any]:
        checks = {
            "allowed": True,
            "reasons": []
        }

        # PipelineGate: refuse risk evaluation when data is stale. Phase 8: 60s cache.
        # Blocks in all modes — paper trading is production.
        if self.db and getattr(self.db, "session_factory", None):
            try:
                now = time.monotonic()
                if now < self._pipeline_gate_cache_until and self._pipeline_gate_cache is not None:
                    passed, _ = self._pipeline_gate_cache
                    if not passed:
                        checks["allowed"] = False
                        checks["reasons"].append("Data freshness check failed")
                        return checks
                else:
                    from base_engine.monitoring.pipeline_gate import PipelineGate
                    from base_engine.monitoring.alerting import AlertSeverity

                    gate = PipelineGate(self.db, alerting=self.alerting)
                    gate_result = await gate.check_before_risk()
                    self._pipeline_gate_cache = (gate_result.passed, gate_result)
                    self._pipeline_gate_cache_until = now + PIPELINE_GATE_CACHE_TTL
                    if not gate_result.passed:
                        logger.error(
                            "Risk gate failed — refusing to evaluate. Data may be stale: %s",
                            gate_result.summary,
                        )
                        if self.alerting:
                            await self.alerting.send_alert(
                                title="Risk gate failed — data freshness check",
                                message=gate_result.summary,
                                severity=AlertSeverity.ERROR,
                                source="pipeline_gate",
                                metadata={"failures": gate_result.failures},
                            )
                        checks["allowed"] = False
                        checks["reasons"].append("Data freshness check failed")
                        return checks
            except Exception as e:
                logger.warning("PipelineGate check failed (proceeding with risk eval): %s", e)

        try:
            confidence = validate_confidence(confidence, "confidence")
            price = validate_price(price, "price")
            size = validate_size(size, "size")
        except ValueError as e:
            checks["allowed"] = False
            checks["reasons"].append(str(e))
            return checks
        
        # BUG FIX: Calculate position value correctly and compare with proper units
        # Root cause: Comparing position_value (dollar amount) to MAX_POSITION_SIZE_PCT (percentage)
        # Impact: Risk checks fail incorrectly, positions rejected due to unit mismatch
        # Fix: Calculate position as percentage of total capital and compare properly
        position_value = validate_numeric(size * price, "position_value", min_val=0.0)
        if settings.TOTAL_CAPITAL <= 0:
            checks["allowed"] = False
            checks["reasons"].append("TOTAL_CAPITAL must be > 0")
            return checks
        position_size_pct = position_value / settings.TOTAL_CAPITAL  # Convert to percentage
        
        # WeatherBot uses multi-bucket markets (9 temperature outcomes) where
        # single-bucket model_prob rarely exceeds 45%.  Use WEATHER_MIN_CONFIDENCE.
        _min_conf = settings.MIN_CONFIDENCE_THRESHOLD
        if bot_name == "WeatherBot":
            _min_conf = getattr(settings, "WEATHER_MIN_CONFIDENCE", _min_conf)
        if confidence < _min_conf:
            checks["allowed"] = False
            checks["reasons"].append(f"Confidence {confidence:.2%} below threshold {_min_conf:.2%}")

        # Consecutive loss guardrail: pause trading after N consecutive losing trades.
        # 0 = disabled. Learning phase = 3. Resets on any winning trade via record_trade_outcome().
        _max_consec = getattr(settings, "MAX_CONSECUTIVE_LOSSES", 0)
        if _max_consec > 0 and bot_name:
            _consec = self._consecutive_losses.get(bot_name, 0)
            if _consec >= _max_consec:
                checks["allowed"] = False
                checks["reasons"].append(
                    f"Consecutive loss limit reached ({_consec}/{_max_consec}) — trading paused"
                )

        # Edge filter: only trade when model disagrees with market by at least MIN_EDGE
        # B3: In simulation mode, halve the cost_edge (paper trades pay no real fees)
        # B3: Polymarket resolution is FREE — no exit fee — so use one-way cost, not round-trip
        if prediction is not None and 0 <= price <= 1:
            edge = prediction - price
            min_edge = getattr(settings, "RISK_MIN_EDGE_PCT", 2) / 100.0
            try:
                from base_engine.risk.transaction_cost import TransactionCostModel
                order_value = size * price
                if order_value > 0:
                    cost_model = TransactionCostModel()
                    cost_edge = cost_model.min_edge_for_profitability(order_value, 0)
                    # Transaction cost edge: single-sided cost model (same in all modes).
                    # PaperTradingEngine handles exit fee accounting internally.
                    min_edge = max(min_edge, cost_edge)
            except Exception as e:
                logger.debug("transaction cost model edge calculation failed: %s", e)
            if edge < min_edge:
                checks["allowed"] = False
                checks["reasons"].append(f"Edge {edge:.2%} below threshold {min_edge:.2%} (prediction vs price)")

        max_pos_usd = getattr(settings, "RISK_MAX_POSITION_SIZE_USD", 100.0)
        if position_value > max_pos_usd:
            checks["allowed"] = False
            checks["reasons"].append(f"Position ${position_value:.2f} exceeds max ${max_pos_usd:.2f}")

        _global_min_price = getattr(settings, "RISK_MIN_PRICE", 0.05)
        _global_max_price = getattr(settings, "RISK_MAX_PRICE", 0.95)
        # Bot-specific override: RISK_MIN_PRICE_{BOTNAME} / RISK_MAX_PRICE_{BOTNAME}
        # (e.g. RISK_MIN_PRICE_WEATHERBOT=0.005 lets weather markets priced at 1-2% through)
        _bot_price_min_key = f"RISK_MIN_PRICE_{bot_name.upper()}" if bot_name else None
        _bot_price_max_key = f"RISK_MAX_PRICE_{bot_name.upper()}" if bot_name else None
        min_price = float(os.getenv(_bot_price_min_key, _global_min_price)) if _bot_price_min_key else _global_min_price
        max_price = float(os.getenv(_bot_price_max_key, _global_max_price)) if _bot_price_max_key else _global_max_price
        if price < min_price or price > max_price:
            checks["allowed"] = False
            checks["reasons"].append(f"Price {price:.2%} outside bounds [{min_price:.2%}, {max_price:.2%}]")

        # Volume gate: thin markets have unreliable price discovery and high slippage.
        # Bot-specific override: RISK_MIN_VOL_{BOTNAME} (e.g. RISK_MIN_VOL_WEATHERBOT=0)
        # takes precedence over the global ENSEMBLE_MIN_MARKET_VOLUME_USD floor.
        # This lets WeatherBot (volume=0 in DB) bypass the gate without lowering it for all bots.
        if market_id and self.db and getattr(self.db, "session_factory", None):
            _global_min_vol = float(getattr(settings, "ENSEMBLE_MIN_MARKET_VOLUME_USD", 5000.0))
            _bot_vol_key = f"RISK_MIN_VOL_{bot_name.upper()}" if bot_name else None
            _min_vol = float(os.getenv(_bot_vol_key, _global_min_vol)) if _bot_vol_key else _global_min_vol
            if _min_vol > 0:
                _market_vol = await self._get_market_volume(market_id)
                if _market_vol < _min_vol:
                    checks["allowed"] = False
                    checks["reasons"].append(
                        f"Market volume ${_market_vol:.0f} below minimum ${_min_vol:.0f}"
                    )

        if position_size_pct > settings.MAX_POSITION_SIZE_PCT:
            checks["allowed"] = False
            checks["reasons"].append(f"Position size {position_size_pct:.2%} exceeds max {settings.MAX_POSITION_SIZE_PCT:.2%}")
        
        # FAST PATH: Use in-memory position/exposure data from OrderGateway if available.
        # Eliminates 3 sequential DB queries (position count, total exposure, daily exposure).
        og = self._order_gateway
        if og is not None:
            count = og.get_position_count(bot_name)
            max_positions = getattr(settings, "RISK_MAX_POSITIONS_COUNT", None) or settings.MAX_POSITIONS_PER_BOT
            # WeatherBot: multi-bucket markets (28 groups × up to 9 buckets) need higher cap
            if bot_name == "WeatherBot":
                max_positions = getattr(settings, "WEATHER_MAX_POSITIONS", max_positions)
            # MirrorBot: 51 pre-fix BUY positions exceed global 50 cap; needs higher limit
            if bot_name == "MirrorBot":
                max_positions = getattr(settings, "MIRROR_MAX_POSITIONS", max_positions)
            if count > max_positions:
                checks["allowed"] = False
                checks["reasons"].append(f"Max positions {max_positions} exceeded (have {count})")

            # Per-bot exposure cap: prevents one bot from consuming entire exposure budget.
            # Override via RISK_MAX_EXPOSURE_{BOTNAME} env var (e.g. RISK_MAX_EXPOSURE_MIRRORBOT=6000).
            # When set, the bot is checked against its own budget instead of the global cap.
            _bot_exp_key = f"RISK_MAX_EXPOSURE_{bot_name.upper()}" if bot_name else None
            _per_bot_max_str = os.getenv(_bot_exp_key, "") if _bot_exp_key else ""
            _per_bot_max = float(_per_bot_max_str) if _per_bot_max_str else 0.0
            if _per_bot_max > 0:
                bot_exposure = og.get_bot_exposure_usd(bot_name)
                if bot_exposure + position_value > _per_bot_max:
                    checks["allowed"] = False
                    checks["reasons"].append(
                        f"{bot_name} exposure ${bot_exposure + position_value:.2f} exceeds bot max ${_per_bot_max:.2f}"
                    )

            total_exposure = og.get_total_exposure_usd()
            max_total = getattr(settings, "RISK_MAX_TOTAL_EXPOSURE_USD", 500.0)
            # WeatherBot: use bot-specific exposure (not global) + higher cap to allow
            # multi-bucket trading across 30+ city/date groups simultaneously.
            if bot_name == "WeatherBot":
                total_exposure = og.get_bot_exposure_usd(bot_name)
                max_total = getattr(settings, "WEATHER_MAX_TOTAL_EXPOSURE_USD", max_total)
            # EsportsBot variants: same pattern — isolate from MirrorBot/WeatherBot exposure.
            elif bot_name in ("EsportsBot", "EsportsLiveBot", "EsportsSeriesBot"):
                total_exposure = og.get_bot_exposure_usd(bot_name)
                max_total = getattr(settings, "ESPORTS_MAX_TOTAL_EXPOSURE_USD", max_total)
            if total_exposure + position_value > max_total:
                checks["allowed"] = False
                checks["reasons"].append(
                    f"Total exposure ${total_exposure + position_value:.2f} exceeds max ${max_total:.2f}"
                )

            daily_exposure = og.get_daily_exposure_usd(bot_name)
            daily_exposure_pct = daily_exposure / settings.TOTAL_CAPITAL
            new_total_exposure_pct = (daily_exposure + position_value) / settings.TOTAL_CAPITAL
            if new_total_exposure_pct > settings.MAX_DAILY_EXPOSURE:
                checks["allowed"] = False
                checks["reasons"].append(f"Daily exposure limit {settings.MAX_DAILY_EXPOSURE:.2%} would be exceeded (current: {daily_exposure_pct:.2%}, new total: {new_total_exposure_pct:.2%})")
        elif self.db.session_factory is not None:
            # DB FALLBACK: only used when OrderGateway not wired (e.g. tests, standalone risk check)
            async with self.db.get_session() as session:
                from sqlalchemy import select, func, or_

                active_positions = await session.execute(
                    select(func.count(Position.id)).where(
                        or_(
                            Position.bot_id == bot_name,
                            Position.source_bot == bot_name,
                        ),
                        Position.status == "open"
                    )
                )
                count = active_positions.scalar() or 0
                max_positions = getattr(settings, "RISK_MAX_POSITIONS_COUNT", None) or settings.MAX_POSITIONS_PER_BOT
                if bot_name == "WeatherBot":
                    max_positions = getattr(settings, "WEATHER_MAX_POSITIONS", max_positions)
                if bot_name == "MirrorBot":
                    max_positions = getattr(settings, "MIRROR_MAX_POSITIONS", max_positions)

                if count > max_positions:
                    checks["allowed"] = False
                    checks["reasons"].append(f"Max positions {max_positions} exceeded (have {count})")

                # Per-bot exposure cap (DB fallback path)
                _bot_exp_key = f"RISK_MAX_EXPOSURE_{bot_name.upper()}" if bot_name else None
                _per_bot_max_str = os.getenv(_bot_exp_key, "") if _bot_exp_key else ""
                _per_bot_max = float(_per_bot_max_str) if _per_bot_max_str else 0.0
                if _per_bot_max > 0:
                    bot_exp_query = await session.execute(
                        select(func.coalesce(func.sum(Position.size * Position.entry_price), 0)).where(
                            or_(Position.bot_id == bot_name, Position.source_bot == bot_name),
                            Position.status == "open"
                        )
                    )
                    bot_exposure = bot_exp_query.scalar() or 0.0
                    if bot_exposure + position_value > _per_bot_max:
                        checks["allowed"] = False
                        checks["reasons"].append(
                            f"{bot_name} exposure ${bot_exposure + position_value:.2f} exceeds bot max ${_per_bot_max:.2f}"
                        )

                total_exposure_query = await session.execute(
                    select(func.coalesce(func.sum(Position.size * Position.entry_price), 0)).where(
                        Position.status == "open"
                    )
                )
                total_exposure = total_exposure_query.scalar() or 0.0
                max_total = getattr(settings, "RISK_MAX_TOTAL_EXPOSURE_USD", 500.0)
                # WeatherBot: apply higher bot-specific cap (DB fallback path)
                if bot_name == "WeatherBot":
                    max_total = getattr(settings, "WEATHER_MAX_TOTAL_EXPOSURE_USD", max_total)
                if total_exposure + position_value > max_total:
                    checks["allowed"] = False
                    checks["reasons"].append(
                        f"Total exposure ${total_exposure + position_value:.2f} exceeds max ${max_total:.2f}"
                    )

                today = datetime.now(timezone.utc).date()
                today_utc_naive = datetime.combine(today, datetime.min.time()).replace(tzinfo=None)
                daily_exposure_query = await session.execute(
                    select(func.sum(Position.size * Position.entry_price)).where(
                        or_(
                            Position.bot_id == bot_name,
                            Position.source_bot == bot_name,
                        ),
                        Position.opened_at >= today_utc_naive,
                        Position.status == "open"
                    )
                )
                daily_exposure = daily_exposure_query.scalar() or 0.0
                daily_exposure_pct = daily_exposure / settings.TOTAL_CAPITAL
                new_total_exposure_pct = (daily_exposure + position_value) / settings.TOTAL_CAPITAL

                if new_total_exposure_pct > settings.MAX_DAILY_EXPOSURE:
                    checks["allowed"] = False
                    checks["reasons"].append(f"Daily exposure limit {settings.MAX_DAILY_EXPOSURE:.2%} would be exceeded (current: {daily_exposure_pct:.2%}, new total: {new_total_exposure_pct:.2%})")
        else:
            raise DatabaseError(
                "Database required for risk checks. Cannot proceed without database connection.",
                operation="check_risk_limits",
                table="positions"
            )

        # Oracle manipulation risk factor: check if manipulation cost vs market OI makes it risky
        oracle_risk_enabled = getattr(settings, "ORACLE_MANIPULATION_RISK_ENABLED", True)
        if oracle_risk_enabled and checks["allowed"]:
            try:
                manipulation_risk = await self._check_oracle_manipulation_risk(market_id, size * price)
                if manipulation_risk and manipulation_risk.get("is_risky"):
                    checks["reasons"].append(
                        f"Oracle manipulation risk: cost ${manipulation_risk.get('manipulation_cost', 0):.0f} "
                        f"vs OI ${manipulation_risk.get('open_interest', 0):.0f}"
                    )
                    # Don't block, but reduce allowed size
                    if manipulation_risk.get("risk_score", 0) > 0.8:
                        checks["allowed"] = False
                        checks["reasons"].append("Oracle manipulation risk too high — blocking trade")
            except Exception as e:
                logger.debug("Oracle manipulation risk check failed: %s", e)

        # Check persistent loss limits (risk_state)
        daily_loss_limit = getattr(settings, "RISK_MAX_DAILY_LOSS_USD", 50.0)
        weekly_loss_limit = getattr(settings, "RISK_MAX_WEEKLY_LOSS_USD", 150.0)
        if self.db.session_factory:
            state = await self._get_risk_state()
            if state:
                if state.get("daily_pnl", 0) <= -daily_loss_limit:
                    reason = f"Daily loss limit exceeded (${state['daily_pnl']:.2f})"
                    checks["allowed"] = False
                    checks["reasons"].append(reason)
                    if self.kill_switch:
                        await self.kill_switch.engage(reason)
                elif state.get("weekly_pnl", 0) <= -weekly_loss_limit:
                    reason = f"Weekly loss limit exceeded (${state['weekly_pnl']:.2f})"
                    checks["allowed"] = False
                    checks["reasons"].append(reason)
                    if self.kill_switch:
                        await self.kill_switch.engage(reason)

        # DEAD-1 fix: CVaR tail-risk gate via CorrelationRiskManager
        # Only runs when allowed so far and correlation_risk is available
        if checks["allowed"] and self._correlation_risk is not None:
            try:
                max_cvar = getattr(settings, "RISK_MAX_PORTFOLIO_CVAR_USD", 200.0)
                existing_positions = await self._get_open_positions_for_cvar()
                new_pos = {
                    "market_id": market_id,
                    "side": "YES",
                    "size": size,
                    "price": price,
                    "predicted_prob": prediction if prediction is not None else 0.5,
                }
                marginal = self._correlation_risk.compute_marginal_cvar(existing_positions, new_pos)
                cvar_after = self._correlation_risk.compute_cvar(existing_positions + [new_pos])
                portfolio_cvar = cvar_after.get("cvar", 0)
                if portfolio_cvar > max_cvar:
                    checks["allowed"] = False
                    checks["reasons"].append(
                        f"Portfolio CVaR ${portfolio_cvar:.2f} exceeds max ${max_cvar:.2f} "
                        f"(marginal impact: +${marginal:.2f})"
                    )
                    logger.warning(
                        "CVaR limit exceeded",
                        bot_name=bot_name, market_id=market_id,
                        portfolio_cvar=portfolio_cvar, max_cvar=max_cvar,
                        marginal_cvar=round(marginal, 2),
                    )
            except Exception as e:
                logger.debug("CVaR risk check failed (non-blocking): %s", e)

        # PCA factor exposure gate: prevent over-concentration in correlated clusters
        # Runs after CVaR when correlation_risk provides PCA factor data
        if checks["allowed"] and self._correlation_risk is not None:
            try:
                _max_factor_usd = float(getattr(settings, "RISK_MAX_FACTOR_EXPOSURE_USD", 500.0))
                if _max_factor_usd > 0:
                    _corr_strat = getattr(self._correlation_risk, "_correlation_strategy", None)
                    if _corr_strat is not None and hasattr(_corr_strat, "compute_factor_exposure"):
                        existing_positions = await self._get_open_positions_for_cvar()
                        # Add proposed position
                        all_positions = existing_positions + [{
                            "market_id": market_id, "size": size, "price": price,
                        }]
                        market_factor_map = getattr(self._correlation_risk, "_market_factor_map", None)
                        if market_factor_map:
                            factor_exposure = _corr_strat.compute_factor_exposure(
                                all_positions, market_factor_map
                            )
                            factor_check = _corr_strat.check_factor_limits(
                                factor_exposure, max_factor_exposure_usd=_max_factor_usd
                            )
                            if not factor_check["allowed"]:
                                checks["allowed"] = False
                                for v in factor_check["violations"]:
                                    checks["reasons"].append(
                                        f"Factor {v['factor']} exposure ${v['exposure_usd']:.2f} "
                                        f"exceeds limit ${v['limit_usd']:.2f}"
                                    )
            except Exception as e:
                logger.debug("PCA factor exposure check failed (non-blocking): %s", e)

        return checks

    # ── Time-Horizon Capital Bucketing ─────────────────────────────────────

    def get_capital_bucket(self, days_to_expiry: float) -> str:
        """Classify market into capital bucket by days to resolution."""
        if days_to_expiry <= 30:
            return "short_term"
        elif days_to_expiry <= 180:
            return "medium_term"
        else:
            return "long_term"

    def get_bucket_allocation(self, bucket: str) -> float:
        """
        Get USD allocation for a capital bucket.

        Default splits (configurable via settings):
        - short_term (<30d): 40% — higher turnover, faster feedback
        - medium_term (30-180d): 35% — political season trades
        - long_term (>180d): 5% — structural positions
        - liquid_reserve: 20% — opportunistic entries on breaking news
        """
        total = float(getattr(settings, "TOTAL_CAPITAL", 100000.0))
        pcts = {
            "short_term": float(getattr(settings, "BUCKET_SHORT_TERM_PCT", 0.40)),
            "medium_term": float(getattr(settings, "BUCKET_MEDIUM_TERM_PCT", 0.35)),
            "long_term": float(getattr(settings, "BUCKET_LONG_TERM_PCT", 0.05)),
            "liquid_reserve": float(getattr(settings, "BUCKET_LIQUID_RESERVE_PCT", 0.20)),
        }
        return total * pcts.get(bucket, 0.05)

    def check_bucket_capacity(
        self,
        days_to_expiry: float,
        proposed_usd: float,
        current_bucket_exposure: float,
    ) -> Dict[str, Any]:
        """
        Check if a trade fits within its time-horizon bucket.

        Args:
            days_to_expiry: Days until market resolution
            proposed_usd: USD value of proposed trade
            current_bucket_exposure: Current USD exposure in this bucket

        Returns:
            allowed: bool, bucket: str, remaining: float
        """
        bucket = self.get_capital_bucket(days_to_expiry)
        allocation = self.get_bucket_allocation(bucket)
        remaining = allocation - current_bucket_exposure

        allowed = proposed_usd <= remaining
        return {
            "allowed": allowed,
            "bucket": bucket,
            "allocation_usd": round(allocation, 2),
            "current_exposure_usd": round(current_bucket_exposure, 2),
            "remaining_usd": round(max(0, remaining), 2),
            "proposed_usd": round(proposed_usd, 2),
        }

    async def check_arbitrage_risk_limits(
        self,
        bot_name: str,
        legs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Arbitrage-specific risk: treat hedged multi-leg as single exposure; cap leg-failure exposure.
        Only arbitrage bot calls this; other bots use check_risk_limits for single orders.
        legs: list of {"market_id", "token_id", "side", "size", "price"}.
        """
        out = {"allowed": True, "reasons": []}
        if not legs:
            return out
        try:
            leg_values = []
            for leg in legs:
                size = validate_numeric(float(leg.get("size", 0)), "size", min_val=0.0)
                price = validate_price(float(leg.get("price", 0)), "price")
                leg_values.append(size * price)
            max_leg_usd = max(leg_values) if leg_values else 0.0
            hedged_mult = getattr(settings, "ARB_RISK_HEDGED_EXPOSURE_MULTIPLIER", 0.5)
            hedged_exposure = max_leg_usd * hedged_mult
            max_total = getattr(settings, "RISK_MAX_TOTAL_EXPOSURE_USD", 500.0)
            if hedged_exposure > max_total:
                out["allowed"] = False
                out["reasons"].append(
                    f"Arb hedged exposure ${hedged_exposure:.2f} exceeds max ${max_total:.2f}"
                )
            max_leg_failure = getattr(settings, "ARB_RISK_MAX_LEG_FAILURE_EXPOSURE_USD", 200.0)
            if max_leg_usd > max_leg_failure:
                out["allowed"] = False
                out["reasons"].append(
                    f"Arb leg failure exposure ${max_leg_usd:.2f} exceeds max ${max_leg_failure:.2f}"
                )
        except (ValueError, TypeError) as e:
            out["allowed"] = False
            out["reasons"].append(str(e))
        return out
    
    async def calculate_position_size(
        self,
        bot_name: str,
        confidence: float,
        available_capital: float,
        price: float,
        calibration_quality: Optional[Dict[str, float]] = None,
        market_vol: float = 0.0,
        category: str = "",
    ) -> float:
        confidence = validate_confidence(confidence, "confidence")
        price = validate_price(price, "price")
        available_capital = validate_size(available_capital, "available_capital")

        # ── Quarter-Kelly sizing (all bots) ──────────────────────────────
        # Kelly criterion: f* = (p*b - q) / b
        #   p = confidence (side-adjusted model probability)
        #   b = (1 - price) / price  (decimal odds minus 1)
        #   q = 1 - p
        # Fractional Kelly (default 0.25) reduces variance at cost of growth rate.
        # Edge filter upstream guarantees confidence > price (positive edge).
        # Per-bot scaling: divide fraction by number of active bots to prevent
        # total exposure from exceeding full Kelly when all bots trade simultaneously.

        # 2e: Category-specific base Kelly fraction (replaces global KELLY_FRACTION per category).
        # Volatile categories (crypto) get lower fractions; high-edge categories (weather) get higher.
        _cat = (category or "").lower().strip()
        fraction = getattr(settings, "KELLY_FRACTION", 0.25)
        if _cat:
            try:
                import json as _json_kelly
                _cat_fracs = _json_kelly.loads(getattr(settings, "CATEGORY_KELLY_FRACTIONS", "{}"))
                if _cat in _cat_fracs:
                    fraction = float(_cat_fracs[_cat])
                    logger.debug("Category Kelly fraction: cat=%s fraction=%.3f", _cat, fraction)
            except Exception:
                pass

        # DEPRECATED (Session 47): Per-bot sizing now handled by BotBankrollManager.
        # This legacy divisor is kept ONLY for bots that haven't migrated to BotBankrollManager.
        # BotBankrollManager gives each bot its own capital pool with no shared divisor.
        _n_bots = max(1, getattr(settings, "KELLY_ACTIVE_BOTS", 3))
        fraction = fraction / _n_bots

        if price <= 0 or price >= 1:
            return 0.0
        b = (1.0 - price) / price
        if b <= 0:
            return 0.0
        q = 1.0 - confidence
        kelly_full = (confidence * b - q) / b
        if kelly_full <= 0:
            return 0.0  # negative edge — don't bet (shouldn't happen with edge filter)

        kelly_frac = kelly_full * fraction

        # Calibration-aware fraction reduction: scale Kelly down when Brier is poor.
        # Good Brier (< 0.15): full fraction. Mediocre (0.15-0.30): reduce 15-50%.
        # Poor (> 0.30): halve fraction (0.50× floor).
        if calibration_quality and calibration_quality.get("count", 0) >= 20:
            brier = calibration_quality.get("brier", 0.25)
            if brier > 0.15:
                cal_floor = 0.50
                cal_multiplier = max(cal_floor, 1.0 - (brier - 0.15) * 3.33)
                kelly_frac *= cal_multiplier
                logger.debug("Kelly calibration adj: brier=%.3f mult=%.2f", brier, cal_multiplier)

        # Drawdown-dependent Kelly compression — preserve capital during drawdowns
        try:
            _dd_drawdown_pct = getattr(self, "_cached_drawdown_pct", 0.0)
            if _dd_drawdown_pct > 0.02:  # Only compress if drawdown > 2%
                _compress = max(0.30, 1.0 - _dd_drawdown_pct * 4.0)  # 5% dd → 0.8×, 10% → 0.6×, 17.5%+ → 0.3×
                kelly_frac *= _compress
                logger.debug("Kelly drawdown compress: dd=%.1f%% compress=%.2f", _dd_drawdown_pct * 100, _compress)
        except Exception:
            pass

        position_usd = kelly_frac * available_capital

        # Cap at MAX_POSITION_SIZE_PCT of capital
        max_pct = settings.MAX_POSITION_SIZE_PCT
        position_usd = min(position_usd, max_pct * available_capital)

        # D7: Cap dollar amount at RISK_MAX_POSITION_SIZE_USD.
        # Edge-proportional cap: low-edge trades get a fraction of the max cap.
        _base_max_usd = getattr(settings, "RISK_MAX_POSITION_SIZE_USD", 1000.0)
        _edge = max(0.0, confidence - price)
        _edge_scale = min(1.0, _edge / 0.15)      # 5% edge→0.33×; 15%+ edge→1× full cap
        max_pos_usd = _base_max_usd * max(0.2, _edge_scale)  # floor at 20%
        position_usd = min(position_usd, max_pos_usd)

        # Volatility-scaled sizing — reduce size on high-vol markets.
        _mkt_vol = float(market_vol or 0.0)
        if _mkt_vol > 0.0:
            _vol_divisor = max(1.0, 1.0 + _mkt_vol * getattr(settings, "VOL_SCALE_FACTOR", 2.0))
            position_usd /= _vol_divisor
            logger.debug("Kelly vol-scaled: vol=%.3f divisor=%.2f", _mkt_vol, _vol_divisor)

        # 2b: Phase-based max bet cap — hard limit by operational phase.
        # Overrides Kelly if the Kelly-sized bet exceeds the phase cap.
        # Prevents oversizing during paper/learning phases while risk-model is calibrating.
        try:
            import json as _json_phase
            _phase = getattr(settings, "TRADING_PHASE", "paper").lower()
            _phase_caps = _json_phase.loads(getattr(settings, "PHASE_MAX_BET_USD", "{}"))
            _phase_cap_usd = float(_phase_caps.get(_phase, _phase_caps.get("paper", 15.0)))
            if position_usd > _phase_cap_usd:
                logger.debug(
                    "Phase bet cap: phase=%s cap_usd=%.2f (kelly was %.2f)",
                    _phase, _phase_cap_usd, position_usd,
                )
                position_usd = _phase_cap_usd
        except Exception:
            pass

        shares = safe_divide(position_usd, price, default=0.0)
        logger.info(
            "Kelly sizing: bot=%s cat=%s phase=%s full=%.4f frac=%.4f usd=%.2f shares=%.1f conf=%.3f price=%.3f edge=%.3f",
            bot_name, _cat or "?", getattr(settings, "TRADING_PHASE", "paper"),
            kelly_full, kelly_frac, position_usd, shares,
            confidence, price, confidence - price,
        )
        return validate_numeric(shares, "kelly_shares", min_val=0.0)
    
    async def update_position(
        self,
        bot_name: str,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float
    ) -> None:
        if self.db.session_factory is None:
            raise DatabaseError(
                "Database required for position updates. Cannot proceed without database connection.",
                operation="update_position",
                table="positions",
                bot_name=bot_name,
                market_id=market_id
            )
        
        size = validate_size(size, "size")
        price = validate_price(price, "price")
        
        if side not in ["YES", "NO"]:
            raise ValueError(f"Invalid side: {side}. Must be 'YES' or 'NO'")
        
        async with self.db.get_session() as session:
            try:
                async with session.begin():
                    from sqlalchemy import select, text
                    
                    await session.execute(
                        text("SELECT * FROM positions WHERE bot_id = :bot_id AND market_id = :market_id AND status = 'open' FOR UPDATE"),
                        {"bot_id": bot_name, "market_id": market_id}
                    )
                    
                    position = Position(
                        bot_id=bot_name,
                        market_id=market_id,
                        token_id=token_id,
                        side=side,
                        size=size,
                        entry_price=price,
                        current_price=price,
                        unrealized_pnl=0.0,
                        opened_at=datetime.now(timezone.utc),
                        status="open",
                        is_paper=bool(getattr(settings, "SIMULATION_MODE", False)),
                    )
                    session.add(position)
            except Exception as e:
                logger.error(f"Failed to create position: {str(e)}", exc_info=True)
                raise
    
    async def close_position(
        self,
        bot_name: str,
        market_id: str,
        exit_price: float
    ) -> Optional[Dict[str, Any]]:
        if self.db.session_factory is None:
            raise DatabaseError(
                "Database required for closing positions. Cannot proceed without database connection.",
                operation="close_position",
                table="positions",
                bot_name=bot_name,
                market_id=market_id
            )
        
        exit_price = validate_price(exit_price, "exit_price")
        
        result = None
        async with self.db.get_session() as session:
            try:
                async with session.begin():
                    from sqlalchemy import select
                    
                    from sqlalchemy import or_
                    pos_result = await session.execute(
                        select(Position).where(
                            or_(
                                Position.bot_id == bot_name,
                                Position.source_bot == bot_name,
                            ),
                            Position.market_id == market_id,
                            Position.status == "open"
                        ).limit(1).with_for_update()
                    )
                    position = pos_result.scalar_one_or_none()
                    
                    if not position:
                        return None
                    
                    entry_price = validate_price(position.entry_price, "entry_price")
                    size = validate_size(position.size, "size")
                    
                    # D8 FIX: P&L is (exit - entry) * size for BOTH YES and NO tokens.
                    # Both are bought assets — if you buy NO at 0.30 and it moves to 0.40, you profit.
                    # The old code negated P&L for NO, which treated NO wins as losses and
                    # corrupted daily/weekly loss tracking in risk_state.
                    pnl = validate_numeric((exit_price - entry_price) * size, "pnl")
                    
                    position.status = "closed"
                    position.current_price = exit_price
                    position.unrealized_pnl = pnl
                    result = {
                        "market_id": market_id,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "size": size,
                        "pnl": pnl
                    }
            except Exception as e:
                logger.error(f"Failed to close position: {str(e)}", exc_info=True)
                raise
        if result:
            await self._update_pnl(result["pnl"])
        return result

    async def _get_risk_state(self) -> Optional[Dict[str, Any]]:
        """Load risk_state singleton. Cached for 60s to avoid DB on every trade."""
        now = time.monotonic()
        if self._risk_state_cache is not None and now < self._risk_state_cache_until:
            return self._risk_state_cache
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text(
                    "SELECT daily_pnl, weekly_pnl, peak_balance, current_balance, "
                    "daily_reset_at, weekly_reset_at FROM risk_state WHERE id = 1"
                ))
                row = r.fetchone()
                if row:
                    state = {
                        "daily_pnl": float(row[0] or 0),
                        "weekly_pnl": float(row[1] or 0),
                        "peak_balance": row[2],
                        "current_balance": row[3],
                        "daily_reset_at": row[4],
                        "weekly_reset_at": row[5],
                    }
                    self._risk_state_cache = state
                    self._risk_state_cache_until = now + 60.0
                    return state
        except Exception as e:
            logger.debug("risk_state load skipped: %s", e)
        return None

    async def _update_pnl(self, pnl: float) -> None:
        """Update risk_state with realized PnL. Resets daily/weekly at boundaries."""
        try:
            from sqlalchemy import text
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            today_start = datetime.combine(now.date(), datetime.min.time())
            week_start = today_start - timedelta(days=now.weekday())
            async with self.db.get_session() as session:
                await session.execute(text("""
                    INSERT INTO risk_state (id, daily_pnl, weekly_pnl, daily_reset_at, weekly_reset_at, updated_at)
                    VALUES (1, :pnl, :pnl, :today, :week, :now)
                    ON CONFLICT (id) DO UPDATE SET
                        daily_pnl = CASE WHEN risk_state.daily_reset_at < :today THEN :pnl ELSE risk_state.daily_pnl + :pnl END,
                        weekly_pnl = CASE WHEN risk_state.weekly_reset_at < :week THEN :pnl ELSE risk_state.weekly_pnl + :pnl END,
                        daily_reset_at = CASE WHEN risk_state.daily_reset_at < :today THEN :today ELSE risk_state.daily_reset_at END,
                        weekly_reset_at = CASE WHEN risk_state.weekly_reset_at < :week THEN :week ELSE risk_state.weekly_reset_at END,
                        updated_at = :now
                """), {"pnl": pnl, "today": today_start, "week": week_start, "now": now})
                await session.commit()
        except Exception as e:
            logger.debug("risk_state update skipped: %s", e)

    # ── Oracle Manipulation Risk Factor (2026 roadmap) ──────────────────

    async def _check_oracle_manipulation_risk(
        self, market_id: str, position_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Check if a market is vulnerable to oracle manipulation.
        Cached 60s per market to avoid DB query on every trade.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return None

        # 60s cache per market
        now = time.monotonic()
        if not hasattr(self, "_oracle_risk_cache"):
            self._oracle_risk_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
        cached = self._oracle_risk_cache.get(market_id)
        if cached is not None:
            result, expiry = cached
            if now < expiry:
                return result

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT COALESCE(SUM(size * entry_price), 0) as open_interest,
                           COUNT(*) as position_count
                    FROM positions
                    WHERE market_id = :mid AND status = 'open'
                """), {"mid": market_id})
                row = r.fetchone()
                open_interest = float(row[0]) if row else 0
                position_count = int(row[1]) if row else 0

            uma_bond_cost = 5000.0
            manipulation_cost = uma_bond_cost

            if open_interest > 0:
                profit_from_manipulation = open_interest
                risk_ratio = profit_from_manipulation / manipulation_cost
                risk_score = min(1.0, risk_ratio / 10)
                is_risky = risk_score > 0.5
                result = {
                    "market_id": market_id,
                    "open_interest": open_interest,
                    "position_count": position_count,
                    "manipulation_cost": manipulation_cost,
                    "risk_score": risk_score,
                    "is_risky": is_risky,
                }
                self._oracle_risk_cache[market_id] = (result, now + 60.0)
                return result
            self._oracle_risk_cache[market_id] = (None, now + 60.0)
        except Exception as e:
            logger.debug("Oracle manipulation risk check failed: %s", e)
        return None
