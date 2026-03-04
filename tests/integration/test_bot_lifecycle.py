"""
Integration tests for bot lifecycle and price event wiring.

Tests the BaseBot on_price_update mechanism, scan_loop behavior,
and SportsBot market type classification.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestBaseBotPriceCache:
    """Test that on_price_update caches WS prices for use by scan_and_trade."""

    @pytest.mark.asyncio
    async def test_on_price_update_caches_price(self, mock_base_engine):
        from bots.base_bot import BaseBot

        class DummyBot(BaseBot):
            async def scan_and_trade(self):
                pass

            async def analyze_opportunity(self, market_data):
                return None

        bot = DummyBot("TestBot", mock_base_engine)

        await bot.on_price_update({
            "market_id": "0xabc",
            "token_id": "tok1",
            "price": 0.72,
            "timestamp": "2026-02-10T12:00:00Z",
        })

        assert bot.get_ws_price("0xabc") == 0.72
        assert bot.get_ws_price("nonexistent") is None

    @pytest.mark.asyncio
    async def test_on_price_update_ignores_invalid(self, mock_base_engine):
        from bots.base_bot import BaseBot

        class DummyBot(BaseBot):
            async def scan_and_trade(self):
                pass

            async def analyze_opportunity(self, market_data):
                return None

        bot = DummyBot("TestBot", mock_base_engine)

        # Missing market_id
        await bot.on_price_update({"price": 0.5})
        assert bot.get_ws_price("") is None

        # Missing price
        await bot.on_price_update({"market_id": "m1"})
        assert bot.get_ws_price("m1") is None


class TestSportsBotMarketType:
    """Test SportsBot._parse_market_type classification."""

    def test_outcome_market(self):
        from bots.sports_bot import SportsBot
        assert SportsBot._parse_market_type("Will Manchester United win?") == "outcome"

    def test_total_market(self):
        from bots.sports_bot import SportsBot
        assert SportsBot._parse_market_type("Over 2.5 goals in the match?") == "total"
        assert SportsBot._parse_market_type("Will the score be under 45.5?") == "total"

    def test_spread_market(self):
        from bots.sports_bot import SportsBot
        assert SportsBot._parse_market_type("Team A wins by 3+ spread") == "spread"
        assert SportsBot._parse_market_type("Handicap: Team B +1.5") == "spread"

    def test_draw_market(self):
        from bots.sports_bot import SportsBot
        assert SportsBot._parse_market_type("Will the match end in a draw?") == "draw"
        assert SportsBot._parse_market_type("Will there be a tie?") == "draw"

    def test_prop_market(self):
        from bots.sports_bot import SportsBot
        assert SportsBot._parse_market_type("Will Player X score first goal scorer?") == "prop"
        assert SportsBot._parse_market_type("MVP of the tournament?") == "prop"


class TestSportsBotSignalIntegration:
    """Test that SportsBot live game analysis calls apply_signal_enhancements."""

    @pytest.mark.asyncio
    async def test_live_game_applies_signals(self, mock_base_engine):
        from bots.sports_bot import SportsBot

        bot = SportsBot(mock_base_engine)
        # Mock signal enhancement to boost confidence
        bot.apply_signal_enhancements = AsyncMock(return_value=0.88)

        game_state = {
            "score_home": 3,
            "score_away": 0,
            "elapsed_pct": 85,
        }
        market_data = {"question": "Will Team A win the match?"}

        result = await bot._analyze_live_game(
            "0xmarket1", "tok1", 0.60, game_state, market_data
        )

        # Should have called signal enhancements
        assert bot.apply_signal_enhancements.called
        assert result is not None
        assert result["confidence"] == 0.88  # from mock
        assert result["market_type"] == "outcome"

    @pytest.mark.asyncio
    async def test_live_game_too_early_returns_none(self, mock_base_engine):
        from bots.sports_bot import SportsBot

        bot = SportsBot(mock_base_engine)
        game_state = {"score_home": 0, "score_away": 0, "elapsed_pct": 5}

        result = await bot._analyze_live_game(
            "0xmarket1", "tok1", 0.50, game_state, {"question": "Team wins?"}
        )
        assert result is None


class TestBaseBotHelpers:
    """Test BaseBot utility methods."""

    def test_validate_price_valid(self):
        from bots.base_bot import BaseBot
        assert BaseBot.validate_price(0.5) == 0.5
        assert BaseBot.validate_price("0.99") == 0.99
        assert BaseBot.validate_price(1.0) == 1.0

    def test_validate_price_invalid(self):
        from bots.base_bot import BaseBot
        assert BaseBot.validate_price(0) is None
        assert BaseBot.validate_price(-0.1) is None
        assert BaseBot.validate_price(1.1) is None
        assert BaseBot.validate_price("not_a_number") is None
        assert BaseBot.validate_price(float("nan")) is None
        assert BaseBot.validate_price(float("inf")) is None
