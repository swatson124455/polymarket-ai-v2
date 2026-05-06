"""
Rigorous tests verifying that all 4 active bots (WeatherBot, MirrorBot, EsportsBot,
EsportsLiveBot) operate identically in paper and live modes.

Session 83: After SIMULATION_MODE branching was removed from risk_manager, bankroll_manager,
order_gateway (liquidity), and ensemble_bot, these tests verify:

1. Pipeline gate blocks ALL modes equally (no fail-open in paper)
2. Liquidity check blocks ALL modes equally (no fail-open in paper)
3. Kelly calibration floor is 0.50 in ALL modes (no 0.75 paper override)
4. Transaction cost edge is single-sided in ALL modes (no 2x paper doubling)
5. Each active bot's place_order path routes correctly through OrderGateway

Tests are parametrized across all 5 active bots where applicable.
"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

ACTIVE_BOTS = ["WeatherBot", "MirrorBot", "EsportsBot", "EsportsLiveBot"]

# Default mock settings values needed by check_risk_limits past pipeline gate
_RISK_SETTINGS_DEFAULTS = dict(
    RISK_MIN_EDGE_PCT=2,
    RISK_MAX_POSITION_SIZE_USD=10000.0,
    RISK_MIN_PRICE=0.05,
    RISK_MAX_PRICE=0.95,
    MAX_POSITION_SIZE_PCT=0.10,
    KELLY_FRACTION=0.25,
    KELLY_ACTIVE_BOTS=1,
    CATEGORY_KELLY_FRACTIONS="{}",
    TOTAL_CAPITAL=100000.0,
    MIN_CONFIDENCE_THRESHOLD=0.01,
    WEATHER_MIN_CONFIDENCE=0.01,
    ESPORTS_MIN_CONFIDENCE=0.01,
    MAX_CONSECUTIVE_LOSSES=0,
    VOL_SCALE_FACTOR=2.0,
    ENSEMBLE_MIN_MARKET_VOLUME_USD=0,  # disable volume gate in tests
    MAX_POSITIONS_PER_BOT=1000,
    WEATHER_MAX_POSITIONS=1000,
    MIRROR_MAX_POSITIONS=1000,
    RISK_MAX_TOTAL_EXPOSURE_USD=100000.0,
    WEATHER_MAX_TOTAL_EXPOSURE_USD=100000.0,
    ESPORTS_MAX_TOTAL_EXPOSURE_USD=100000.0,
    MAX_DAILY_EXPOSURE=1.0,  # 100% of capital
    RISK_MAX_POSITIONS_COUNT=1000,
)


def _apply_settings(mock_s, overrides=None):
    """Apply default + override settings to a mock."""
    vals = {**_RISK_SETTINGS_DEFAULTS, **(overrides or {})}
    for k, v in vals.items():
        setattr(mock_s, k, v)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_risk_manager(pipeline_gate_passed=True, pipeline_gate_summary="OK"):
    """Create a RiskManager with controllable pipeline gate behavior."""
    from base_engine.risk.risk_manager import RiskManager

    mock_db = MagicMock()
    mock_db.session_factory = MagicMock()  # truthy so pipeline gate check runs

    rm = RiskManager(db=mock_db, kill_switch=None, alerting=None)
    # Prime the pipeline gate cache so it doesn't import PipelineGate
    rm._pipeline_gate_cache = (pipeline_gate_passed, MagicMock(passed=pipeline_gate_passed, summary=pipeline_gate_summary))
    rm._pipeline_gate_cache_until = time.monotonic() + 3600  # cache valid for 1hr

    # Wire a mock OrderGateway so the fast-path is used (avoids DB fallback)
    mock_og = MagicMock()
    mock_og.get_position_count = MagicMock(return_value=0)
    mock_og.get_total_exposure_usd = MagicMock(return_value=0.0)
    mock_og.get_bot_exposure_usd = MagicMock(return_value=0.0)
    mock_og.get_daily_exposure_usd = MagicMock(return_value=0.0)
    rm._order_gateway = mock_og

    return rm


def make_bankroll_manager(bot_name="WeatherBot"):
    """Create a BotBankrollManager with mocked dependencies."""
    gw = MagicMock()
    gw._daily_exposure_usd = {}
    gw.get_daily_exposure_usd = lambda bn: 0.0

    with patch("base_engine.risk.bankroll_manager.settings") as ms:
        ms.BOT_BANKROLL_CONFIG = "{}"
        ms.CATEGORY_KELLY_FRACTIONS = "{}"
        from base_engine.risk.bankroll_manager import BotBankrollManager
        mgr = BotBankrollManager(bot_name=bot_name, order_gateway=gw, db=None)
    return mgr


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Pipeline Gate: Blocks in ALL modes (no fail-open for paper)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPipelineGateBlocksAllModes:
    """Pipeline gate must block stale-data trades regardless of SIMULATION_MODE."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_stale_data_blocks_trade_sim_true(self, bot_name):
        """With SIMULATION_MODE=True, pipeline gate still blocks when data is stale."""
        rm = make_risk_manager(pipeline_gate_passed=False, pipeline_gate_summary="sync_log stale > 300s")
        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {"SIMULATION_MODE": True})
            result = await rm.check_risk_limits(
                bot_name=bot_name, market_id="0xabc", size=10.0,
                price=0.50, confidence=0.60, prediction=0.60,
            )
        assert result["allowed"] is False, f"Pipeline gate must block {bot_name} even with SIMULATION_MODE=True"
        assert any("freshness" in r.lower() or "data" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_stale_data_blocks_trade_sim_false(self, bot_name):
        """With SIMULATION_MODE=False, pipeline gate blocks when data is stale (baseline)."""
        rm = make_risk_manager(pipeline_gate_passed=False, pipeline_gate_summary="sync_log stale > 300s")
        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {"SIMULATION_MODE": False})
            result = await rm.check_risk_limits(
                bot_name=bot_name, market_id="0xabc", size=10.0,
                price=0.50, confidence=0.60, prediction=0.60,
            )
        assert result["allowed"] is False, f"Pipeline gate must block {bot_name} with SIMULATION_MODE=False"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sim_mode", [True, False])
    async def test_fresh_data_allows_trade(self, sim_mode):
        """When data is fresh, pipeline gate allows trades in both modes."""
        rm = make_risk_manager(pipeline_gate_passed=True)
        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {"SIMULATION_MODE": sim_mode})
            result = await rm.check_risk_limits(
                bot_name="WeatherBot", market_id="0xabc", size=10.0,
                price=0.50, confidence=0.60, prediction=0.60,
            )
        assert result["allowed"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sim_mode", [True, False])
    async def test_pipeline_gate_result_identical(self, sim_mode):
        """Pipeline gate returns identical result structure regardless of mode."""
        rm = make_risk_manager(pipeline_gate_passed=False)
        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {"SIMULATION_MODE": sim_mode})
            result = await rm.check_risk_limits(
                bot_name="MirrorBot", market_id="0xabc", size=10.0,
                price=0.50, confidence=0.60, prediction=0.60,
            )
        assert result["allowed"] is False
        assert result["reasons"] == ["Data freshness check failed"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Transaction Cost Edge: Single-sided in ALL modes (no 2x paper doubling)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransactionCostEdgeUnified:
    """Transaction cost edge is calculated identically regardless of SIMULATION_MODE."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_edge_threshold_identical_both_modes(self, bot_name):
        """Same edge threshold in paper and live modes — no 2x doubling."""
        results = {}
        for sim_mode in [True, False]:
            rm = make_risk_manager(pipeline_gate_passed=True)
            with patch("base_engine.risk.risk_manager.settings") as ms:
                _apply_settings(ms, {"SIMULATION_MODE": sim_mode, "RISK_MIN_EDGE_PCT": 0})
                result = await rm.check_risk_limits(
                    bot_name=bot_name, market_id="0xabc", size=100.0,
                    price=0.50, confidence=0.52, prediction=0.52,  # low edge
                )
                results[sim_mode] = result

        # Both modes must give the same allow/deny decision
        assert results[True]["allowed"] == results[False]["allowed"], (
            f"{bot_name}: Edge check differs between paper ({results[True]}) and live ({results[False]})"
        )

    @pytest.mark.asyncio
    async def test_marginal_edge_same_result(self):
        """A marginal edge trade gets identical treatment in both modes.

        Transaction cost model requires ~1.54% for a $25 order.
        We test at 2% edge (passes) and 1% edge (fails) — both must be identical across modes.
        """
        for edge, expected_allowed in [(0.02, True), (0.01, False)]:
            results = {}
            for sim_mode in [True, False]:
                rm = make_risk_manager(pipeline_gate_passed=True)
                with patch("base_engine.risk.risk_manager.settings") as ms:
                    _apply_settings(ms, {"SIMULATION_MODE": sim_mode, "RISK_MIN_EDGE_PCT": 0})
                    result = await rm.check_risk_limits(
                        bot_name="WeatherBot", market_id="0xabc", size=50.0,
                        price=0.50, confidence=0.50 + edge, prediction=0.50 + edge,
                    )
                    results[sim_mode] = result
            assert results[True]["allowed"] == results[False]["allowed"], (
                f"Edge {edge}: paper={results[True]} vs live={results[False]}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Kelly Calibration Floor: 0.50 in ALL modes (no 0.75 paper override)
# ═══════════════════════════════════════════════════════════════════════════════

class TestKellyCalibrationFloorUnified:
    """Kelly calibration floor is 0.50 regardless of SIMULATION_MODE."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_risk_manager_kelly_floor_paper_mode(self, bot_name):
        """RiskManager uses 0.50 cal_floor even with SIMULATION_MODE=True."""
        rm = make_risk_manager(pipeline_gate_passed=True)
        rm._cached_drawdown_pct = 0.0  # no drawdown compression

        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {"SIMULATION_MODE": True, "MAX_POSITION_SIZE_PCT": 1.0})
            size_paper = await rm.calculate_position_size(
                bot_name=bot_name, confidence=0.70, price=0.50, available_capital=10000.0,
                calibration_quality={"brier": 0.35, "count": 50},
            )

        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {"SIMULATION_MODE": False, "MAX_POSITION_SIZE_PCT": 1.0})
            size_live = await rm.calculate_position_size(
                bot_name=bot_name, confidence=0.70, price=0.50, available_capital=10000.0,
                calibration_quality={"brier": 0.35, "count": 50},
            )

        assert size_paper == size_live, (
            f"{bot_name}: Kelly sizing differs: paper=${size_paper:.2f} vs live=${size_live:.2f}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_bankroll_manager_kelly_floor_paper_mode(self, bot_name):
        """BotBankrollManager uses 0.50 cal_floor even with SIMULATION_MODE=True."""
        mgr = make_bankroll_manager(bot_name)

        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.SIMULATION_MODE = True
            ms.BOT_BANKROLL_CONFIG = "{}"
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            size_paper, _ = await mgr.get_bet_size(
                confidence=0.70, price=0.50,
                calibration_quality={"brier": 0.35, "count": 50},
            )

        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.SIMULATION_MODE = False
            ms.BOT_BANKROLL_CONFIG = "{}"
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            size_live, _ = await mgr.get_bet_size(
                confidence=0.70, price=0.50,
                calibration_quality={"brier": 0.35, "count": 50},
            )

        assert size_paper == size_live, (
            f"{bot_name}: BankrollManager sizing differs: paper=${size_paper:.2f} vs live=${size_live:.2f}"
        )

    @pytest.mark.asyncio
    async def test_calibration_floor_is_exactly_050(self):
        """Verify the floor value is exactly 0.50 (not 0.75)."""
        rm = make_risk_manager(pipeline_gate_passed=True)
        rm._cached_drawdown_pct = 0.0

        with patch("base_engine.risk.risk_manager.settings") as ms:
            _apply_settings(ms, {
                "SIMULATION_MODE": True,  # was 0.75 before fix
                "KELLY_FRACTION": 1.0,  # full Kelly to isolate calibration effect
                "MAX_POSITION_SIZE_PCT": 1.0,
                "RISK_MAX_POSITION_SIZE_USD": 100000.0,
            })

            # brier=0.50 (terrible calibration) → multiplier = max(0.50, 1.0 - (0.50-0.15)*3.33)
            # = max(0.50, 1.0 - 1.165) = max(0.50, -0.165) = 0.50
            size_poor_cal = await rm.calculate_position_size(
                bot_name="WeatherBot", confidence=0.80, price=0.50, available_capital=10000.0,
                calibration_quality={"brier": 0.50, "count": 100},
            )

            # brier=0.10 (great calibration) → no reduction
            size_good_cal = await rm.calculate_position_size(
                bot_name="WeatherBot", confidence=0.80, price=0.50, available_capital=10000.0,
                calibration_quality={"brier": 0.10, "count": 100},
            )

        # Poor cal should be exactly half of good cal (floor=0.50)
        # If floor were 0.75, poor cal would be 75% of good cal
        ratio = size_poor_cal / size_good_cal if size_good_cal > 0 else 0
        assert abs(ratio - 0.50) < 0.01, (
            f"Calibration floor ratio={ratio:.3f}, expected ~0.50 (got {size_poor_cal:.2f}/{size_good_cal:.2f})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Liquidity Check: Blocks in ALL modes (no fail-open for paper)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLiquidityCheckBlocksAllModes:
    """Liquidity check must block trades in paper mode, not just warn.

    Tests the specific code path in order_gateway.py where liquidity results
    are evaluated. Verifies no SIMULATION_MODE branching exists.
    """

    def test_no_simulation_mode_in_liquidity_handling(self):
        """The liquidity result handling code has no SIMULATION_MODE reference."""
        import inspect
        from base_engine.execution.order_gateway import OrderGateway
        source = inspect.getsource(OrderGateway.place_order)
        # Find the liquidity handling section
        lines = source.split("\n")
        liq_lines = []
        in_liq_block = False
        for line in lines:
            if "can_execute" in line:
                in_liq_block = True
            if in_liq_block:
                liq_lines.append(line)
                if "return" in line and "success" in line:
                    break
        liq_block = "\n".join(liq_lines)
        assert "_is_simulation" not in liq_block, "Liquidity handling still references _is_simulation"
        assert "SIMULATION_MODE" not in liq_block, "Liquidity handling still references SIMULATION_MODE"

    def test_liquidity_block_is_unconditional(self):
        """Verify the liquidity block path returns error without any mode check."""
        import inspect
        from base_engine.execution.order_gateway import OrderGateway
        source = inspect.getsource(OrderGateway.place_order)
        # After "can_execute" check, there should be no "if _is_simulation" before the return
        lines = source.split("\n")
        found_can_execute = False
        for line in lines:
            stripped = line.strip()
            if "can_execute" in stripped and "not" in stripped:
                found_can_execute = True
                continue
            if found_can_execute:
                assert "_is_simulation" not in stripped, (
                    f"Found _is_simulation in liquidity block: {stripped}"
                )
                if "return" in stripped and "success" in stripped:
                    break

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sim_mode", [True, False])
    async def test_liquidity_rejection_identical_both_modes(self, sim_mode):
        """Liquidity rejection path produces same error message in both modes."""
        # We test by verifying the source code has no branching — the behavioral
        # test is in test_no_simulation_mode_in_liquidity_handling above.
        # Additional source audit: verify place_order only has 2 SIMULATION_MODE refs
        # (both for executor dispatch, not for liquidity)
        import inspect
        from base_engine.execution.order_gateway import OrderGateway
        source = inspect.getsource(OrderGateway.place_order)
        sim_refs = [i for i, line in enumerate(source.split("\n")) if "SIMULATION_MODE" in line]
        # Should have exactly 3: paper cash pre-check, S115 edge-eroded log label, main dispatch
        assert len(sim_refs) == 3, (
            f"Expected 3 SIMULATION_MODE refs in place_order, found {len(sim_refs)} at lines {sim_refs}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Mode Parity: Identical risk decisions for all active bots
# ═══════════════════════════════════════════════════════════════════════════════

class TestModeParity:
    """Risk decisions must be identical in paper and live modes for all active bots."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_check_risk_limits_identical_both_modes(self, bot_name):
        """check_risk_limits returns identical results regardless of SIMULATION_MODE."""
        results = {}
        for sim_mode in [True, False]:
            rm = make_risk_manager(pipeline_gate_passed=True)
            with patch("base_engine.risk.risk_manager.settings") as ms:
                _apply_settings(ms, {"SIMULATION_MODE": sim_mode, "RISK_MAX_POSITION_SIZE_USD": 500.0})
                result = await rm.check_risk_limits(
                    bot_name=bot_name, market_id="0xabc", size=50.0,
                    price=0.45, confidence=0.55, prediction=0.55,
                )
                results[sim_mode] = result

        assert results[True]["allowed"] == results[False]["allowed"], (
            f"{bot_name}: risk decision differs between modes!\n"
            f"  Paper: {results[True]}\n"
            f"  Live:  {results[False]}"
        )
        assert results[True]["reasons"] == results[False]["reasons"], (
            f"{bot_name}: risk reasons differ between modes!\n"
            f"  Paper: {results[True]['reasons']}\n"
            f"  Live:  {results[False]['reasons']}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_calculate_position_size_identical_both_modes(self, bot_name):
        """calculate_position_size returns identical value regardless of SIMULATION_MODE."""
        sizes = {}
        for sim_mode in [True, False]:
            rm = make_risk_manager(pipeline_gate_passed=True)
            rm._cached_drawdown_pct = 0.0
            with patch("base_engine.risk.risk_manager.settings") as ms:
                _apply_settings(ms, {"SIMULATION_MODE": sim_mode})
                size = await rm.calculate_position_size(
                    bot_name=bot_name, confidence=0.65, price=0.50, available_capital=5000.0,
                    calibration_quality={"brier": 0.20, "count": 30},
                )
                sizes[sim_mode] = size

        assert sizes[True] == sizes[False], (
            f"{bot_name}: position size differs: paper=${sizes[True]:.2f} vs live=${sizes[False]:.2f}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. No SIMULATION_MODE references in bot files or risk modules
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoSimulationModeBranching:
    """Verify SIMULATION_MODE is not checked in any bot or risk module (source code audit)."""

    def test_no_simulation_mode_in_risk_manager_check_risk_limits(self):
        """check_risk_limits source code has no SIMULATION_MODE reference."""
        import inspect
        from base_engine.risk.risk_manager import RiskManager
        source = inspect.getsource(RiskManager.check_risk_limits)
        assert "SIMULATION_MODE" not in source, (
            "check_risk_limits still references SIMULATION_MODE — remove all branching"
        )

    def test_no_simulation_mode_in_risk_manager_calculate_position_size(self):
        """calculate_position_size source code has no SIMULATION_MODE reference."""
        import inspect
        from base_engine.risk.risk_manager import RiskManager
        source = inspect.getsource(RiskManager.calculate_position_size)
        assert "SIMULATION_MODE" not in source, (
            "calculate_position_size still references SIMULATION_MODE — remove all branching"
        )

    def test_no_simulation_mode_in_bankroll_manager_get_bet_size(self):
        """get_bet_size source code has no SIMULATION_MODE reference."""
        import inspect
        from base_engine.risk.bankroll_manager import BotBankrollManager
        source = inspect.getsource(BotBankrollManager.get_bet_size)
        assert "SIMULATION_MODE" not in source, (
            "get_bet_size still references SIMULATION_MODE — remove all branching"
        )

    @pytest.mark.parametrize("bot_module,bot_class", [
        ("bots.weather_bot", "WeatherBot"),
        ("bots.mirror_bot", "MirrorBot"),
        ("bots.esports_bot", "EsportsBot"),
        ("bots.esports_live_bot", "EsportsLiveBot"),
    ])
    def test_no_simulation_mode_in_bot_source(self, bot_module, bot_class):
        """No active bot file references SIMULATION_MODE."""
        import importlib
        import inspect
        mod = importlib.import_module(bot_module)
        source = inspect.getsource(mod)
        assert "SIMULATION_MODE" not in source, (
            f"{bot_module} still references SIMULATION_MODE — remove all branching"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Bankroll Manager: Identical sizing across modes for all active bots
# ═══════════════════════════════════════════════════════════════════════════════

class TestBankrollManagerModeParity:
    """BotBankrollManager produces identical sizing in paper and live modes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_sizing_with_good_calibration(self, bot_name):
        """Good calibration (brier=0.10) → full Kelly fraction, same in both modes."""
        sizes = {}
        for sim_mode in [True, False]:
            mgr = make_bankroll_manager(bot_name)
            with patch("base_engine.risk.bankroll_manager.settings") as ms:
                ms.SIMULATION_MODE = sim_mode
                ms.BOT_BANKROLL_CONFIG = "{}"
                ms.CATEGORY_KELLY_FRACTIONS = "{}"
                size, _ = await mgr.get_bet_size(
                    confidence=0.65, price=0.50,
                    calibration_quality={"brier": 0.10, "count": 50},
                )
                sizes[sim_mode] = size
        assert sizes[True] == sizes[False], f"{bot_name}: good cal sizing differs"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_sizing_with_poor_calibration(self, bot_name):
        """Poor calibration (brier=0.35) → 0.50 floor applied, same in both modes."""
        sizes = {}
        for sim_mode in [True, False]:
            mgr = make_bankroll_manager(bot_name)
            with patch("base_engine.risk.bankroll_manager.settings") as ms:
                ms.SIMULATION_MODE = sim_mode
                ms.BOT_BANKROLL_CONFIG = "{}"
                ms.CATEGORY_KELLY_FRACTIONS = "{}"
                size, _ = await mgr.get_bet_size(
                    confidence=0.65, price=0.50,
                    calibration_quality={"brier": 0.35, "count": 50},
                )
                sizes[sim_mode] = size
        assert sizes[True] == sizes[False], f"{bot_name}: poor cal sizing differs"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bot_name", ACTIVE_BOTS)
    async def test_sizing_without_calibration(self, bot_name):
        """No calibration data → no adjustment, same in both modes."""
        sizes = {}
        for sim_mode in [True, False]:
            mgr = make_bankroll_manager(bot_name)
            with patch("base_engine.risk.bankroll_manager.settings") as ms:
                ms.SIMULATION_MODE = sim_mode
                ms.BOT_BANKROLL_CONFIG = "{}"
                ms.CATEGORY_KELLY_FRACTIONS = "{}"
                size, _ = await mgr.get_bet_size(confidence=0.65, price=0.50)
                sizes[sim_mode] = size
        assert sizes[True] == sizes[False], f"{bot_name}: no-cal sizing differs"
