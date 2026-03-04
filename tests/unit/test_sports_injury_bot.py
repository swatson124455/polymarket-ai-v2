"""
Unit tests for bots/sports_injury_bot.py

Tests:
  - Queue drain (max 10 per scan)
  - Edge gate (below SPORTS_MIN_EDGE → skip)
  - Confidence gate (below SPORTS_MIN_CONFIDENCE → skip)
  - place_order called on valid event
  - NFL offseason free_agent_move handling
  - SPORT_IMPACT_TABLE coverage
  - enqueue_injury_event() put_nowait behavior
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sports.data.injury_store import InjuryEvent
from bots.sports_injury_bot import SportsInjuryBot, SPORT_IMPACT_TABLE


def make_bot():
    """Create a SportsInjuryBot with mocked base_engine."""
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
    bot = SportsInjuryBot(base_engine)
    bot._scanner = AsyncMock()
    bot._bankroll_mgr = AsyncMock()
    bot._current_correlation_id = "test-corr-id"
    return bot


def make_injury_event(
    sport="nba",
    status="out",
    confidence=0.92,
    player_raw="LeBron James",
    game_id=None,
) -> InjuryEvent:
    return InjuryEvent(
        player_raw=player_raw,
        sport=sport,
        detected_status=status,
        confidence=confidence,
        source="twitter",
        raw_text=f"{player_raw} {status}",
    )


class TestSportImpactTable:
    """SPORT_IMPACT_TABLE sanity checks."""

    def test_all_sports_present(self):
        for sport in ("nba", "nfl", "mlb", "nhl", "soccer", "tennis", "unknown"):
            assert sport in SPORT_IMPACT_TABLE, f"Missing sport: {sport}"

    def test_nba_out_edge(self):
        assert SPORT_IMPACT_TABLE["nba"]["out"] == 0.10

    def test_nfl_free_agent_move(self):
        assert SPORT_IMPACT_TABLE["nfl"]["free_agent_move"] == 0.08

    def test_mlb_sp_scratch_highest(self):
        assert SPORT_IMPACT_TABLE["mlb"]["sp_scratch"] == 0.15

    def test_tennis_withdrawal_full(self):
        assert SPORT_IMPACT_TABLE["tennis"]["withdrawal"] == 1.0

    def test_nhl_goalie_swap(self):
        assert SPORT_IMPACT_TABLE["nhl"]["goalie_swap"] == 0.12


class TestQueueDrain:
    """Tests for scan_and_trade queue drain behavior."""

    @pytest.mark.asyncio
    async def test_drains_up_to_10_events(self):
        bot = make_bot()
        bot._process_injury_event = AsyncMock()

        # Put 15 events in queue
        for i in range(15):
            event = make_injury_event()
            bot._injury_queue.put_nowait(event)

        await bot.scan_and_trade()

        # Should have processed exactly 10
        assert bot._process_injury_event.call_count == 10

    @pytest.mark.asyncio
    async def test_empty_queue_does_not_block(self):
        bot = make_bot()
        bot._process_injury_event = AsyncMock()
        # No events in queue — should return immediately
        await bot.scan_and_trade()
        bot._process_injury_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_drains_fewer_than_10_if_queue_small(self):
        bot = make_bot()
        bot._process_injury_event = AsyncMock()
        for i in range(3):
            bot._injury_queue.put_nowait(make_injury_event())
        await bot.scan_and_trade()
        assert bot._process_injury_event.call_count == 3


class TestEdgeAndConfidenceGates:
    """Tests for _process_injury_event gate logic."""

    @pytest.mark.asyncio
    async def test_zero_edge_skips_bet(self):
        bot = make_bot()
        bot.place_order = AsyncMock()
        # Status not in NBA table (returns 0.0 from unknown fallback here via "nba")
        # Use a status with 0 edge
        event = make_injury_event(sport="nba", status="sp_scratch", confidence=0.95)
        # nba sp_scratch = 0.0
        await bot._process_injury_event(event)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_min_edge_skips(self):
        bot = make_bot()
        bot.place_order = AsyncMock()
        # NBA day-to-day = 0.02 < default min_edge=0.05
        event = make_injury_event(sport="nba", status="day-to-day", confidence=0.95)
        with patch("bots.sports_injury_bot.settings") as mock_settings:
            mock_settings.SPORTS_MIN_EDGE = 0.05
            mock_settings.SPORTS_MIN_CONFIDENCE = 0.60
            await bot._process_injury_event(event)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_min_confidence_skips(self):
        bot = make_bot()
        bot.place_order = AsyncMock()
        event = make_injury_event(sport="nba", status="out", confidence=0.40)
        with patch("bots.sports_injury_bot.settings") as mock_settings:
            mock_settings.SPORTS_MIN_EDGE = 0.05
            mock_settings.SPORTS_MIN_CONFIDENCE = 0.60
            await bot._process_injury_event(event)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_event_calls_scanner(self):
        bot = make_bot()
        bot.place_order = AsyncMock(return_value={"success": True, "trade_id": "t1"})
        bot._scanner.find_markets_for_game = AsyncMock(return_value=[])
        bot._bankroll_mgr.get_bet_size = AsyncMock(return_value=50.0)

        event = make_injury_event(sport="nba", status="out", confidence=0.92)
        with patch("bots.sports_injury_bot.settings") as mock_settings:
            mock_settings.SPORTS_MIN_EDGE = 0.05
            mock_settings.SPORTS_MIN_CONFIDENCE = 0.60
            await bot._process_injury_event(event)

        bot._scanner.find_markets_for_game.assert_called_once()


class TestBetPlacement:
    """Tests for place_order invocation."""

    @pytest.mark.asyncio
    async def test_place_order_called_with_valid_market(self):
        from sports.markets.kalshi_client import SportsMarketCandidate
        bot = make_bot()
        bot.place_order = AsyncMock(return_value={"success": True, "trade_id": "t1"})

        market = SportsMarketCandidate(
            platform="polymarket",
            market_id="mkt123",
            market_type="moneyline",
            sport="nba",
            yes_token_id="tok123",
            no_token_id=None,
            current_price=0.60,
            title="Lakers to win",
        )
        bot._scanner.find_markets_for_game = AsyncMock(return_value=[market])
        bot._bankroll_mgr.get_bet_size = AsyncMock(return_value=75.0)

        event = make_injury_event(sport="nba", status="out", confidence=0.92)
        with patch("bots.sports_injury_bot.settings") as mock_settings:
            mock_settings.SPORTS_MIN_EDGE = 0.05
            mock_settings.SPORTS_MIN_CONFIDENCE = 0.60
            await bot._process_injury_event(event)

        bot.place_order.assert_called_once()
        call_kwargs = bot.place_order.call_args[1]
        assert call_kwargs["market_id"] == "mkt123"
        assert call_kwargs["size"] == 75.0
        assert call_kwargs["confidence"] == 0.92

    @pytest.mark.asyncio
    async def test_zero_size_skips_place_order(self):
        from sports.markets.kalshi_client import SportsMarketCandidate
        bot = make_bot()
        bot.place_order = AsyncMock()

        market = SportsMarketCandidate(
            platform="polymarket", market_id="mkt456", market_type="moneyline",
            sport="nba", yes_token_id="tok456", no_token_id=None,
            current_price=0.55, title="Test market",
        )
        bot._scanner.find_markets_for_game = AsyncMock(return_value=[market])
        bot._bankroll_mgr.get_bet_size = AsyncMock(return_value=0.0)  # zero size

        event = make_injury_event(sport="nba", status="out", confidence=0.92)
        with patch("bots.sports_injury_bot.settings") as mock_settings:
            mock_settings.SPORTS_MIN_EDGE = 0.05
            mock_settings.SPORTS_MIN_CONFIDENCE = 0.60
            await bot._process_injury_event(event)

        bot.place_order.assert_not_called()


class TestEnqueueInjuryEvent:
    """Tests for enqueue_injury_event()."""

    def test_enqueues_event(self):
        bot = make_bot()
        event = make_injury_event()
        bot.enqueue_injury_event(event)
        assert bot._injury_queue.qsize() == 1

    def test_queue_full_does_not_raise(self):
        bot = make_bot()
        # Fill queue to max
        for _ in range(500):
            bot._injury_queue.put_nowait(make_injury_event())
        # This should log warning, not raise
        bot.enqueue_injury_event(make_injury_event())  # 501st — should be dropped
