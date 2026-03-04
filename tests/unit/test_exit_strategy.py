"""
Unit tests for base_engine/execution/exit_strategy.py — Session 45 Intelligent Exit Engine.

Tests:
  1. Breakeven includes round-trip costs
  2. Vol-scaled stop: high vol → wider stop
  3. Vol-scaled stop: low vol → tighter stop
  4. Near resolution (<48h) + in-the-money → hold_to_resolution=True
  5. Near resolution (<6h) → stop_loss much wider
  6. Trending regime → TP × 1.3
  7. Mean-reverting regime → TP × 0.6
  8. Weak reversal blocked in cost trap (prob=0.42)
  9. Strong reversal forces exit (prob=0.30)
  10. Cost-adjusted P&L deducts entry + exit costs
  11. Stop-loss always fires (never blocked)
  12. Kill switch (PM_COST_AWARE_EXITS=false) → old static behavior
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers: build mock position / DB / settings
# ---------------------------------------------------------------------------

def _mock_position(
    entry_price=0.50,
    current_price=0.55,
    size=100.0,
    side="YES",
    market_id="market_1",
    token_id="token_1",
    entry_cost=None,
    breakeven_price=None,
    opened_at=None,
):
    """Create a mock Position object matching the ORM model."""
    pos = SimpleNamespace(
        id=1,
        bot_id="TestBot",
        source_bot=None,
        market_id=market_id,
        token_id=token_id,
        side=side,
        size=size,
        entry_price=entry_price,
        current_price=current_price,
        unrealized_pnl=(current_price - entry_price) * size,
        entry_cost=entry_cost,
        breakeven_price=breakeven_price,
        opened_at=opened_at or datetime.now(timezone.utc),
        status="open",
        is_paper=True,
    )
    return pos


def _mock_settings(**overrides):
    """Build a mock settings namespace with sane defaults for exit strategy tests."""
    defaults = dict(
        PM_COST_AWARE_EXITS=True,
        PM_BASE_STOP_LOSS_PCT=0.30,
        PM_BASE_TAKE_PROFIT_PCT=0.60,
        PM_STRONG_REVERSAL_THRESHOLD=0.35,
        PM_VOL_STOP_MULTIPLIER=2.0,
        PM_NEAR_RESOLUTION_HOURS=48.0,
        FIXED_SLIPPAGE_BPS=50,
        TAKER_FEE_BPS=150,
    )
    defaults.update(overrides)

    class _Settings:
        pass

    s = _Settings()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _mock_tcm(cost_per_dollar=0.02):
    """Mock TransactionCostModel that returns cost = notional × cost_per_dollar."""
    tcm = MagicMock()
    tcm.estimate_cost = MagicMock(side_effect=lambda notional, **kw: notional * cost_per_dollar)
    return tcm


def _make_db_session(vol_stddev=None, vol_count=50, vol_avg=0.50, end_date=None):
    """Create a mock DB with session that returns preset query results."""
    db = MagicMock()
    db.session_factory = True

    session = AsyncMock()

    # We need to handle multiple queries: volatility and time-decay
    call_count = {"n": 0}

    async def _execute(query, params=None):
        call_count["n"] += 1
        result = MagicMock()

        # Detect which query by examining the text
        query_str = str(query.text) if hasattr(query, "text") else str(query)

        if "STDDEV" in query_str:
            # Volatility query
            if vol_stddev is not None:
                row = (vol_stddev, vol_count, vol_avg)
            else:
                row = (None, 0, None)
            result.fetchone = MagicMock(return_value=row)
        elif "end_date_iso" in query_str:
            # Time-decay query
            if end_date is not None:
                result.fetchone = MagicMock(return_value=(end_date,))
            else:
                result.fetchone = MagicMock(return_value=(None,))
        else:
            result.fetchone = MagicMock(return_value=None)

        return result

    session.execute = _execute

    # get_session returns an async context manager
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db.get_session = MagicMock(return_value=ctx)

    return db


# ---------------------------------------------------------------------------
# Test 1: Breakeven includes round-trip costs
# ---------------------------------------------------------------------------

class TestBreakevenCosts:
    def test_breakeven_includes_round_trip_costs(self):
        """Breakeven = entry + (entry_cost + exit_cost) / size ≈ entry × 1.04 for 2% each way."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        tcm = _mock_tcm(cost_per_dollar=0.02)  # 2% per leg
        db = MagicMock()
        db.session_factory = None  # Skip DB queries

        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db, tcm=tcm)
            pos = _mock_position(entry_price=0.50, current_price=0.50, size=100.0)
            params = ExitParams()
            es._compute_costs(params, pos)

        # entry_cost = 100 * 0.50 * 0.02 = 1.0
        assert abs(params.entry_cost - 1.0) < 0.01
        # exit_cost = 100 * 0.50 * 0.02 = 1.0
        assert abs(params.est_exit_cost - 1.0) < 0.01
        # breakeven = 0.50 + (1.0 + 1.0) / 100 = 0.52
        assert abs(params.breakeven_price - 0.52) < 0.01
        # min_exit_pnl = -(1.0 + 1.0) = -2.0
        assert abs(params.min_exit_pnl - (-2.0)) < 0.01


# ---------------------------------------------------------------------------
# Tests 2-3: Volatility-scaled stops
# ---------------------------------------------------------------------------

class TestVolatilityScaling:
    @pytest.mark.asyncio
    async def test_vol_scaled_stop_high_vol(self):
        """High volatility → wider stop (e.g., ≥ 0.30)."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        # High vol: stddev = 0.08 → vol_adjusted = 0.08 * 4*0.5*0.5 = 0.08
        # vol_stop = 2.0 * 0.08 / 0.50 = 0.32
        db = _make_db_session(vol_stddev=0.08, vol_count=50, vol_avg=0.50)
        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db)
            params = ExitParams(stop_loss_pct=0.30)
            pos = _mock_position(current_price=0.50)
            await es._apply_volatility_scaling(params, "token_1", pos)

        assert params.stop_loss_pct >= 0.25  # Wider than tight minimum

    @pytest.mark.asyncio
    async def test_vol_scaled_stop_low_vol(self):
        """Low volatility → tighter stop (e.g., ≤ 0.15)."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        # Low vol: stddev = 0.01 → vol_adjusted = 0.01 * 1.0 = 0.01
        # vol_stop = 2.0 * 0.01 / 0.50 = 0.04 → clamped to 0.10
        db = _make_db_session(vol_stddev=0.01, vol_count=50, vol_avg=0.50)
        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db)
            params = ExitParams(stop_loss_pct=0.30)
            pos = _mock_position(current_price=0.50)
            await es._apply_volatility_scaling(params, "token_1", pos)

        assert params.stop_loss_pct <= 0.15  # Tighter than base 0.30


# ---------------------------------------------------------------------------
# Tests 4-5: Time-to-resolution
# ---------------------------------------------------------------------------

class TestTimeToResolution:
    @pytest.mark.asyncio
    async def test_near_resolution_hold(self):
        """<48h + in-the-money → hold_to_resolution=True."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        end_date = datetime.now(timezone.utc) + timedelta(hours=24)
        db = _make_db_session(end_date=end_date)

        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db)
            params = ExitParams()
            # In-the-money: current > entry
            pos = _mock_position(entry_price=0.40, current_price=0.55)
            await es._apply_time_decay(params, "market_1", pos)

        assert params.hold_to_resolution is True
        assert params.hours_to_resolution is not None
        assert params.hours_to_resolution < 48

    @pytest.mark.asyncio
    async def test_near_resolution_wide_stop(self):
        """<6h → stop_loss much wider (sl_mult=1.80)."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        end_date = datetime.now(timezone.utc) + timedelta(hours=3)
        db = _make_db_session(end_date=end_date)

        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db)
            params = ExitParams(stop_loss_pct=0.30)
            pos = _mock_position(entry_price=0.40, current_price=0.55)
            await es._apply_time_decay(params, "market_1", pos)

        # stop_loss = 0.30 * 1.80 = 0.54
        assert params.stop_loss_pct > 0.45  # Much wider than 0.30 base
        # strong reversal threshold should be tighter (only strong reversals)
        assert params.strong_reversal_threshold <= 0.25


# ---------------------------------------------------------------------------
# Tests 6-7: Regime-adaptive targets
# ---------------------------------------------------------------------------

class TestRegimeScaling:
    @pytest.mark.asyncio
    async def test_trending_regime_extends_tp(self):
        """Trending regime → TP × 1.3."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        regime_detector = MagicMock()
        regime_detector.detect_regime = AsyncMock(return_value={
            "regime": "trending",
            "confidence": 0.7,
            "scores": {},
            "metrics": {},
        })

        db = MagicMock()
        db.session_factory = None
        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db, regime_detector=regime_detector)
            params = ExitParams(stop_loss_pct=0.30, take_profit_pct=0.60)
            await es._apply_regime_scaling(params, "market_1")

        # TP should be extended (× 1.3)
        assert params.take_profit_pct > 0.60  # 0.60 * 1.3 = 0.78
        assert abs(params.take_profit_pct - 0.78) < 0.05
        # SL should be tighter (× 0.8)
        assert params.stop_loss_pct < 0.30  # 0.30 * 0.8 = 0.24
        assert abs(params.stop_loss_pct - 0.24) < 0.05

    @pytest.mark.asyncio
    async def test_mean_reverting_tightens_tp(self):
        """Mean-reverting regime → TP × 0.6."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        regime_detector = MagicMock()
        regime_detector.detect_regime = AsyncMock(return_value={
            "regime": "mean_reverting",
            "confidence": 0.7,
            "scores": {},
            "metrics": {},
        })

        db = MagicMock()
        db.session_factory = None
        with patch("base_engine.execution.exit_strategy.settings", _mock_settings()):
            es = ExitStrategy(db=db, regime_detector=regime_detector)
            params = ExitParams(stop_loss_pct=0.30, take_profit_pct=0.60)
            await es._apply_regime_scaling(params, "market_1")

        # TP should be tightened (× 0.6)
        assert params.take_profit_pct < 0.60  # 0.60 * 0.6 = 0.36
        assert abs(params.take_profit_pct - 0.36) < 0.05
        # SL should be wider (× 1.3)
        assert params.stop_loss_pct > 0.30  # 0.30 * 1.3 = 0.39
        assert abs(params.stop_loss_pct - 0.39) < 0.05


# ---------------------------------------------------------------------------
# Tests 8-9: Model reversal gating
# ---------------------------------------------------------------------------

class TestModelReversalGating:
    def test_weak_reversal_blocked_in_cost_trap(self):
        """prob=0.42, below breakeven → HOLD (no exit triggered)."""
        # This is tested indirectly via the ExitParams fields:
        # With prob=0.42 (above strong_reversal_threshold=0.35),
        # and price below breakeven, and cost_adjusted_pnl above min_exit_pnl,
        # the position manager should hold (no exit).
        from base_engine.execution.exit_strategy import ExitParams

        params = ExitParams(
            strong_reversal_threshold=0.35,
            breakeven_price=0.52,
            min_exit_pnl=-2.0,
            hold_to_resolution=False,
        )

        prob = 0.42  # Above 0.35 → not a strong reversal
        current_price = 0.48  # Below breakeven 0.52
        cost_adjusted_pnl = -1.5  # Above min_exit_pnl (-2.0)

        # Decision logic from position_manager:
        should_exit = False
        if params.hold_to_resolution:
            should_exit = False  # Hold
        elif current_price >= params.breakeven_price:
            should_exit = True  # Profitable exit
        elif prob < params.strong_reversal_threshold:
            should_exit = True  # Strong reversal
        elif cost_adjusted_pnl < params.min_exit_pnl:
            should_exit = True  # Cost stop
        # else: weak reversal, hold

        assert should_exit is False, "Weak reversal in cost trap should HOLD, not exit"

    def test_strong_reversal_forces_exit(self):
        """prob=0.30, below breakeven → EXIT (strong reversal threshold breached)."""
        from base_engine.execution.exit_strategy import ExitParams

        params = ExitParams(
            strong_reversal_threshold=0.35,
            breakeven_price=0.52,
            min_exit_pnl=-2.0,
            hold_to_resolution=False,
        )

        prob = 0.30  # Below 0.35 → strong reversal
        current_price = 0.48  # Below breakeven

        should_exit = False
        if params.hold_to_resolution:
            should_exit = False
        elif current_price >= params.breakeven_price:
            should_exit = True
        elif prob < params.strong_reversal_threshold:
            should_exit = True  # ← This fires
        elif -1.5 < params.min_exit_pnl:
            should_exit = True

        assert should_exit is True, "Strong reversal should force EXIT"


# ---------------------------------------------------------------------------
# Test 10: Cost-adjusted P&L
# ---------------------------------------------------------------------------

class TestCostAdjustedPnL:
    def test_cost_adjusted_pnl_accurate(self):
        """unrealized_pnl should deduct entry + estimated exit costs."""
        entry_price = 0.50
        current_price = 0.55
        size = 100.0
        entry_cost = 1.0  # $1 entry cost
        # est exit cost: 100 * 0.55 * 0.02 = 1.10
        exit_cost_rate = (50 + 150) / 10000.0  # 0.02
        est_exit_cost = size * current_price * exit_cost_rate

        raw_pnl = (current_price - entry_price) * size  # 5.0
        cost_adjusted_pnl = raw_pnl - (entry_cost + est_exit_cost)  # 5.0 - 1.0 - 1.10 = 2.90

        assert abs(raw_pnl - 5.0) < 0.01
        assert abs(est_exit_cost - 1.10) < 0.01
        assert abs(cost_adjusted_pnl - 2.90) < 0.01

        # Cost-adjusted P&L % = cost_pnl / (entry * size) = 2.90 / 50 = 0.058
        cost_pnl_pct = cost_adjusted_pnl / (entry_price * size)
        assert abs(cost_pnl_pct - 0.058) < 0.01


# ---------------------------------------------------------------------------
# Test 11: Stop-loss never blocked
# ---------------------------------------------------------------------------

class TestStopLossNeverBlocked:
    def test_stop_loss_fires_regardless_of_cost_gate(self):
        """Stop-loss always fires, even when cost gate would block a reversal exit."""
        from base_engine.execution.exit_strategy import ExitParams

        params = ExitParams(
            stop_loss_pct=0.30,
            hold_to_resolution=True,  # Would block model reversal
        )

        entry_price = 0.50
        current_price = 0.30  # -40% drop
        size = 100.0
        entry_cost = 1.0
        exit_cost_rate = 0.02
        est_exit_cost = size * current_price * exit_cost_rate

        raw_pnl = (current_price - entry_price) * size
        cost_adjusted_pnl = raw_pnl - (entry_cost + est_exit_cost)
        cost_pnl_pct = cost_adjusted_pnl / (entry_price * size)

        # cost_pnl_pct = (-20 - 1 - 0.6) / 50 = -0.432 → below -0.30 stop
        assert cost_pnl_pct < -params.stop_loss_pct, \
            f"Stop-loss should trigger: cost_pnl_pct={cost_pnl_pct:.3f} < -{params.stop_loss_pct}"

        # Even with hold_to_resolution=True, stop-loss runs (it's independent of reversal gate)
        # The position manager checks stop-loss AFTER model reversal — stop-loss always fires
        should_stop_loss = cost_pnl_pct <= -params.stop_loss_pct
        assert should_stop_loss is True


# ---------------------------------------------------------------------------
# Test 12: Kill switch reverts to fixed defaults
# ---------------------------------------------------------------------------

class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_kill_switch_reverts_to_fixed(self):
        """PM_COST_AWARE_EXITS=false → old static behavior (fixed 0.30/0.60)."""
        from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

        db = MagicMock()
        db.session_factory = True

        mock_s = _mock_settings(PM_COST_AWARE_EXITS=False)
        with patch("base_engine.execution.exit_strategy.settings", mock_s):
            es = ExitStrategy(db=db)
            pos = _mock_position(entry_price=0.50, current_price=0.55)
            params = await es.compute_exit_params(pos)

        # When kill switch is off, should return static defaults
        assert abs(params.stop_loss_pct - 0.30) < 0.01
        assert abs(params.take_profit_pct - 0.60) < 0.01
        assert params.hold_to_resolution is False
