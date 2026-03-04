"""
Unit tests for bots/logical_arb_bot.py (LogicalArbBot).

Tests:
  - __init__: bot name, min_spread, max_position from settings
  - Scan interval: uses SCAN_INTERVAL_LOGICAL_ARB via _SCAN_INTERVAL_KEYS
  - analyze_opportunity: always returns None (scan-driven)
  - _get_yes_token_id: extracts YES token ID from various market formats
  - scan_and_trade: fetches markets, scans for opportunities, caps at 3 executions
  - _execute_mutual_exclusivity: sells NO on the most overpriced market
  - _execute_subset_violation: two-leg trade (NO on subset, YES on superset)
  - _execute_complement_violation: buy both YES if sum < 1, sell both if sum > 1
  - Edge cases: risk blocked, missing token, missing market, empty opportunities
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bots.logical_arb_bot import LogicalArbBot, _get_yes_token_id, MAX_OPPS_PER_SCAN


# ──────────────────────────── Fixtures ────────────────────────────


def _make_engine():
    """Create a mocked base_engine with all attributes LogicalArbBot needs."""
    engine = MagicMock()
    engine.db = MagicMock()
    engine.cache = MagicMock()
    engine.order_gateway = MagicMock()
    engine.order_gateway._daily_exposure_usd = {}
    engine.get_markets = AsyncMock(return_value=[])
    engine.risk_manager = MagicMock()
    engine.risk_manager.check_risk_limits = AsyncMock(
        return_value={"allowed": True, "reasons": []}
    )
    engine.risk_manager.calculate_position_size = AsyncMock(return_value=50.0)
    engine.place_order = AsyncMock(return_value={"success": True})
    return engine


def _make_bot(engine=None, min_spread=0.025, max_position=200):
    """Create a LogicalArbBot with patched settings."""
    if engine is None:
        engine = _make_engine()
    with patch("bots.logical_arb_bot.settings") as mock_s:
        mock_s.LOGICAL_ARB_MIN_SPREAD = str(min_spread)
        mock_s.LOGICAL_ARB_MAX_POSITION_USD = str(max_position)
        mock_s.SCAN_INTERVAL_LOGICAL_ARB = 300
        bot = LogicalArbBot(engine)
    bot.running = True  # mark bot as running so place_order works
    return bot


def _make_market(market_id="m1", yes_price=0.60, yes_token_id="tok-yes-1"):
    """Create a minimal market dict."""
    return {
        "id": market_id,
        "question": f"Market {market_id}?",
        "tokens": [
            {"tokenId": yes_token_id, "outcome": "Yes"},
            {"tokenId": f"tok-no-{market_id}", "outcome": "No"},
        ],
        "yes_price": yes_price,
    }


# ──────────────────────────── Group A: Init ────────────────────────────


class TestInit:

    def test_bot_name(self):
        bot = _make_bot()
        assert bot.bot_name == "LogicalArbBot"

    def test_min_spread_from_settings(self):
        bot = _make_bot(min_spread=0.05)
        assert bot.min_spread == 0.05

    def test_max_position_from_settings(self):
        bot = _make_bot(max_position=500)
        assert bot.max_position_usd == 500.0

    def test_detector_is_none_initially(self):
        bot = _make_bot()
        assert bot._detector is None

    def test_market_cache_is_empty_initially(self):
        bot = _make_bot()
        assert bot._market_cache == {}


# ──────────────────────────── Group B: Scan Interval ────────────────────────────


class TestScanInterval:

    def test_scan_interval_key_mapping(self):
        """LogicalArbBot maps to SCAN_INTERVAL_LOGICAL_ARB via _SCAN_INTERVAL_KEYS."""
        from bots.base_bot import _SCAN_INTERVAL_KEYS
        assert _SCAN_INTERVAL_KEYS.get("LogicalArbBot") == "LOGICAL_ARB"

    def test_scan_interval_reads_setting(self):
        bot = _make_bot()
        with patch("bots.base_bot.settings") as mock_s:
            mock_s.SCAN_INTERVAL_LOGICAL_ARB = 300
            mock_s.BOT_SCAN_INTERVAL_SECONDS = 60
            mock_s.DEFAULT_SCAN_INTERVAL = 60
            mock_s.USE_SCAN_JITTER = False
            interval = bot._get_scan_interval()
        assert interval == 300.0


# ──────────────────────────── Group C: analyze_opportunity ────────────────────────────


class TestAnalyzeOpportunity:

    @pytest.mark.asyncio
    async def test_always_returns_none(self):
        bot = _make_bot()
        result = await bot.analyze_opportunity({"id": "m1", "price": 0.5})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_empty_dict(self):
        bot = _make_bot()
        result = await bot.analyze_opportunity({})
        assert result is None


# ──────────────────────────── Group D: _get_yes_token_id ────────────────────────────


class TestGetYesTokenId:

    def test_direct_yes_token_id_field(self):
        market = {"yes_token_id": "tok-123"}
        assert _get_yes_token_id(market) == "tok-123"

    def test_camelcase_yesTokenId_field(self):
        market = {"yesTokenId": "tok-456"}
        assert _get_yes_token_id(market) == "tok-456"

    def test_tokens_array_with_yes_outcome(self):
        market = {
            "tokens": [
                {"tokenId": "tok-no", "outcome": "No"},
                {"tokenId": "tok-yes", "outcome": "Yes"},
            ]
        }
        assert _get_yes_token_id(market) == "tok-yes"

    def test_tokens_array_fallback_first_token(self):
        """If no outcome field matches YES, first token is used as fallback."""
        market = {
            "tokens": [
                {"tokenId": "tok-first"},
                {"tokenId": "tok-second"},
            ]
        }
        assert _get_yes_token_id(market) == "tok-first"

    def test_empty_market_returns_empty_string(self):
        assert _get_yes_token_id({}) == ""

    def test_empty_tokens_list_returns_empty_string(self):
        assert _get_yes_token_id({"tokens": []}) == ""


# ──────────────────────────── Group E: scan_and_trade ────────────────────────────


class TestScanAndTrade:

    @pytest.mark.asyncio
    async def test_no_markets_returns_early(self):
        engine = _make_engine()
        engine.get_markets = AsyncMock(return_value=[])
        bot = _make_bot(engine)
        mock_detector = MagicMock()
        mock_detector.init = AsyncMock()
        mock_detector.scan_for_opportunities = AsyncMock(return_value=[])
        mock_detector.get_cache_stats = MagicMock(return_value={})
        bot._detector = mock_detector
        await bot.scan_and_trade()
        mock_detector.scan_for_opportunities.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_opportunities_returns_early(self):
        engine = _make_engine()
        markets = [_make_market("m1"), _make_market("m2")]
        engine.get_markets = AsyncMock(return_value=markets)
        bot = _make_bot(engine)
        mock_detector = MagicMock()
        mock_detector.init = AsyncMock()
        mock_detector.scan_for_opportunities = AsyncMock(return_value=[])
        mock_detector.get_cache_stats = MagicMock(return_value={})
        bot._detector = mock_detector
        await bot.scan_and_trade()
        mock_detector.scan_for_opportunities.assert_called_once()

    @pytest.mark.asyncio
    async def test_caps_at_max_opps_per_scan(self):
        """Even with 5 opportunities, only MAX_OPPS_PER_SCAN (3) are executed."""
        engine = _make_engine()
        markets = [_make_market(f"m{i}", yes_price=0.6, yes_token_id=f"tok-{i}") for i in range(5)]
        engine.get_markets = AsyncMock(return_value=markets)

        opps = [
            {"type": "mutual_exclusivity", "markets": [f"m{i}"], "prices": [0.6], "spread": 0.05}
            for i in range(5)
        ]
        mock_detector = MagicMock()
        mock_detector.init = AsyncMock()
        mock_detector.scan_for_opportunities = AsyncMock(return_value=opps)
        mock_detector.get_cache_stats = MagicMock(return_value={})

        bot = _make_bot(engine)
        bot._detector = mock_detector

        # Mock _execute_logical_arb to always succeed
        bot._execute_logical_arb = AsyncMock(return_value=True)
        await bot.scan_and_trade()

        assert bot._execute_logical_arb.call_count == MAX_OPPS_PER_SCAN

    @pytest.mark.asyncio
    async def test_market_cache_populated(self):
        engine = _make_engine()
        m1 = _make_market("m1")
        m2 = _make_market("m2")
        engine.get_markets = AsyncMock(return_value=[m1, m2])

        mock_detector = MagicMock()
        mock_detector.init = AsyncMock()
        mock_detector.scan_for_opportunities = AsyncMock(return_value=[])
        mock_detector.get_cache_stats = MagicMock(return_value={})

        bot = _make_bot(engine)
        bot._detector = mock_detector
        await bot.scan_and_trade()

        assert "m1" in bot._market_cache
        assert "m2" in bot._market_cache

    @pytest.mark.asyncio
    async def test_market_fetch_failure_returns_gracefully(self):
        engine = _make_engine()
        engine.get_markets = AsyncMock(side_effect=RuntimeError("API down"))
        bot = _make_bot(engine)
        mock_detector = MagicMock()
        mock_detector.init = AsyncMock()
        mock_detector.get_cache_stats = MagicMock(return_value={})
        bot._detector = mock_detector
        # Should not raise
        await bot.scan_and_trade()


# ──────────────────────────── Group F: _execute_mutual_exclusivity ────────────────────────────


class TestExecuteMutualExclusivity:

    @pytest.mark.asyncio
    async def test_places_no_order_on_most_overpriced(self):
        engine = _make_engine()
        bot = _make_bot(engine)
        # Populate market cache
        bot._market_cache = {
            "m1": _make_market("m1", yes_price=0.40, yes_token_id="tok-m1"),
            "m2": _make_market("m2", yes_price=0.70, yes_token_id="tok-m2"),
        }

        opp = {
            "type": "mutual_exclusivity",
            "markets": ["m1", "m2"],
            "prices": [0.40, 0.70],
            "spread": 0.10,
        }
        result = await bot._execute_mutual_exclusivity(opp)
        assert result is True

        # Verify place_order was called on the engine (via base_bot.place_order -> engine.place_order)
        engine.place_order.assert_called_once()
        call_kwargs = engine.place_order.call_args
        # It should target market m2 (highest price) with side=NO
        assert call_kwargs.kwargs.get("market_id") or call_kwargs[1].get("market_id", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None)
        # Check keyword arguments
        kw = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kw.get("side") == "NO"
        assert kw.get("market_id") == "m2"

    @pytest.mark.asyncio
    async def test_risk_blocked_returns_false(self):
        engine = _make_engine()
        engine.risk_manager.check_risk_limits = AsyncMock(
            return_value={"allowed": False, "reasons": ["max exposure"]}
        )
        bot = _make_bot(engine)
        bot._market_cache = {
            "m1": _make_market("m1", yes_price=0.60, yes_token_id="tok-m1"),
        }
        opp = {
            "type": "mutual_exclusivity",
            "markets": ["m1"],
            "prices": [0.60],
            "spread": 0.05,
        }
        result = await bot._execute_mutual_exclusivity(opp)
        assert result is False
        engine.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_market_in_cache_returns_false(self):
        bot = _make_bot()
        bot._market_cache = {}  # empty cache
        opp = {
            "type": "mutual_exclusivity",
            "markets": ["m_missing"],
            "prices": [0.60],
            "spread": 0.05,
        }
        result = await bot._execute_mutual_exclusivity(opp)
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_markets_list_returns_false(self):
        bot = _make_bot()
        opp = {"type": "mutual_exclusivity", "markets": [], "prices": [], "spread": 0.05}
        result = await bot._execute_mutual_exclusivity(opp)
        assert result is False

    @pytest.mark.asyncio
    async def test_size_capped_at_max_position(self):
        engine = _make_engine()
        # Risk manager returns a huge position size
        engine.risk_manager.calculate_position_size = AsyncMock(return_value=9999.0)
        bot = _make_bot(engine, max_position=100)
        bot._market_cache = {
            "m1": _make_market("m1", yes_price=0.60, yes_token_id="tok-m1"),
        }
        opp = {
            "type": "mutual_exclusivity",
            "markets": ["m1"],
            "prices": [0.60],
            "spread": 0.10,
        }
        result = await bot._execute_mutual_exclusivity(opp)
        assert result is True
        # Size passed to place_order should be <= 100
        kw = engine.place_order.call_args.kwargs
        assert kw.get("size") <= 100.0

    @pytest.mark.asyncio
    async def test_no_token_id_returns_false(self):
        engine = _make_engine()
        bot = _make_bot(engine)
        # Market with no tokens at all
        bot._market_cache = {
            "m1": {"id": "m1", "question": "Test?"},
        }
        opp = {
            "type": "mutual_exclusivity",
            "markets": ["m1"],
            "prices": [0.60],
            "spread": 0.05,
        }
        result = await bot._execute_mutual_exclusivity(opp)
        assert result is False


# ──────────────────────────── Group G: _execute_subset_violation ────────────────────────────


class TestExecuteSubsetViolation:

    @pytest.mark.asyncio
    async def test_two_leg_trade_placed(self):
        """Leg 1: NO on subset, Leg 2: YES on superset."""
        engine = _make_engine()
        bot = _make_bot(engine)
        bot._market_cache = {
            "sub1": _make_market("sub1", yes_price=0.70, yes_token_id="tok-sub1"),
            "sup1": _make_market("sup1", yes_price=0.55, yes_token_id="tok-sup1"),
        }
        opp = {
            "type": "subset_violation",
            "subset_market": "sub1",
            "superset_market": "sup1",
            "subset_price": 0.70,
            "superset_price": 0.55,
            "spread": 0.15,
        }
        result = await bot._execute_subset_violation(opp)
        assert result is True
        # Two place_order calls
        assert engine.place_order.call_count == 2
        # First call: NO on subset
        kw1 = engine.place_order.call_args_list[0].kwargs
        assert kw1["market_id"] == "sub1"
        assert kw1["side"] == "NO"
        # Second call: YES on superset
        kw2 = engine.place_order.call_args_list[1].kwargs
        assert kw2["market_id"] == "sup1"
        assert kw2["side"] == "YES"

    @pytest.mark.asyncio
    async def test_risk_blocked_on_first_leg(self):
        engine = _make_engine()
        engine.risk_manager.check_risk_limits = AsyncMock(
            return_value={"allowed": False, "reasons": ["blocked"]}
        )
        bot = _make_bot(engine)
        bot._market_cache = {
            "sub1": _make_market("sub1", yes_token_id="tok-sub1"),
            "sup1": _make_market("sup1", yes_token_id="tok-sup1"),
        }
        opp = {
            "type": "subset_violation",
            "subset_market": "sub1",
            "superset_market": "sup1",
            "subset_price": 0.70,
            "superset_price": 0.55,
            "spread": 0.15,
        }
        result = await bot._execute_subset_violation(opp)
        assert result is False
        engine.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_leg1_failure_stops_execution(self):
        engine = _make_engine()
        engine.place_order = AsyncMock(return_value={"success": False, "error": "rejected"})
        bot = _make_bot(engine)
        bot._market_cache = {
            "sub1": _make_market("sub1", yes_token_id="tok-sub1"),
            "sup1": _make_market("sup1", yes_token_id="tok-sup1"),
        }
        opp = {
            "type": "subset_violation",
            "subset_market": "sub1",
            "superset_market": "sup1",
            "subset_price": 0.70,
            "superset_price": 0.55,
            "spread": 0.15,
        }
        result = await bot._execute_subset_violation(opp)
        assert result is False
        # Only leg 1 attempted
        assert engine.place_order.call_count == 1

    @pytest.mark.asyncio
    async def test_missing_subset_market_returns_false(self):
        bot = _make_bot()
        bot._market_cache = {
            "sup1": _make_market("sup1", yes_token_id="tok-sup1"),
        }
        opp = {
            "type": "subset_violation",
            "subset_market": "sub_missing",
            "superset_market": "sup1",
            "subset_price": 0.70,
            "superset_price": 0.55,
            "spread": 0.15,
        }
        result = await bot._execute_subset_violation(opp)
        assert result is False


# ──────────────────────────── Group H: _execute_complement_violation ────────────────────────────


class TestExecuteComplementViolation:

    @pytest.mark.asyncio
    async def test_sum_less_than_1_buys_yes_on_both(self):
        """When sum < 1.0, buy YES on both markets."""
        engine = _make_engine()
        bot = _make_bot(engine)
        bot._market_cache = {
            "a1": _make_market("a1", yes_price=0.30, yes_token_id="tok-a1"),
            "b1": _make_market("b1", yes_price=0.40, yes_token_id="tok-b1"),
        }
        opp = {
            "type": "complement_violation",
            "market_a": "a1",
            "market_b": "b1",
            "price_a": 0.30,
            "price_b": 0.40,
            "sum": 0.70,  # < 1.0
            "spread": 0.30,
        }
        result = await bot._execute_complement_violation(opp)
        assert result is True
        assert engine.place_order.call_count == 2
        kw1 = engine.place_order.call_args_list[0].kwargs
        kw2 = engine.place_order.call_args_list[1].kwargs
        assert kw1["side"] == "YES"
        assert kw2["side"] == "YES"

    @pytest.mark.asyncio
    async def test_sum_greater_than_1_sells_both(self):
        """When sum > 1.0, sell YES (side=NO) on both markets."""
        engine = _make_engine()
        bot = _make_bot(engine)
        bot._market_cache = {
            "a1": _make_market("a1", yes_price=0.60, yes_token_id="tok-a1"),
            "b1": _make_market("b1", yes_price=0.55, yes_token_id="tok-b1"),
        }
        opp = {
            "type": "complement_violation",
            "market_a": "a1",
            "market_b": "b1",
            "price_a": 0.60,
            "price_b": 0.55,
            "sum": 1.15,  # > 1.0
            "spread": 0.15,
        }
        result = await bot._execute_complement_violation(opp)
        assert result is True
        assert engine.place_order.call_count == 2
        kw1 = engine.place_order.call_args_list[0].kwargs
        kw2 = engine.place_order.call_args_list[1].kwargs
        assert kw1["side"] == "NO"
        assert kw2["side"] == "NO"

    @pytest.mark.asyncio
    async def test_risk_blocked_returns_false(self):
        engine = _make_engine()
        engine.risk_manager.check_risk_limits = AsyncMock(
            return_value={"allowed": False, "reasons": ["limit hit"]}
        )
        bot = _make_bot(engine)
        bot._market_cache = {
            "a1": _make_market("a1", yes_token_id="tok-a1"),
            "b1": _make_market("b1", yes_token_id="tok-b1"),
        }
        opp = {
            "type": "complement_violation",
            "market_a": "a1",
            "market_b": "b1",
            "price_a": 0.60,
            "price_b": 0.55,
            "sum": 1.15,
            "spread": 0.15,
        }
        result = await bot._execute_complement_violation(opp)
        assert result is False
        engine.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_market_a_returns_false(self):
        bot = _make_bot()
        bot._market_cache = {
            "b1": _make_market("b1", yes_token_id="tok-b1"),
        }
        opp = {
            "type": "complement_violation",
            "market_a": "a_missing",
            "market_b": "b1",
            "price_a": 0.30,
            "price_b": 0.40,
            "sum": 0.70,
            "spread": 0.30,
        }
        result = await bot._execute_complement_violation(opp)
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_success_returns_true(self):
        """If one leg succeeds but the other fails, still returns True."""
        engine = _make_engine()
        # First call succeeds, second fails
        engine.place_order = AsyncMock(
            side_effect=[
                {"success": True},
                {"success": False, "error": "rejected"},
            ]
        )
        bot = _make_bot(engine)
        bot._market_cache = {
            "a1": _make_market("a1", yes_token_id="tok-a1"),
            "b1": _make_market("b1", yes_token_id="tok-b1"),
        }
        opp = {
            "type": "complement_violation",
            "market_a": "a1",
            "market_b": "b1",
            "price_a": 0.30,
            "price_b": 0.40,
            "sum": 0.70,
            "spread": 0.30,
        }
        result = await bot._execute_complement_violation(opp)
        assert result is True

    @pytest.mark.asyncio
    async def test_both_legs_fail_returns_false(self):
        engine = _make_engine()
        engine.place_order = AsyncMock(return_value={"success": False, "error": "rejected"})
        bot = _make_bot(engine)
        bot._market_cache = {
            "a1": _make_market("a1", yes_token_id="tok-a1"),
            "b1": _make_market("b1", yes_token_id="tok-b1"),
        }
        opp = {
            "type": "complement_violation",
            "market_a": "a1",
            "market_b": "b1",
            "price_a": 0.30,
            "price_b": 0.40,
            "sum": 0.70,
            "spread": 0.30,
        }
        result = await bot._execute_complement_violation(opp)
        assert result is False


# ──────────────────────────── Group I: _execute_logical_arb routing ────────────────────────────


class TestExecuteLogicalArbRouting:

    @pytest.mark.asyncio
    async def test_routes_mutual_exclusivity(self):
        bot = _make_bot()
        bot._execute_mutual_exclusivity = AsyncMock(return_value=True)
        result = await bot._execute_logical_arb({"type": "mutual_exclusivity"})
        assert result is True
        bot._execute_mutual_exclusivity.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_subset_violation(self):
        bot = _make_bot()
        bot._execute_subset_violation = AsyncMock(return_value=True)
        result = await bot._execute_logical_arb({"type": "subset_violation"})
        assert result is True
        bot._execute_subset_violation.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_complement_violation(self):
        bot = _make_bot()
        bot._execute_complement_violation = AsyncMock(return_value=True)
        result = await bot._execute_logical_arb({"type": "complement_violation"})
        assert result is True
        bot._execute_complement_violation.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_type_returns_false(self):
        bot = _make_bot()
        result = await bot._execute_logical_arb({"type": "unknown_type"})
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_type_returns_false(self):
        bot = _make_bot()
        result = await bot._execute_logical_arb({})
        assert result is False


# ──────────────────────────── Group J: MAX_OPPS_PER_SCAN constant ────────────────────────────


class TestConstants:

    def test_max_opps_per_scan_is_3(self):
        assert MAX_OPPS_PER_SCAN == 3
