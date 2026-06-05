"""
Intelligent Exit Strategy Engine — Session 45.

Computes dynamic, context-aware exit parameters by wiring existing infrastructure
(TCA, regime detection, volatility, time-to-resolution) into position_manager's
exit decisions.

Replaces fixed 30%/60% stop-loss/take-profit with dynamic thresholds that adapt
to market conditions. Prevents cost-blind exits that caused 0% sell win rate.

Key insight: exit when expected_remaining_alpha < transaction_cost.
Fixed thresholds can't capture this — need cost, volatility, TTR, and regime.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from structlog import get_logger

from bots.weather.engine.config.settings import settings

logger = get_logger()


@dataclass
class ExitParams:
    """Dynamic exit parameters for a single position."""

    stop_loss_pct: float = 0.30        # Dynamic stop-loss threshold
    take_profit_pct: float = 0.60      # Dynamic take-profit threshold
    breakeven_price: float = 0.0       # Price for active SELL to net-positive after costs
    entry_cost: float = 0.0            # Entry cost $ (slippage + fee)
    est_exit_cost: float = 0.0         # Estimated exit cost $
    min_exit_pnl: float = 0.0          # Minimum P&L for non-forced exit (cost floor)
    strong_reversal_threshold: float = 0.35  # Prob level below which reversal is forced
    hold_to_resolution: bool = False   # True if near resolution and in-the-money
    hours_to_resolution: Optional[float] = None  # Hours until market resolves


@dataclass
class _CacheEntry:
    """Per-market cached exit params to avoid recomputation every 10s cycle."""

    params: ExitParams
    computed_at: float  # time.monotonic()


class ExitStrategy:
    """
    Computes dynamic exit parameters by wiring existing infrastructure.

    Called by position_manager before every exit decision. Returns ExitParams
    with cost-aware, volatility-scaled, regime-adapted, TTR-decayed thresholds.

    Uses:
        - TransactionCostModel (base_engine/risk/transaction_cost.py) → breakeven
        - MarketRegimeDetector (base_engine/analysis/market_regime.py) → regime scaling
        - market_prices STDDEV query → volatility-based stops
        - markets.end_date_iso → time-to-resolution decay
    """

    # Cache TTL: recompute exit params at most every 5 minutes per market
    _CACHE_TTL_SECONDS = 300.0

    def __init__(
        self,
        db: Any,
        regime_detector: Optional[Any] = None,
        tcm: Optional[Any] = None,
    ):
        self.db = db
        self.regime_detector = regime_detector
        self.tcm = tcm
        self._cache: Dict[str, _CacheEntry] = {}
        # Lazy-import to avoid circular deps
        self._tcm_class = None
        self._regime_class = None

    def _ensure_tcm(self):
        """Lazy-init TransactionCostModel."""
        if self.tcm is None:
            try:
                from bots.weather.engine.base_engine.risk.transaction_cost import TransactionCostModel
                self.tcm = TransactionCostModel()
            except Exception:
                pass
        return self.tcm

    def _ensure_regime_detector(self):
        """Lazy-init MarketRegimeDetector."""
        if self.regime_detector is None:
            try:
                from bots.weather.engine.base_engine.analysis.market_regime import MarketRegimeDetector
                self.regime_detector = MarketRegimeDetector(db=self.db)
            except Exception:
                pass
        return self.regime_detector

    async def compute_exit_params(self, position: Any) -> ExitParams:
        """
        Compute dynamic exit parameters for a position.

        Checks cache first (5-min TTL). On cache miss, queries volatility,
        regime, and time-to-resolution, then combines with TCA for breakeven.

        Falls back to static defaults if any subsystem fails.
        """
        if not getattr(settings, "PM_COST_AWARE_EXITS", True):
            return self._static_defaults(position)

        market_id = str(getattr(position, "market_id", ""))
        token_id = str(getattr(position, "token_id", ""))
        cache_key = f"{market_id}:{token_id}"

        # Check cache
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and (now - cached.computed_at) < self._CACHE_TTL_SECONDS:
            # Update position-specific fields that change every cycle
            return self._update_position_fields(cached.params, position)

        # Compute fresh params
        params = ExitParams(
            stop_loss_pct=float(getattr(settings, "PM_BASE_STOP_LOSS_PCT", 0.30)),
            take_profit_pct=float(getattr(settings, "PM_BASE_TAKE_PROFIT_PCT", 0.60)),
        )

        # --- 1. TCA: breakeven + cost floor ---
        self._compute_costs(params, position)

        # --- 2. Volatility-scaled stops ---
        await self._apply_volatility_scaling(params, token_id, position)

        # --- 3. Time-to-resolution decay ---
        await self._apply_time_decay(params, market_id, position)

        # --- 4. Regime-adaptive targets ---
        await self._apply_regime_scaling(params, market_id)

        # Clamp final values to sane bounds
        params.stop_loss_pct = max(0.05, min(0.60, params.stop_loss_pct))
        params.take_profit_pct = max(0.10, min(1.50, params.take_profit_pct))
        params.strong_reversal_threshold = max(0.15, min(0.45, params.strong_reversal_threshold))

        # Cache
        self._cache[cache_key] = _CacheEntry(params=params, computed_at=now)

        # Prune stale cache entries (prevent unbounded growth)
        if len(self._cache) > 200:
            stale_keys = [
                k for k, v in self._cache.items()
                if now - v.computed_at > self._CACHE_TTL_SECONDS * 3
            ]
            for k in stale_keys:
                del self._cache[k]

        return self._update_position_fields(params, position)

    # ------------------------------------------------------------------
    # Internal: TCA breakeven
    # ------------------------------------------------------------------

    def _compute_costs(self, params: ExitParams, position: Any) -> None:
        """Compute breakeven price and cost floor from TransactionCostModel."""
        tcm = self._ensure_tcm()
        entry_price = float(getattr(position, "entry_price", None) or 0.5)
        current_price = float(getattr(position, "current_price", None) or entry_price)
        size = float(getattr(position, "size", None) or 0.0)

        if tcm and size > 0 and entry_price > 0:
            entry_notional = size * entry_price
            exit_notional = size * current_price
            entry_cost_est = tcm.estimate_cost(entry_notional)
            exit_cost_est = tcm.estimate_cost(exit_notional)
            params.entry_cost = entry_cost_est
            params.est_exit_cost = exit_cost_est
            # Breakeven: price at which selling covers all costs
            total_cost = entry_cost_est + exit_cost_est
            params.breakeven_price = entry_price + total_cost / size if size > 0 else entry_price
            # Cost floor: minimum expected P&L for voluntary exit
            # Don't exit if expected loss is less than total costs (it's cheaper to hold)
            params.min_exit_pnl = -total_cost
        else:
            # Fallback: use stored entry_cost from DB if available
            stored_cost = float(getattr(position, "entry_cost", None) or 0.0)
            stored_breakeven = float(getattr(position, "breakeven_price", None) or 0.0)
            params.entry_cost = stored_cost
            params.breakeven_price = stored_breakeven if stored_breakeven > 0 else entry_price * 1.04
            _cost_rate = (
                getattr(settings, "FIXED_SLIPPAGE_BPS", 50)
                + getattr(settings, "TAKER_FEE_BPS", 150)
            ) / 10000.0
            params.est_exit_cost = size * current_price * _cost_rate
            params.min_exit_pnl = -(params.entry_cost + params.est_exit_cost)

    # ------------------------------------------------------------------
    # Internal: Volatility-scaled stops
    # ------------------------------------------------------------------

    async def _apply_volatility_scaling(
        self, params: ExitParams, token_id: str, position: Any
    ) -> None:
        """
        Replace fixed % stops with ATR-equivalent scaled to prediction market bounds.

        Uses STDDEV of recent prices for the specific token_id.
        Applies 4*p*(1-p) boundary scaling (volatility peaks at 0.50, vanishes at 0/1).
        """
        if not self.db or not getattr(self.db, "session_factory", None) or not token_id:
            return

        try:
            from sqlalchemy import text as sa_text

            async with self.db.get_session() as session:
                result = await session.execute(
                    sa_text("""
                        SELECT STDDEV(price), COUNT(*), AVG(price)
                        FROM market_prices
                        WHERE token_id = :token_id
                          AND timestamp > NOW() - INTERVAL '7 days'
                    """),
                    {"token_id": token_id},
                )
                row = result.fetchone()

            if not row or row[0] is None or row[1] < 10:
                return  # Insufficient data

            raw_vol = float(row[0])
            data_points = int(row[1])
            avg_price = float(row[2]) if row[2] else 0.5

            if raw_vol <= 0 or avg_price <= 0:
                return

            # Boundary-aware volatility: peaks at p=0.50, vanishes at 0/1
            boundary_factor = 4.0 * avg_price * (1.0 - avg_price)
            vol_adjusted = raw_vol * boundary_factor

            # Dynamic stop-loss: multiplier × volatility / price
            vol_mult = float(getattr(settings, "PM_VOL_STOP_MULTIPLIER", 2.0))
            vol_stop = vol_mult * vol_adjusted / avg_price if avg_price > 0 else 0.30

            # Blend with base: don't go below minimum, don't go above maximum
            params.stop_loss_pct = max(0.10, min(0.50, vol_stop))

            # Take-profit scales proportionally (reward:risk ratio preserved)
            base_ratio = (
                float(getattr(settings, "PM_BASE_TAKE_PROFIT_PCT", 0.60))
                / float(getattr(settings, "PM_BASE_STOP_LOSS_PCT", 0.30))
            )
            params.take_profit_pct = params.stop_loss_pct * base_ratio

            logger.debug(
                "Vol-scaled exits: token=%s vol=%.4f boundary=%.3f stop=%.3f tp=%.3f (n=%d)",
                token_id[:12], raw_vol, boundary_factor,
                params.stop_loss_pct, params.take_profit_pct, data_points,
            )
        except Exception as e:
            logger.debug("Volatility scaling failed (using base): %s", e)

    # ------------------------------------------------------------------
    # Internal: Time-to-resolution decay
    # ------------------------------------------------------------------

    async def _apply_time_decay(
        self, params: ExitParams, market_id: str, position: Any
    ) -> None:
        """
        Adapt exit thresholds based on time until market resolves.

        Near resolution: widen stops (don't panic-sell before binary outcome),
        tighten take-profit (take any profit). Resolution is FREE on Polymarket
        (no exit cost) — selling near resolution wastes ~2% exit fee.
        """
        if not self.db or not getattr(self.db, "session_factory", None) or not market_id:
            return

        try:
            from sqlalchemy import text as sa_text

            async with self.db.get_session() as session:
                result = await session.execute(
                    sa_text("""
                        SELECT end_date_iso FROM markets
                        WHERE CAST(id AS TEXT) = :market_id
                           OR CAST(condition_id AS TEXT) = :market_id
                        LIMIT 1
                    """),
                    {"market_id": str(market_id)},
                )
                row = result.fetchone()

            if not row or not row[0]:
                return

            end_date = row[0]
            if hasattr(end_date, "tzinfo") and end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            elif not hasattr(end_date, "tzinfo"):
                return

            hours = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600.0
            params.hours_to_resolution = hours

            if hours <= 0:
                return  # Already resolved

            # Time-decay multipliers: (take_profit_mult, stop_loss_mult)
            if hours < 6:
                # Final stretch: binary outcome imminent
                # Tiny TP (grab any profit), wide SL (don't panic-sell)
                tp_mult, sl_mult = 0.15, 1.80
                params.strong_reversal_threshold = 0.20  # Only very strong conviction
            elif hours < 48:
                # Near resolution: consider holding to free settlement
                tp_mult, sl_mult = 0.40, 1.40
                params.strong_reversal_threshold = 0.25
            elif hours < 168:  # ~1 week
                tp_mult, sl_mult = 0.70, 1.10
            elif hours < 720:  # ~1 month
                tp_mult, sl_mult = 1.00, 1.00
            else:
                # Long-dated: very patient
                tp_mult, sl_mult = 1.20, 1.20

            params.stop_loss_pct *= sl_mult
            params.take_profit_pct *= tp_mult

            # Hold-to-resolution: if <48h and in-the-money, resolution is FREE
            near_resolution_hours = float(
                getattr(settings, "PM_NEAR_RESOLUTION_HOURS", 48.0)
            )
            if hours < near_resolution_hours:
                entry_price = float(getattr(position, "entry_price", None) or 0.5)
                current_price = float(getattr(position, "current_price", None) or entry_price)
                if current_price > entry_price:
                    params.hold_to_resolution = True

            logger.debug(
                "TTR decay: market=%s hours=%.1f tp_mult=%.2f sl_mult=%.2f hold=%s",
                str(market_id)[:12], hours, tp_mult, sl_mult, params.hold_to_resolution,
            )
        except Exception as e:
            logger.debug("Time-decay calculation failed (using base): %s", e)

    # ------------------------------------------------------------------
    # Internal: Regime-adaptive targets
    # ------------------------------------------------------------------

    async def _apply_regime_scaling(self, params: ExitParams, market_id: str) -> None:
        """
        Adapt exit thresholds based on market regime (trending, mean-reverting, etc.).

        Uses existing MarketRegimeDetector to classify the market, then scales:
        - Trending: let winners run (wider TP), tighter stop (trend broke = exit)
        - Mean-reverting: take profits fast (tighter TP), wider stop (bounces expected)
        - High volatility: wider stops (noise), slightly higher TP (bigger moves possible)
        """
        detector = self._ensure_regime_detector()
        if not detector or not market_id:
            return

        try:
            regime = await detector.detect_regime(str(market_id), lookback_days=14)
            regime_type = regime.get("regime", "unknown")
            confidence = float(regime.get("confidence", 0.0))

            if confidence < 0.3:
                return  # Regime unknown, keep base

            if regime_type == "trending":
                params.take_profit_pct *= 1.30   # Let winners run
                params.stop_loss_pct *= 0.80     # Tighter stop (trend broke)
            elif regime_type == "mean_reverting":
                params.take_profit_pct *= 0.60   # Take profits fast
                params.stop_loss_pct *= 1.30     # Wider stop (bounces)
            elif regime_type == "high_volatility":
                params.stop_loss_pct *= 1.50     # Wide stop (noise)
                params.take_profit_pct *= 1.20   # Bigger moves possible
            elif regime_type == "low_volatility":
                params.stop_loss_pct *= 0.70     # Tight stop (confident moves)
                params.take_profit_pct *= 0.80   # Smaller moves
            elif regime_type == "bear":
                # Bearish: tighter TP for YES, could be wider for NO (but we don't know side here)
                params.take_profit_pct *= 0.80
                params.stop_loss_pct *= 1.10
            elif regime_type == "bull":
                params.take_profit_pct *= 1.10
                params.stop_loss_pct *= 0.90

            logger.debug(
                "Regime scaling: market=%s regime=%s conf=%.2f sl=%.3f tp=%.3f",
                str(market_id)[:12], regime_type, confidence,
                params.stop_loss_pct, params.take_profit_pct,
            )
        except Exception as e:
            logger.debug("Regime scaling failed (using base): %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _static_defaults(self, position: Any) -> ExitParams:
        """Return static defaults when PM_COST_AWARE_EXITS is disabled."""
        entry_price = float(getattr(position, "entry_price", None) or 0.5)
        size = float(getattr(position, "size", None) or 0.0)
        _cost_rate = (
            getattr(settings, "FIXED_SLIPPAGE_BPS", 50)
            + getattr(settings, "TAKER_FEE_BPS", 150)
        ) / 10000.0
        return ExitParams(
            stop_loss_pct=float(getattr(settings, "PM_STOP_LOSS_PCT", 0.30)),
            take_profit_pct=float(getattr(settings, "PM_TAKE_PROFIT_PCT", 0.60)),
            breakeven_price=entry_price * (1.0 + 2 * _cost_rate),
            entry_cost=size * entry_price * _cost_rate,
            est_exit_cost=size * entry_price * _cost_rate,
            min_exit_pnl=-(2 * size * entry_price * _cost_rate),
            strong_reversal_threshold=float(
                getattr(settings, "PM_STRONG_REVERSAL_THRESHOLD", 0.35)
            ),
        )

    def _update_position_fields(self, params: ExitParams, position: Any) -> ExitParams:
        """Update position-specific cost fields that change every price tick."""
        current_price = float(getattr(position, "current_price", None) or 0.5)
        entry_price = float(getattr(position, "entry_price", None) or 0.5)
        size = float(getattr(position, "size", None) or 0.0)

        # Refresh exit cost estimate with current price
        tcm = self._ensure_tcm()
        if tcm and size > 0:
            params.est_exit_cost = tcm.estimate_cost(size * current_price)
            params.entry_cost = float(getattr(position, "entry_cost", None) or 0.0)
            if params.entry_cost <= 0:
                params.entry_cost = tcm.estimate_cost(size * entry_price)
            total_cost = params.entry_cost + params.est_exit_cost
            params.breakeven_price = entry_price + total_cost / size if size > 0 else entry_price
            params.min_exit_pnl = -total_cost

        # Check hold_to_resolution with latest price
        if params.hours_to_resolution is not None and params.hours_to_resolution < float(
            getattr(settings, "PM_NEAR_RESOLUTION_HOURS", 48.0)
        ):
            params.hold_to_resolution = current_price > entry_price

        return params
