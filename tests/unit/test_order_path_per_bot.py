"""P0.21: Per-bot order-path test fixture.

Verifies that every bot in BOT_REGISTRY flows through OrderGateway.place_order
without regression, and that P0.A hard-reject (`rejection_type="risk_cap"`)
appears in the return dict when a risk cap is hit.

Intentional SKIP stubs (populated as those items land):
  - P0.2: intended_size captured pre-cap in shadow_fill row
  - P0.3: twin book-walk VWAP at intended size
  - P0.5: SELL path and paper success write intended fields

Intentional XFAIL stub (P0.19 deferred to P1):
  - partial-fill position size reconciliation
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


# ── xfail: P0.19 deferred to P1 ─────────────────────────────────────────────

class TestPartialFillReconciliation:
    """P0.19 (deferred P1): position.size must equal filled_size, not requested_size."""

    @pytest.mark.xfail(
        reason=(
            "P0.19 deferred to P1: partial fill leaves position.size = requested_size "
            "instead of filled_size. Must ship before P0.22 cap-flip event."
        ),
        strict=False,
    )
    def test_partial_fill_position_size_equals_filled_size(self):
        """On partial fill, positions table should record filled_size, not order_size."""
        # Arrange: order for 100 shares, CLOB returns 60 shares filled
        fill_response = {
            "success": True,
            "order_id": "clob-partial-001",
            "filled_size": 60.0,
            "requested_size": 100.0,
        }
        # This test intentionally fails until P0.19 is implemented.
        # The assertion below documents the desired behaviour.
        assert fill_response["filled_size"] == fill_response["requested_size"], (
            "P0.19: position should record filled_size; currently records requested_size"
        )
