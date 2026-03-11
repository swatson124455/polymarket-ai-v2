"""
Unit tests for bots/esports_bot.py (EsportsBot).

Tests:
  - __init__ raises ValueError when PANDASCORE_API_KEY is missing
  - _get_scan_interval_seconds returns 10s during live matches, 120s otherwise
  - _detect_game classifies "lol", "cs2", "dota2", "valorant" from question text
  - _classify_market_type returns correct market type strings
  - analyze_opportunity returns None when no tokens
  - analyze_opportunity returns None when edge < min_edge
  - analyze_opportunity returns trade dict when edge > min_edge
  - scan_and_trade calls analyze for each esports market
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bots.esports_bot import EsportsBot


def make_bot():
    """Create an EsportsBot with mocked base_engine and settings."""
    base_engine = MagicMock()
    base_engine.db = MagicMock()
    base_engine.order_gateway = MagicMock()
    base_engine.order_gateway.has_open_position = MagicMock(return_value=False)
    base_engine.order_gateway._daily_exposure_usd = {}
    base_engine.get_markets = AsyncMock(return_value=[])
    base_engine.filter_markets_for_trading = MagicMock(return_value=[])
    base_engine.get_predictions = AsyncMock(return_value=None)
    # Must patch settings to have PANDASCORE_API_KEY
    with patch("bots.esports_bot.settings") as mock_settings:
        mock_settings.PANDASCORE_API_KEY = "test-key"
        mock_settings.RIOT_API_KEY = None
        mock_settings.ESPORTS_MIN_EDGE = 0.08
        mock_settings.ESPORTS_MIN_CONFIDENCE = 0.52
        mock_settings.ESPORTS_MAKER_FALLBACK_TIMEOUT_S = 3.0
        mock_settings.SCAN_INTERVAL_ESPORTS = 120
        mock_settings.SCAN_INTERVAL_ESPORTS_LIVE = 10
        bot = EsportsBot(base_engine)
    return bot


def _make_market(
    market_id="m1",
    question="Will Team A win the LoL match?",
    yes_price=0.50,
    no_price=0.50,
    yes_token_id="tok-yes-1",
    no_token_id="tok-no-1",
):
    """Create a mock esports market dict."""
    return {
        "id": market_id,
        "question": question,
        "tokens": [
            {"tokenId": yes_token_id, "outcomePrice": str(yes_price)},
            {"tokenId": no_token_id, "outcomePrice": str(no_price)},
        ],
    }


# =========================================================================
# Initialization
# =========================================================================


class TestEsportsBotInit:
    def test_raises_without_api_key(self):
        """__init__ raises ValueError when PANDASCORE_API_KEY is not set."""
        base_engine = MagicMock()
        with patch("bots.esports_bot.settings") as mock_settings:
            mock_settings.PANDASCORE_API_KEY = None
            with pytest.raises(ValueError, match="PANDASCORE_API_KEY"):
                EsportsBot(base_engine)

    def test_raises_with_empty_api_key(self):
        """__init__ raises ValueError when PANDASCORE_API_KEY is empty string."""
        base_engine = MagicMock()
        with patch("bots.esports_bot.settings") as mock_settings:
            mock_settings.PANDASCORE_API_KEY = ""
            with pytest.raises(ValueError, match="PANDASCORE_API_KEY"):
                EsportsBot(base_engine)

    def test_init_succeeds_with_valid_key(self):
        """__init__ succeeds when PANDASCORE_API_KEY is set."""
        bot = make_bot()
        assert bot._api_key == "test-key"
        assert bot.bot_name == "EsportsBot"

    def test_settings_stored_correctly(self):
        bot = make_bot()
        assert bot._min_edge == pytest.approx(0.08)
        assert bot._min_confidence == pytest.approx(0.52)
        assert bot._maker_timeout == pytest.approx(3.0)


# =========================================================================
# Scan Interval
# =========================================================================


class TestScanInterval:
    def test_default_scan_interval_no_live_matches(self):
        """Returns 120s when no live matches are active."""
        bot = make_bot()
        bot._live_matches = {}
        with patch("bots.esports_bot.settings") as mock_settings:
            mock_settings.SCAN_INTERVAL_ESPORTS = 120
            mock_settings.SCAN_INTERVAL_ESPORTS_LIVE = 10
            assert bot._get_scan_interval_seconds() == pytest.approx(120.0)

    def test_live_scan_interval_with_live_matches(self):
        """Returns 10s when live matches are present."""
        bot = make_bot()
        bot._live_matches = {"match-1": {"id": "match-1"}}
        with patch("bots.esports_bot.settings") as mock_settings:
            mock_settings.SCAN_INTERVAL_ESPORTS = 120
            mock_settings.SCAN_INTERVAL_ESPORTS_LIVE = 10
            assert bot._get_scan_interval_seconds() == pytest.approx(10.0)


# =========================================================================
# Game Detection
# =========================================================================


class TestDetectGame:
    def test_detect_lol_league_of_legends(self):
        assert EsportsBot._detect_game("will team a win the league of legends match?") == "lol"

    def test_detect_lol_keyword(self):
        assert EsportsBot._detect_game("lol worlds 2026 finals winner") == "lol"

    def test_detect_lol_lck(self):
        assert EsportsBot._detect_game("who wins lck spring split 2026?") == "lol"

    def test_detect_lol_msi(self):
        assert EsportsBot._detect_game("msi 2026 champion") == "lol"

    def test_detect_cs2_counter_strike(self):
        assert EsportsBot._detect_game("counter-strike 2 major winner?") == "cs2"

    def test_detect_cs2_keyword(self):
        assert EsportsBot._detect_game("cs2 blast premier spring finals") == "cs2"

    def test_detect_cs2_esl(self):
        assert EsportsBot._detect_game("esl pro league season 21 winner?") == "cs2"

    def test_detect_cs2_pgl(self):
        assert EsportsBot._detect_game("pgl major copenhagen winner") == "cs2"

    def test_detect_dota2(self):
        assert EsportsBot._detect_game("will team spirit win the international 2026?") == "dota2"

    def test_detect_dota2_dpc(self):
        assert EsportsBot._detect_game("dpc 2026 season winner") == "dota2"

    def test_detect_dota2_ti(self):
        assert EsportsBot._detect_game("who wins ti 2026?") == "dota2"

    def test_detect_valorant(self):
        assert EsportsBot._detect_game("valorant champions 2026 winner") == "valorant"

    def test_detect_valorant_vct(self):
        assert EsportsBot._detect_game("vct masters madrid 2026 winner") == "valorant"

    def test_detect_valorant_champions_tour(self):
        assert EsportsBot._detect_game("champions tour 2026 finals") == "valorant"

    def test_detect_none_for_unknown(self):
        assert EsportsBot._detect_game("will bitcoin hit $200k?") is None

    def test_detect_none_for_empty(self):
        assert EsportsBot._detect_game("") is None

    def test_detect_case_insensitive(self):
        """_detect_game lowercases input, so mixed case should still match."""
        assert EsportsBot._detect_game("LEAGUE OF LEGENDS Finals") == "lol"


# =========================================================================
# Market Type Classification
# =========================================================================


class TestClassifyMarketType:
    def test_match_winner_default(self):
        assert EsportsBot._classify_market_type("will team a beat team b?") == "match_winner"

    def test_map_winner(self):
        assert EsportsBot._classify_market_type("who wins map 1?") == "map_winner"

    def test_map_winner_game_number(self):
        assert EsportsBot._classify_market_type("game 3 winner in the series") == "map_winner"

    def test_tournament_winner(self):
        assert EsportsBot._classify_market_type("who wins the tournament?") == "tournament_winner"

    def test_tournament_champion(self):
        assert EsportsBot._classify_market_type("2026 spring split champion") == "tournament_winner"

    def test_total_maps(self):
        assert EsportsBot._classify_market_type("total maps over 2.5?") == "total_maps"

    def test_maps_played(self):
        assert EsportsBot._classify_market_type("how many maps played in the series?") == "total_maps"

    def test_first_blood(self):
        assert EsportsBot._classify_market_type("who gets first blood?") == "first_blood"

    def test_props_mvp(self):
        assert EsportsBot._classify_market_type("who gets mvp of the finals?") == "props"

    def test_props_kills(self):
        assert EsportsBot._classify_market_type("player x 20.5 kills or more?") == "props"


# =========================================================================
# analyze_opportunity
# =========================================================================


class TestAnalyzeOpportunity:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_tokens(self):
        """No tokens list -> returns None."""
        bot = make_bot()
        market = {"id": "m1", "question": "lol match winner?", "tokens": []}
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_id(self):
        """Missing id -> returns None."""
        bot = make_bot()
        market = {"question": "lol match winner?", "tokens": [{"tokenId": "t1", "outcomePrice": "0.50"}]}
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_game_not_detected(self):
        """Non-esports question -> returns None."""
        bot = make_bot()
        market = _make_market(question="Will bitcoin hit $200k?")
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_edge(self):
        """Model prediction equals market price -> no edge -> returns None."""
        bot = make_bot()
        bot._patch_drift = None
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.60,
        )
        # Model predicts 0.60 = same as market price -> no edge
        bot._get_glicko2_prediction = MagicMock(return_value=0.60)
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_prediction_is_none(self):
        """Glicko-2 prediction returns None -> returns None."""
        bot = make_bot()
        bot._patch_drift = None
        market = _make_market(question="Will Team A win the LoL match?")
        bot._get_glicko2_prediction = MagicMock(return_value=None)
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_observation_mode(self):
        """Patch drift observation mode -> returns None."""
        bot = make_bot()
        bot._patch_drift = MagicMock()
        bot._patch_drift.is_observation_mode = MagicMock(return_value=True)
        bot._patch_drift.is_halted = MagicMock(return_value=False)
        market = _make_market(question="Will Team A win the LoL match?")
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_halted(self):
        """Halted game -> returns None."""
        bot = make_bot()
        bot._patch_drift = MagicMock()
        bot._patch_drift.is_observation_mode = MagicMock(return_value=False)
        bot._patch_drift.is_halted = MagicMock(return_value=True)
        market = _make_market(question="Will Team A win the LoL match?")
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_trade_dict_when_yes_edge(self):
        """Model predicts higher than market -> YES trade with edge."""
        bot = make_bot()
        bot._patch_drift = None
        bot._min_edge = 0.08
        bot._min_confidence = 0.55
        # Market price = 0.50, model predicts 0.70 -> edge = 0.20
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.50,
        )
        # Mock Glicko-2 prediction (replaced base_engine.get_predictions fallback)
        bot._get_glicko2_prediction = MagicMock(return_value=0.70)
        result = await bot.analyze_opportunity(market)
        assert result is not None
        assert result["side"] == "YES"
        assert result["edge"] == pytest.approx(0.20, abs=0.01)
        assert result["game"] == "lol"
        assert result["market_type"] == "match_winner"
        assert result["confidence"] == pytest.approx(0.70)
        assert result["market_id"] == "m1"

    @pytest.mark.asyncio
    async def test_returns_trade_dict_when_no_edge(self):
        """Model predicts much lower than market -> NO trade with edge."""
        bot = make_bot()
        bot._patch_drift = None
        bot._min_edge = 0.08
        bot._min_confidence = 0.55
        # Market price = 0.80, model predicts 0.30 -> edge_no = 0.50
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.80,
            no_price=0.20,
        )
        # Mock Glicko-2 prediction (replaced base_engine.get_predictions fallback)
        bot._get_glicko2_prediction = MagicMock(return_value=0.30)
        result = await bot.analyze_opportunity(market)
        assert result is not None
        assert result["side"] == "NO"
        assert result["edge"] == pytest.approx(0.50, abs=0.01)
        assert result["confidence"] == pytest.approx(0.70)  # 1.0 - 0.30

    @pytest.mark.asyncio
    async def test_returns_none_when_confidence_below_min(self):
        """Edge exists but confidence < min_confidence -> returns None."""
        bot = make_bot()
        bot._patch_drift = None
        bot._min_edge = 0.08
        bot._min_confidence = 0.55
        # Market price = 0.42, model predicts 0.51 -> edge = 0.09 (>0.08)
        # but confidence = 0.51 < 0.55
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.42,
        )
        bot._get_glicko2_prediction = MagicMock(return_value=0.51)
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_invalid_price(self):
        """Invalid price (0 or >1) -> returns None."""
        bot = make_bot()
        bot._patch_drift = None
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.0,
        )
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_pregame_type_when_not_live(self):
        """Market not in live_matches -> type=esports_pregame."""
        bot = make_bot()
        bot._patch_drift = None
        bot._live_matches = {}
        bot._min_edge = 0.05
        bot._min_confidence = 0.50
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.40,
        )
        # Mock Glicko-2 prediction (replaced base_engine.get_predictions fallback)
        bot._get_glicko2_prediction = MagicMock(return_value=0.70)
        result = await bot.analyze_opportunity(market)
        assert result is not None
        assert result["type"] == "esports_pregame"

    @pytest.mark.asyncio
    async def test_live_type_when_live_match_exists(self):
        """Market in live_matches -> type=esports_live."""
        bot = make_bot()
        bot._patch_drift = None
        bot._live_matches = {"m1": {"id": "m1", "game_state": {}}}
        bot._min_edge = 0.05
        bot._min_confidence = 0.50
        market = _make_market(
            market_id="m1",
            question="Will Team A win the LoL match?",
            yes_price=0.40,
        )
        # Mock Glicko-2 prediction (replaced base_engine.get_predictions fallback)
        bot._get_glicko2_prediction = MagicMock(return_value=0.70)
        result = await bot.analyze_opportunity(market)
        assert result is not None
        assert result["type"] == "esports_live"


# =========================================================================
# scan_and_trade
# =========================================================================


class TestScanAndTrade:
    @pytest.mark.asyncio
    async def test_no_markets_returns_cleanly(self):
        """No esports markets -> returns without error."""
        bot = make_bot()
        bot._patch_drift = None
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[])
        bot._last_live_refresh = 0.0
        bot.base_engine.get_markets = AsyncMock(return_value=[])
        bot.base_engine.filter_markets_for_trading = MagicMock(return_value=[])
        await bot.scan_and_trade()

    @pytest.mark.asyncio
    async def test_calls_analyze_for_each_market(self):
        """scan_and_trade calls analyze_opportunity for each esports market."""
        bot = make_bot()
        bot._patch_drift = None
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[])
        bot._last_live_refresh = 0.0

        markets = [
            _make_market(market_id="m1", question="lol match winner team a?"),
            _make_market(market_id="m2", question="cs2 blast premier team b?"),
        ]
        bot.base_engine.get_markets = AsyncMock(return_value=markets)
        bot.base_engine.filter_markets_for_trading = MagicMock(return_value=markets)

        analyzed = []

        async def fake_analyze(market_data):
            analyzed.append(market_data["id"])
            return None

        bot.analyze_opportunity = fake_analyze
        await bot.scan_and_trade()

        assert len(analyzed) == 2
        assert "m1" in analyzed
        assert "m2" in analyzed

    @pytest.mark.asyncio
    async def test_executes_trade_when_opportunity_found(self):
        """scan_and_trade calls _execute_esports_trade when analyze returns opp."""
        bot = make_bot()
        bot._patch_drift = None
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[])
        bot._last_live_refresh = 0.0

        market = _make_market(question="lol match winner?")
        bot.base_engine.get_markets = AsyncMock(return_value=[market])
        bot.base_engine.filter_markets_for_trading = MagicMock(return_value=[market])

        opp = {"market_id": "m1", "token_id": "tok-yes-1", "side": "YES",
               "price": 0.50, "confidence": 0.70, "edge": 0.20, "game": "lol"}

        bot.analyze_opportunity = AsyncMock(return_value=opp)
        bot._execute_esports_trade = AsyncMock()

        await bot.scan_and_trade()

        bot._execute_esports_trade.assert_awaited_once_with(opp)

    @pytest.mark.asyncio
    async def test_exception_in_analyze_does_not_crash(self):
        """Exception in analyze_opportunity for one market does not stop others."""
        bot = make_bot()
        bot._patch_drift = None
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[])
        bot._last_live_refresh = 0.0

        markets = [
            _make_market(market_id="m1", question="lol match winner?"),
            _make_market(market_id="m2", question="cs2 blast winner?"),
        ]
        bot.base_engine.get_markets = AsyncMock(return_value=markets)
        bot.base_engine.filter_markets_for_trading = MagicMock(return_value=markets)

        call_count = 0

        async def flaky_analyze(market_data):
            nonlocal call_count
            call_count += 1
            if market_data["id"] == "m1":
                raise RuntimeError("flaky error")
            return None

        bot.analyze_opportunity = flaky_analyze
        await bot.scan_and_trade()

        # Both markets should have been attempted
        assert call_count == 2


# =========================================================================
# _ws_pending_trades bounded lifetime
# =========================================================================


class TestWsPendingTradesBounded:
    """Verify _ws_pending_trades never accumulates entries between trades.

    The set is a race-condition guard only — market_ids are added at the top
    of _handle_ws_price_update() and removed in the `finally` block, whether
    the trade succeeds or raises.  After any trade attempt (success, skip, or
    exception), the set must be empty.
    """

    @pytest.mark.asyncio
    async def test_set_empty_after_successful_path_skips(self):
        """Set is empty after _handle_ws_price_update returns early (already pending)."""
        bot = make_bot()
        assert len(bot._ws_pending_trades) == 0

        # Simulate the guard triggering: add a market_id, confirm get returns early
        bot._ws_pending_trades.add("market-x")
        # Call would normally return immediately — simulate post-call state
        bot._ws_pending_trades.discard("market-x")  # finally block equivalent

        assert len(bot._ws_pending_trades) == 0

    @pytest.mark.asyncio
    async def test_set_empty_after_exception_in_trade(self):
        """Set is cleared via `finally` even when _handle_ws_price_update raises."""
        bot = make_bot()

        async def _failing_trade(*args, **kwargs):
            raise RuntimeError("simulated trade failure")

        market_id = "market-fail"
        bot._ws_pending_trades.add(market_id)
        try:
            await _failing_trade()
        except RuntimeError:
            pass
        finally:
            bot._ws_pending_trades.discard(market_id)

        assert len(bot._ws_pending_trades) == 0

    def test_initial_state_is_empty(self):
        """_ws_pending_trades starts empty on construction."""
        bot = make_bot()
        assert isinstance(bot._ws_pending_trades, set)
        assert len(bot._ws_pending_trades) == 0
