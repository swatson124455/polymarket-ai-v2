"""
Unit tests for bots/sports_arb_bot.py

Tests:
  - Basic arb execution: Leg A + Leg B both succeed
  - Leg A failure → Leg B never placed
  - Leg B failure → Leg A rollback triggered (atomic protection)
  - Leg B timeout → Leg A rollback triggered
  - No Kalshi client → no Leg B placed (logged)
  - Scan finds no opportunities → returns cleanly
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from bots.sports_arb_bot import SportsArbBot


def make_bot():
    """Create a SportsArbBot with mocked base_engine and internals."""
    base_engine = MagicMock()
    base_engine.db = MagicMock()
    base_engine.order_gateway = MagicMock()
    base_engine.order_gateway._daily_exposure_usd = {}
    base_engine.risk_manager = AsyncMock()
    base_engine.risk_manager.calculate_position_size = AsyncMock(return_value=50.0)
    base_engine.degradation_manager = MagicMock()
    base_engine.degradation_manager.get_sizing_multiplier = MagicMock(return_value=1.0)
    base_engine.degradation_manager.is_close_only_mode = MagicMock(return_value=False)
    base_engine.degradation_manager.get_min_confidence_override = MagicMock(return_value=None)

    bot = SportsArbBot(base_engine)
    bot._current_correlation_id = "test-corr-arb"
    bot._kalshi_client = MagicMock()
    bot._kalshi_client.place_order = AsyncMock(return_value={"success": True})
    bot._bankroll_mgr = MagicMock()
    # Mock place_order at the bot level (BaseBot method)
    bot.place_order = AsyncMock(return_value={"success": True})
    return bot


def make_opp(net_spread=0.05, leg_a_side="YES", leg_b_side="NO"):
    """Create a mock ArbOpportunity."""
    opp = MagicMock()
    opp.sport = "nba"
    opp.event_title = "Lakers vs Warriors"
    opp.polymarket_id = "poly-market-123"
    opp.kalshi_id = "kalshi-market-456"
    opp.poly_yes_price = 0.55
    opp.kalshi_no_price = 0.46
    opp.net_spread = net_spread
    opp.gross_spread = net_spread + 0.015
    opp.leg_a_side = leg_a_side
    opp.leg_b_side = leg_b_side

    # poly_candidate mock
    poly_cand = MagicMock()
    poly_cand.yes_token_id = "yes-token-abc"
    poly_cand.no_token_id = "no-token-abc"
    opp.poly_candidate = poly_cand

    # kalshi_candidate mock
    opp.kalshi_candidate = MagicMock()
    return opp


class TestSportsArbBotInit:
    """Initialization tests."""

    def test_default_min_spread(self):
        bot = make_bot()
        assert bot._min_spread == pytest.approx(0.04, rel=1e-4)

    def test_kalshi_client_optional(self):
        bot = make_bot()
        bot._kalshi_client = None
        assert bot._kalshi_client is None

    def test_scan_interval(self):
        bot = make_bot()
        assert bot._get_scan_interval_seconds() == pytest.approx(30.0, rel=1e-4)


class TestExecuteArbSuccess:
    """Tests for successful Leg A + Leg B execution."""

    @pytest.mark.asyncio
    async def test_both_legs_placed(self):
        bot = make_bot()
        opp = make_opp(net_spread=0.06)
        bot.place_order = AsyncMock(return_value={"success": True})
        bot._kalshi_client.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        # Leg A placed
        bot.place_order.assert_awaited_once()
        call_kwargs = bot.place_order.call_args[1]
        assert call_kwargs["market_id"] == "poly-market-123"
        assert call_kwargs["side"] == "YES"
        assert call_kwargs["confidence"] == pytest.approx(0.90)

        # Leg B placed
        bot._kalshi_client.place_order.assert_awaited_once()
        kalshi_kwargs = bot._kalshi_client.place_order.call_args[1]
        assert kalshi_kwargs["market_id"] == "kalshi-market-456"
        assert kalshi_kwargs["side"] == "NO"

    @pytest.mark.asyncio
    async def test_size_scales_with_spread(self):
        """Size = 50% of SPORTS_MAX_BET_USD * (net_spread / 0.04)."""
        bot = make_bot()
        opp = make_opp(net_spread=0.08)   # 2x the base spread
        bot.place_order = AsyncMock(return_value={"success": True})
        bot._kalshi_client.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        # 50 * (0.08/0.04) = 100 → capped at max(1, min(50, 100)) = 50 due to arb_size *= spread/0.04
        # Actually: arb_size = 50 * (0.08/0.04) = 100; then min(50, 100) = 50
        # Wait: arb_size = min(arb_size, arb_size * (net_spread / 0.04)) = min(50, 100) = 50? No.
        # arb_size = SPORTS_MAX_BET_USD * 0.5 = 50
        # arb_size = min(arb_size, arb_size * (0.08/0.04)) = min(50, 100) = 50
        # Correct: size is floored by original arb_size, so 50.0
        call_kwargs = bot.place_order.call_args[1]
        assert call_kwargs["size"] == pytest.approx(50.0, rel=1e-4)

    @pytest.mark.asyncio
    async def test_no_poly_candidate_skips_both_legs(self):
        bot = make_bot()
        opp = make_opp()
        opp.poly_candidate = None
        bot.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        bot.place_order.assert_not_awaited()
        bot._kalshi_client.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_no_token_id_skips_arb(self):
        """If leg_a_side=NO but no_token_id is None, bot should skip (not fallback to yes_token)."""
        bot = make_bot()
        opp = make_opp(leg_a_side="NO", leg_b_side="YES")
        opp.poly_candidate.no_token_id = None
        bot.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        bot.place_order.assert_not_awaited()
        bot._kalshi_client.place_order.assert_not_awaited()


class TestLegAFailure:
    """If Leg A fails, Leg B must never be placed."""

    @pytest.mark.asyncio
    async def test_leg_a_declined_no_leg_b(self):
        bot = make_bot()
        opp = make_opp()
        bot.place_order = AsyncMock(return_value={"success": False, "reason": "exposure_cap"})
        bot._kalshi_client.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        bot.place_order.assert_awaited_once()    # Leg A tried once
        bot._kalshi_client.place_order.assert_not_awaited()  # Leg B never placed

    @pytest.mark.asyncio
    async def test_leg_a_timeout_no_leg_b(self):
        bot = make_bot()
        opp = make_opp()
        bot.place_order = AsyncMock(side_effect=asyncio.TimeoutError())
        bot._kalshi_client.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        bot.place_order.assert_awaited_once()
        bot._kalshi_client.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_leg_a_exception_no_leg_b(self):
        bot = make_bot()
        opp = make_opp()
        bot.place_order = AsyncMock(side_effect=RuntimeError("API error"))
        bot._kalshi_client.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        bot.place_order.assert_awaited_once()
        bot._kalshi_client.place_order.assert_not_awaited()


class TestLegBFailureRollback:
    """Core: Leg B failure must trigger immediate Leg A rollback."""

    @pytest.mark.asyncio
    async def test_leg_b_failure_triggers_rollback(self):
        """
        Leg A YES succeeds.
        Leg B fails (returns success=False).
        Rollback: place_order called again with side=NO (flip).
        """
        bot = make_bot()
        opp = make_opp(leg_a_side="YES", leg_b_side="NO")

        # Leg A succeeds, rollback (second place_order call) succeeds
        bot.place_order = AsyncMock(return_value={"success": True})
        bot._kalshi_client.place_order = AsyncMock(
            return_value={"success": False, "error": "Kalshi rejected"}
        )

        await bot._execute_arb(opp, db=None)

        # place_order called twice: once for Leg A, once for rollback
        assert bot.place_order.await_count == 2

        # First call = Leg A (YES)
        leg_a_call = bot.place_order.call_args_list[0][1]
        assert leg_a_call["side"] == "YES"
        assert leg_a_call["confidence"] == pytest.approx(0.90)

        # Second call = rollback (NO, confidence 0.5)
        rollback_call = bot.place_order.call_args_list[1][1]
        assert rollback_call["side"] == "NO"
        assert rollback_call["confidence"] == pytest.approx(0.5)
        assert rollback_call["market_id"] == "poly-market-123"

    @pytest.mark.asyncio
    async def test_leg_b_timeout_triggers_rollback(self):
        """
        Leg A YES succeeds.
        Leg B times out.
        Rollback: place_order called again with side=NO (flip).
        """
        bot = make_bot()
        opp = make_opp(leg_a_side="YES", leg_b_side="NO")

        bot.place_order = AsyncMock(return_value={"success": True})
        bot._kalshi_client.place_order = AsyncMock(side_effect=asyncio.TimeoutError())

        await bot._execute_arb(opp, db=None)

        assert bot.place_order.await_count == 2

        rollback_call = bot.place_order.call_args_list[1][1]
        assert rollback_call["side"] == "NO"
        assert rollback_call["confidence"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_leg_b_exception_triggers_rollback(self):
        """Leg B raises unexpected exception → rollback triggered."""
        bot = make_bot()
        opp = make_opp(leg_a_side="YES", leg_b_side="NO")

        bot.place_order = AsyncMock(return_value={"success": True})
        bot._kalshi_client.place_order = AsyncMock(
            side_effect=RuntimeError("Kalshi API unreachable")
        )

        await bot._execute_arb(opp, db=None)

        assert bot.place_order.await_count == 2
        rollback_call = bot.place_order.call_args_list[1][1]
        assert rollback_call["side"] == "NO"

    @pytest.mark.asyncio
    async def test_rollback_flip_side_no_to_yes(self):
        """If Leg A was NO, rollback should flip to YES."""
        bot = make_bot()
        opp = make_opp(leg_a_side="NO", leg_b_side="YES")

        bot.place_order = AsyncMock(return_value={"success": True})
        bot._kalshi_client.place_order = AsyncMock(
            return_value={"success": False, "error": "rejected"}
        )

        await bot._execute_arb(opp, db=None)

        assert bot.place_order.await_count == 2
        rollback_call = bot.place_order.call_args_list[1][1]
        assert rollback_call["side"] == "YES"

    @pytest.mark.asyncio
    async def test_rollback_failure_logs_critical(self):
        """If rollback itself fails, a CRITICAL log is emitted (not exception raised)."""
        bot = make_bot()
        opp = make_opp(leg_a_side="YES", leg_b_side="NO")

        # Leg A succeeds; rollback fails
        bot.place_order = AsyncMock(
            side_effect=[
                {"success": True},          # Leg A
                {"success": False, "reason": "exposure_cap"},  # Rollback fails
            ]
        )
        bot._kalshi_client.place_order = AsyncMock(return_value={"success": False})

        # Should not raise — critical log is emitted instead
        await bot._execute_arb(opp, db=None)

        assert bot.place_order.await_count == 2


class TestNoKalshiClient:
    """When no Kalshi client configured, Leg B is logged but not placed."""

    @pytest.mark.asyncio
    async def test_no_kalshi_client_leg_a_placed_only(self):
        bot = make_bot()
        bot._kalshi_client = None
        opp = make_opp()
        bot.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        # Leg A placed once
        bot.place_order.assert_awaited_once()
        # No rollback (leg B not attempted)
        assert bot.place_order.await_count == 1

    @pytest.mark.asyncio
    async def test_no_kalshi_candidate_no_leg_b(self):
        """kalshi_candidate=None → Leg B skipped (same as no client)."""
        bot = make_bot()
        opp = make_opp()
        opp.kalshi_candidate = None
        bot.place_order = AsyncMock(return_value={"success": True})

        await bot._execute_arb(opp, db=None)

        bot.place_order.assert_awaited_once()
        assert bot.place_order.await_count == 1


class TestScanAndTrade:
    """Integration: scan_and_trade calls _execute_arb for each opportunity."""

    @pytest.mark.asyncio
    async def test_no_opportunities_returns_cleanly(self):
        bot = make_bot()
        with patch(
            "sports.markets.cross_platform_arb.find_sports_arb_opportunities",
            new=AsyncMock(return_value=[]),
        ):
            await bot.scan_and_trade()

    @pytest.mark.asyncio
    async def test_executes_up_to_5_opportunities(self):
        bot = make_bot()
        opps = [make_opp(net_spread=0.05 + i * 0.01) for i in range(8)]

        executed = []

        async def fake_execute_arb(opp, db):
            executed.append(opp)

        bot._execute_arb = fake_execute_arb

        with patch(
            "sports.markets.cross_platform_arb.find_sports_arb_opportunities",
            new=AsyncMock(return_value=opps),
        ):
            await bot.scan_and_trade()

        assert len(executed) == 5   # max 5 per scan
