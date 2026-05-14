"""P0.21: Per-bot order-path test fixture.

Verifies that every bot in BOT_REGISTRY flows through OrderGateway.place_order
without regression, and that P0.A hard-reject (`rejection_type="risk_cap"`)
appears in the return dict when a risk cap is hit.

Intentional SKIP stubs (populated as those items land):
  - P0.2: intended_size captured pre-cap in shadow_fill row
  - P0.3: twin book-walk VWAP at intended size
  - P0.5: SELL path and paper success write intended fields
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── All 14 bots in BOT_REGISTRY ─────────────────────────────────────────────
BOT_NAMES = [
    "ArbitrageBot",
    "MirrorBot",
    "CrossPlatformArbBot",
    "OracleBot",
    "SportsBot",
    "LLMForecasterBot",
    "WeatherBot",
    "SportsInjuryBot",
    "SportsLiveBot",
    "SportsArbBot",
    "EsportsBot",
    "EsportsBotV2",
    "EsportsLiveBot",
    "LogicalArbBot",
]


def _make_gateway(risk_allowed: bool = True, risk_reasons: list | None = None):
    """Build an OrderGateway with minimal mocked dependencies."""
    from base_engine.execution.order_gateway import OrderGateway

    risk_reasons = risk_reasons or []
    risk_manager = MagicMock()
    risk_manager.check_risk_limits = AsyncMock(
        return_value={"allowed": risk_allowed, "reasons": risk_reasons}
    )

    kill_switch = MagicMock()
    kill_switch.is_engaged = AsyncMock(return_value=False)

    paper_engine = MagicMock()
    paper_engine.enabled = True
    paper_engine.cash = 999_999.0
    paper_engine.place_order = AsyncMock(
        return_value={"success": True, "order_id": "paper-001"}
    )
    paper_engine.realized_pnl_today = {}

    gw = OrderGateway(
        kill_switch=kill_switch,
        risk_manager=risk_manager,
        trade_coordinator=None,
        execution_engine=None,
        paper_trading_engine=paper_engine,
    )
    # NegRisk gate: mark market as single-outcome so _can_exit returns True
    gw._open_position_markets["_negRisk_test"] = set()
    return gw


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Fixture helpers ──────────────────────────────────────────────────────────

@pytest.fixture(params=BOT_NAMES)
def bot_name(request):
    return request.param


# ── Core path tests (parameterized over all 14 bots) ────────────────────────

class TestRiskCapHardReject:
    """P0.A: rejection_type='risk_cap' present when only size/exposure reasons block trade."""

    def test_rejection_type_risk_cap_in_return(self, bot_name):
        gw = _make_gateway(
            risk_allowed=False,
            risk_reasons=["Position $50 exceeds max $30"],
        )
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name=bot_name,
                    market_id="0xdeadbeef",
                    token_id="0xdeadbeef",
                    side="YES",
                    size=100.0,
                    price=0.5,
                )
            )
        assert result["success"] is False
        assert result.get("rejection_type") == "risk_cap", (
            f"bot={bot_name}: expected rejection_type='risk_cap', got {result!r}"
        )

    def test_other_risk_reasons_no_rejection_type(self, bot_name):
        """Non-cap reasons (e.g. portfolio-level limit) should NOT get rejection_type='risk_cap'."""
        gw = _make_gateway(
            risk_allowed=False,
            risk_reasons=["Portfolio-level exposure limit exceeded"],
        )
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name=bot_name,
                    market_id="0xdeadbeef",
                    token_id="0xdeadbeef",
                    side="YES",
                    size=10.0,
                    price=0.5,
                )
            )
        assert result["success"] is False
        assert result.get("rejection_type") != "risk_cap", (
            f"bot={bot_name}: non-cap reason should not produce rejection_type='risk_cap'"
        )


class TestHappyPath:
    """Risk allowed → paper engine called → success propagated."""

    def test_paper_order_succeeds(self, bot_name):
        gw = _make_gateway(risk_allowed=True)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name=bot_name,
                    market_id="0xdeadbeef",
                    token_id="0xdeadbeef",
                    side="YES",
                    size=10.0,
                    price=0.5,
                )
            )
        assert result["success"] is True, (
            f"bot={bot_name}: expected success=True, got {result!r}"
        )


class TestMirrorBotRtdsFastPath:
    """MirrorBot RTDS correlation_id bypasses risk_manager — risk_manager NOT called."""

    def test_rtds_skips_risk_manager(self):
        gw = _make_gateway(risk_allowed=True)
        with patch.object(type(gw), "_can_exit", return_value=True):
            with patch("base_engine.execution.order_gateway.settings") as mock_settings:
                mock_settings.CANARY_STAGE = 0
                mock_settings.MIRROR_RTDS_FAST_PATH = True
                mock_settings.L4_ADVERSE_SIZING_ENABLED = False
                mock_settings.RL_TRADE_TIMING_ENABLED = False
                mock_settings.SIMULATION_MODE = True
                result = _run(
                    gw.place_order(
                        bot_name="MirrorBot",
                        market_id="0xdeadbeef",
                        token_id="0xdeadbeef",
                        side="YES",
                        size=10.0,
                        price=0.5,
                        correlation_id="rtds:abc123",
                    )
                )
        # risk_manager.check_risk_limits should NOT have been called for RTDS fast path
        gw.risk_manager.check_risk_limits.assert_not_awaited()


# ── P0.1: Loop guard ────────────────────────────────────────────────────────

class TestLoopGuard:
    """P0.1: Repeated entries into the same (bot, market) within window are halted."""

    def _run_entry(self, gw, bot_name="MirrorBot", market_id="0xlooptest"):
        with patch.object(type(gw), "_can_exit", return_value=True):
            return _run(
                gw.place_order(
                    bot_name=bot_name,
                    market_id=market_id,
                    token_id="0xlooptest",
                    side="YES",
                    size=1.0,
                    price=0.5,
                )
            )

    def test_first_four_entries_pass(self):
        gw = _make_gateway(risk_allowed=True)
        for i in range(4):
            result = self._run_entry(gw)
            assert result["success"] is True, f"entry {i+1} should pass, got {result!r}"

    def test_fifth_entry_halts(self):
        gw = _make_gateway(risk_allowed=True)
        for _ in range(4):
            self._run_entry(gw)
        result = self._run_entry(gw)
        assert result["success"] is False, f"5th entry should halt, got {result!r}"
        assert "loop_guard_halt" in result.get("error", ""), (
            f"error should mention loop_guard_halt, got {result!r}"
        )

    def test_sell_not_counted(self):
        """SELL orders must not count toward the entry loop guard."""
        gw = _make_gateway(risk_allowed=True)
        # Exhaust the limit with 4 entries (1 below threshold)
        for _ in range(4):
            self._run_entry(gw)
        # A SELL should not trigger halt even after 4 BUYs
        gw._open_position_markets["MirrorBot"] = {"0xlooptest"}
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="MirrorBot",
                    market_id="0xlooptest",
                    token_id="0xlooptest",
                    side="SELL",
                    size=1.0,
                    price=0.5,
                )
            )
        assert "loop_guard_halt" not in result.get("error", ""), (
            f"SELL should not trigger loop_guard_halt, got {result!r}"
        )

    def test_different_markets_independent(self):
        """Loop guard is per-(bot, market) — different market should not be blocked."""
        gw = _make_gateway(risk_allowed=True)
        for _ in range(4):
            self._run_entry(gw, market_id="0xlooptest1")
        # Different market — should pass even after 4 entries on looptest1
        result = self._run_entry(gw, market_id="0xlooptest2")
        assert result["success"] is True, (
            f"different market should not be blocked, got {result!r}"
        )


# ── Skip stubs for items not yet landed ─────────────────────────────────────

class TestIntendedSizeInShadowFill:
    """P0.2: intended_size_usd populated in shadow_fills row (pre-cap value)."""

    @pytest.mark.skip(reason="P0.2 not yet implemented: BotBankrollManager.get_bet_size() tuple return")
    def test_intended_size_usd_in_shadow_fill(self, bot_name):
        pass

    @pytest.mark.skip(reason="P0.3 not yet implemented: twin book-walk at intended size")
    def test_vwap_at_intended_in_shadow_fill(self, bot_name):
        pass

    @pytest.mark.skip(reason="P0.5 not yet implemented: SELL path shadow_fill write")
    def test_sell_path_shadow_fill_write(self, bot_name):
        pass


# ── P0.19: Partial-fill position reconciliation ─────────────────────────────

class TestPartialFillReconciliation:
    """P0.19: position.size must equal filled_size on partial fills, with
    matching adjustment to in-memory exposure trackers."""

    @staticmethod
    def _make_db_with_position(initial_size: float = 100.0):
        """Mock DB whose session yields a Position-like row with mutable size/entry_cost."""
        pos = MagicMock()
        pos.size = initial_size
        pos.entry_cost = 0.0

        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=pos)

        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        db = MagicMock()
        db.session_factory = MagicMock()  # truthy
        db.get_session = MagicMock(return_value=session)
        return db, session, pos

    def test_partial_fill_updates_position_size(self):
        """Partial fill (60/100): position.size should become 60, exposure rescaled."""
        gw = _make_gateway()
        db, _session, pos = self._make_db_with_position(initial_size=100.0)
        gw.db = db
        gw._pending_orders["clob-partial-001"] = {
            "market_id": "0xMARKET",
            "token_id": "tok-1",
            "side": "BUY",
            "size": 100.0,
            "price": 0.50,
            "bot_name": "MirrorBot",
            "submitted_at": 0.0,
            "correlation_id": "corr-1",
        }
        gw._position_exposure["MirrorBot"] = {"0xMARKET": 50.0}  # 100 * 0.50
        gw._total_exposure_usd = 50.0

        _run(gw._on_order_filled({"id": "clob-partial-001", "size": 60.0, "price": 0.50}))

        assert pos.size == 60.0, f"expected pos.size=60.0, got {pos.size}"
        assert gw._position_exposure["MirrorBot"]["0xMARKET"] == 30.0  # 60 * 0.50
        assert gw._total_exposure_usd == 30.0

    def test_full_fill_does_not_adjust_position(self):
        """Full fill (100/100): no DB call to adjust position."""
        gw = _make_gateway()
        db, session, pos = self._make_db_with_position(initial_size=100.0)
        gw.db = db
        gw._pending_orders["clob-full-001"] = {
            "market_id": "0xMARKET",
            "token_id": "tok-1",
            "side": "BUY",
            "size": 100.0,
            "price": 0.50,
            "bot_name": "MirrorBot",
            "submitted_at": 0.0,
            "correlation_id": "corr-1",
        }

        _run(gw._on_order_filled({"id": "clob-full-001", "size": 100.0, "price": 0.50}))

        assert pos.size == 100.0  # unchanged — adjust path not entered
        # full-fill path doesn't open a session for adjustment
        db.get_session.assert_not_called()

    def test_sell_side_skipped(self):
        """SELL is skipped — no DB call, no exception."""
        gw = _make_gateway()
        db, _session, pos = self._make_db_with_position(initial_size=100.0)
        gw.db = db

        _run(gw._adjust_position_for_partial_fill(
            bot_name="MirrorBot",
            market_id="0xMARKET",
            side="SELL",
            new_size=60.0,
            fill_price=0.50,
        ))

        assert pos.size == 100.0
        db.get_session.assert_not_called()

    def test_no_db_no_op(self):
        """When db is None, helper returns silently (no exception)."""
        gw = _make_gateway()
        gw.db = None
        # Should not raise
        _run(gw._adjust_position_for_partial_fill(
            bot_name="MirrorBot",
            market_id="0xMARKET",
            side="BUY",
            new_size=60.0,
            fill_price=0.50,
        ))


# ─────────────────────────────────────────────────────────────────────────────
# S215 Phase 2: depth-gate soft-clamp for WeatherBot
# ─────────────────────────────────────────────────────────────────────────────


def _make_gateway_with_liq_guard(liq_response):
    """Build OrderGateway with a mocked liquidity_guardian that returns liq_response."""
    from base_engine.execution.order_gateway import OrderGateway

    risk_manager = MagicMock()
    risk_manager.check_risk_limits = AsyncMock(return_value={"allowed": True, "reasons": []})

    kill_switch = MagicMock()
    kill_switch.is_engaged = AsyncMock(return_value=False)

    paper_engine = MagicMock()
    paper_engine.enabled = True
    paper_engine.cash = 999_999.0
    paper_engine.place_order = AsyncMock(return_value={"success": True, "order_id": "paper-soft"})
    paper_engine.realized_pnl_today = {}

    liq_guardian = MagicMock()
    liq_guardian.check_liquidity = AsyncMock(return_value=liq_response)

    gw = OrderGateway(
        kill_switch=kill_switch,
        risk_manager=risk_manager,
        trade_coordinator=None,
        execution_engine=None,
        paper_trading_engine=paper_engine,
        liquidity_guardian=liq_guardian,
    )
    gw._open_position_markets["_negRisk_test"] = set()
    return gw


class TestDepthGateSoftClampWB:
    """S215 Phase 2: WB depth_exceeded gets soft-clamped to max_safe.

    Pre-fix: 118 depth_exceeded hard-rejects/24h on WB. The guardian already
    returns max_safe; the caller (this gate) was discarding it. Soft-clamp
    resizes the order to fit and proceeds, so identified edges actually
    become trades. Scope intentionally bot-restricted to WeatherBot.
    """

    def test_wb_depth_exceeded_soft_clamps_and_proceeds(self):
        """WB + depth_exceeded + max_safe>0 + clamped above floor → resized order placed."""
        liq_response = {
            "can_execute": False,
            "reason": "depth_exceeded",
            "trade_size": 100.0,
            "liquidity_depth": 50.0,
            "max_safe": 25.0,  # 100 → 25*0.95 = 23.75 shares; at price 0.50 = $11.875 (above $5 floor)
            "depth_multiplier": 5.0,
            "recommendation": "reduce_size",
        }
        gw = _make_gateway_with_liq_guard(liq_response)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="WeatherBot",
                    market_id="0xdead",
                    token_id="0xdead",
                    side="YES",
                    size=100.0,
                    price=0.50,
                )
            )
        assert result["success"] is True, f"expected success after soft-clamp, got {result!r}"
        # Paper engine must have been called with the clamped size, not the original
        paper_call = gw.paper_trading_engine.place_order.call_args
        # Find the size argument (positional or kwarg)
        clamped_size = paper_call.kwargs.get("size") if "size" in paper_call.kwargs else paper_call.args[3]
        assert clamped_size < 100.0, f"size not reduced: {clamped_size}"
        assert clamped_size <= 25.0 * 0.95 + 1e-6, (
            f"clamped size should be ≤ max_safe*0.95 (≤{25.0 * 0.95}), got {clamped_size}"
        )

    def test_wb_depth_exceeded_clamped_below_floor_aborts(self):
        """WB + max_safe so small that clamped_usd < $5 floor → still rejects."""
        liq_response = {
            "can_execute": False,
            "reason": "depth_exceeded",
            "trade_size": 100.0,
            "liquidity_depth": 5.0,
            "max_safe": 5.0,  # 5 * 0.95 = 4.75 shares × price 0.10 = $0.475 << $5 floor
            "depth_multiplier": 1.0,
            "recommendation": "reduce_size",
        }
        gw = _make_gateway_with_liq_guard(liq_response)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="WeatherBot",
                    market_id="0xdead",
                    token_id="0xdead",
                    side="YES",
                    size=100.0,
                    price=0.10,
                )
            )
        assert result["success"] is False
        assert "soft-clamp below floor" in result["error"], (
            f"expected soft-clamp-below-floor abort, got {result!r}"
        )
        # Paper engine must NOT have been called
        gw.paper_trading_engine.place_order.assert_not_awaited()

    def test_wb_depth_exceeded_max_safe_zero_aborts(self):
        """WB + max_safe=0 (no resize possible) → keeps hard-reject."""
        liq_response = {
            "can_execute": False,
            "reason": "depth_exceeded",
            "trade_size": 100.0,
            "liquidity_depth": 0.0,
            "max_safe": 0.0,
            "depth_multiplier": 5.0,
            "recommendation": "reduce_size",
        }
        gw = _make_gateway_with_liq_guard(liq_response)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="WeatherBot",
                    market_id="0xdead",
                    token_id="0xdead",
                    side="YES",
                    size=100.0,
                    price=0.50,
                )
            )
        assert result["success"] is False
        # Falls into the else-branch: standard "Order blocked: liquidity" message
        assert "depth_exceeded" in result["error"]
        assert "soft-clamp" not in result["error"], (
            "max_safe=0 should not trigger soft-clamp path"
        )
        gw.paper_trading_engine.place_order.assert_not_awaited()

    def test_mirrorbot_depth_exceeded_still_hard_rejects(self):
        """MB + depth_exceeded → keeps hard-reject (soft-clamp scoped to WB only)."""
        liq_response = {
            "can_execute": False,
            "reason": "depth_exceeded",
            "trade_size": 100.0,
            "liquidity_depth": 50.0,
            "max_safe": 25.0,
            "depth_multiplier": 2.0,
            "recommendation": "reduce_size",
        }
        gw = _make_gateway_with_liq_guard(liq_response)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="MirrorBot",
                    market_id="0xdead",
                    token_id="0xdead",
                    side="YES",
                    size=100.0,
                    price=0.50,
                )
            )
        assert result["success"] is False
        assert "depth_exceeded" in result["error"]
        gw.paper_trading_engine.place_order.assert_not_awaited()

    def test_wb_other_liquidity_reasons_still_hard_reject(self):
        """WB + non-depth_exceeded liquidity failure (e.g., no_orderbook_data) → hard-reject."""
        liq_response = {
            "can_execute": False,
            "reason": "no_orderbook_data",
            "recommendation": "abort",
        }
        gw = _make_gateway_with_liq_guard(liq_response)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="WeatherBot",
                    market_id="0xdead",
                    token_id="0xdead",
                    side="YES",
                    size=100.0,
                    price=0.50,
                )
            )
        assert result["success"] is False
        assert "no_orderbook_data" in result["error"]
        assert "soft-clamp" not in result["error"]
        gw.paper_trading_engine.place_order.assert_not_awaited()

    def test_wb_depth_exceeded_recommendation_not_reduce_size_hard_rejects(self):
        """WB + depth_exceeded but recommendation != 'reduce_size' → hard-reject (defensive)."""
        liq_response = {
            "can_execute": False,
            "reason": "depth_exceeded",
            "max_safe": 25.0,
            "recommendation": "abort",  # Hypothetical future variant
        }
        gw = _make_gateway_with_liq_guard(liq_response)
        with patch.object(type(gw), "_can_exit", return_value=True):
            result = _run(
                gw.place_order(
                    bot_name="WeatherBot",
                    market_id="0xdead",
                    token_id="0xdead",
                    side="YES",
                    size=100.0,
                    price=0.50,
                )
            )
        assert result["success"] is False
        gw.paper_trading_engine.place_order.assert_not_awaited()
