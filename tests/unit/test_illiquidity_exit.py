"""S167: Tests for illiquidity exit trigger in position_manager.

The illiquidity exit uses a two-stage check:
1. Cached pre-filter: market_liquidity < multiplier × cost_basis
2. Live CLOB confirmation: liquidity_guardian.check_liquidity()

Conservative behavior: don't exit on incomplete data (timeout/error → hold).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace


def _make_position(market_id="mkt1", token_id="tok1", entry_price=0.5,
                   current_price=0.5, size=200.0, side="YES", pos_id=1):
    """Create a minimal position-like object for testing."""
    return SimpleNamespace(
        id=pos_id,
        market_id=market_id,
        token_id=token_id,
        entry_price=entry_price,
        current_price=current_price,
        size=size,
        side=side,
        bot_id="TestBot",
        bot_name="TestBot",
        opened_at=None,
    )


def _make_pm(liquidity_cache=None, illiquidity_enabled=True, multiplier=3.0,
             lg_result=None, lg_available=True):
    """Create a minimal AutomatedPositionManager-like object for testing."""
    from base_engine.execution.position_manager import AutomatedPositionManager

    pm = AutomatedPositionManager.__new__(AutomatedPositionManager)
    pm.db = MagicMock()
    pm.execution_engine = MagicMock()
    pm.order_manager = MagicMock()
    pm.prediction_engine = None
    pm.alerting = None
    pm.monitoring = False
    pm.exit_strategy = MagicMock()
    pm.exit_strategy.compute_exit_params = AsyncMock(
        return_value=SimpleNamespace(
            entry_cost=0.0, est_exit_cost=0.0,
            stop_loss_pct=0.30, take_profit_pct=0.60,
            hold_to_resolution=False, strong_reversal_threshold=0.35,
            breakeven_price=0.5, min_exit_pnl=-10.0,
            hours_to_resolution=None,
        )
    )
    pm._exit_cooldowns = {}
    pm._market_exit_mult = {}
    pm._last_learning_refresh = 0.0
    pm._learning_refresh_interval = 1800
    pm._market_liquidity_cache = liquidity_cache or {}
    pm._api_price_cache = {}
    pm.default_stop_loss_pct = 0.30
    pm.default_take_profit_pct = 0.60
    pm.risk_manager = None

    # Mock order_gateway with liquidity_guardian
    if lg_available:
        lg = MagicMock()
        lg.check_liquidity = AsyncMock(return_value=lg_result or {
            "can_execute": False, "recommendation": "abort",
        })
        og = MagicMock()
        og.liquidity_guardian = lg
        pm.order_gateway = og
    else:
        pm.order_gateway = None

    # Mock _execute_exit
    pm._execute_exit = AsyncMock()

    return pm


class TestIlliquidityPreFilter:
    """Test the cached pre-filter stage (multiplier × cost_basis)."""

    @pytest.mark.asyncio
    async def test_triggers_when_liquidity_below_threshold(self):
        """$200 liquidity < 3.0 × $100 cost_basis → should trigger."""
        pm = _make_pm(
            liquidity_cache={"mkt1": 200.0},
            multiplier=3.0,
        )
        pos = _make_position(entry_price=0.5, size=200.0)  # cost_basis = $100

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = True
            mock_settings.ILLIQUIDITY_EXIT_MULTIPLIER = 3.0
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        pm._execute_exit.assert_called_once()
        call_args = pm._execute_exit.call_args
        assert "illiquidity_exit" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_no_trigger_when_liquidity_sufficient(self):
        """$500 liquidity > 3.0 × $100 cost_basis → should NOT trigger."""
        pm = _make_pm(
            liquidity_cache={"mkt1": 500.0},
            multiplier=3.0,
        )
        pos = _make_position(entry_price=0.5, size=200.0)  # cost_basis = $100

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = True
            mock_settings.ILLIQUIDITY_EXIT_MULTIPLIER = 3.0
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        # _execute_exit should NOT have been called for illiquidity
        for call in pm._execute_exit.call_args_list:
            assert "illiquidity" not in str(call)

    @pytest.mark.asyncio
    async def test_no_trigger_when_disabled(self):
        """ILLIQUIDITY_EXIT_ENABLED=false → never triggers."""
        pm = _make_pm(liquidity_cache={"mkt1": 1.0})  # extremely low
        pos = _make_position(entry_price=0.5, size=200.0)

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = False
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        for call in pm._execute_exit.call_args_list:
            assert "illiquidity" not in str(call)

    @pytest.mark.asyncio
    async def test_no_trigger_when_market_not_in_cache(self):
        """Market not in liquidity cache → skip (no data, don't exit)."""
        pm = _make_pm(liquidity_cache={})  # empty cache
        pos = _make_position(entry_price=0.5, size=200.0)

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = True
            mock_settings.ILLIQUIDITY_EXIT_MULTIPLIER = 3.0
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        for call in pm._execute_exit.call_args_list:
            assert "illiquidity" not in str(call)


class TestIlliquidityCLOBConfirmation:
    """Test the CLOB confirmation stage (stage 2)."""

    @pytest.mark.asyncio
    async def test_clob_overrides_prefilter_when_liquid(self):
        """Pre-filter triggers but CLOB says liquid → don't exit."""
        pm = _make_pm(
            liquidity_cache={"mkt1": 50.0},  # pre-filter triggers (50 < 3×100)
            lg_result={"can_execute": True, "recommendation": "proceed"},
        )
        pos = _make_position(entry_price=0.5, size=200.0)

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = True
            mock_settings.ILLIQUIDITY_EXIT_MULTIPLIER = 3.0
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        for call in pm._execute_exit.call_args_list:
            assert "illiquidity" not in str(call)

    @pytest.mark.asyncio
    async def test_clob_timeout_conservative_hold(self):
        """CLOB check times out → conservative, don't exit."""
        pm = _make_pm(
            liquidity_cache={"mkt1": 50.0},
        )
        # Make CLOB check raise TimeoutError
        pm.order_gateway.liquidity_guardian.check_liquidity = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        pos = _make_position(entry_price=0.5, size=200.0)

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = True
            mock_settings.ILLIQUIDITY_EXIT_MULTIPLIER = 3.0
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        for call in pm._execute_exit.call_args_list:
            assert "illiquidity" not in str(call)

    @pytest.mark.asyncio
    async def test_no_lg_trusts_prefilter(self):
        """No liquidity_guardian available → trust cached pre-filter, exit."""
        pm = _make_pm(
            liquidity_cache={"mkt1": 50.0},
            lg_available=False,  # no order_gateway
        )
        pos = _make_position(entry_price=0.5, size=200.0)

        with patch("base_engine.execution.position_manager.settings") as mock_settings:
            mock_settings.ILLIQUIDITY_EXIT_ENABLED = True
            mock_settings.ILLIQUIDITY_EXIT_MULTIPLIER = 3.0
            mock_settings.PM_EXCLUDE_BOTS = []
            mock_settings.PM_STOP_LOSS_PCT = 0.30
            mock_settings.PM_TAKE_PROFIT_PCT = 0.60
            mock_settings.MODEL_REVERSAL_THRESHOLD = 0.45
            mock_settings.PM_COST_AWARE_EXITS = True
            await pm._check_position(pos)

        pm._execute_exit.assert_called_once()
        assert "illiquidity_exit" in pm._execute_exit.call_args[0][1]
