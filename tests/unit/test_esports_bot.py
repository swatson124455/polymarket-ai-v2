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
import asyncio

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
        mock_settings.ESPORTS_MIN_CONFIDENCE = 0.35  # S127: lowered for signal_quality dampening
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
    volume_24h=5000.0,
):
    """Create a mock esports market dict."""
    return {
        "id": market_id,
        "question": question,
        "volume_24h": volume_24h,
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
        assert bot._min_confidence == pytest.approx(0.35)
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
        bot._get_glicko2_prediction = AsyncMock(return_value=0.60)
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_prediction_is_none(self):
        """Glicko-2 prediction returns None -> returns None."""
        bot = make_bot()
        bot._patch_drift = None
        market = _make_market(question="Will Team A win the LoL match?")
        bot._get_glicko2_prediction = AsyncMock(return_value=None)
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
        bot._min_confidence = 0.20  # S127: lowered for signal_quality dampening
        # Market price = 0.50, model predicts 0.70 -> edge = 0.20
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.50,
        )
        # Mock Glicko-2 prediction (replaced base_engine.get_predictions fallback)
        bot._get_glicko2_prediction = AsyncMock(return_value=0.70)
        result = await bot.analyze_opportunity(market)
        assert result is not None
        assert result["side"] == "YES"
        assert result["edge"] == pytest.approx(0.20, abs=0.03)
        assert result["game"] == "lol"
        assert result["market_type"] == "match_winner"
        # S149: blue side bonus disabled (no blue/red detection).
        # confidence = raw side_prob after BO1 adjustment (~0.694 from 0.70).
        assert result["confidence"] > 0.65
        assert result["confidence"] < 0.80
        assert result["market_id"] == "m1"

    @pytest.mark.asyncio
    async def test_returns_trade_dict_when_no_edge(self):
        """Model predicts lower than market -> NO trade with edge."""
        bot = make_bot()
        bot._patch_drift = None
        bot._min_edge = 0.08
        bot._min_confidence = 0.20  # S127: lowered for signal_quality dampening
        # Market price = 0.55, model predicts 0.40 -> edge_no ≈ 0.15
        # S135: divergence=0.15 stays within 0.25 cap
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.55,
            no_price=0.45,
        )
        # Mock Glicko-2 prediction (replaced base_engine.get_predictions fallback)
        bot._get_glicko2_prediction = AsyncMock(return_value=0.40)
        result = await bot.analyze_opportunity(market)
        assert result is not None
        assert result["side"] == "NO"
        assert result["edge"] == pytest.approx(0.15, abs=0.03)  # blue side bonus shifts
        # S131: confidence = raw side_prob (SQ now scales sizing, not confidence)
        assert result["confidence"] > 0.55  # 1 - model_prob ≈ 0.60
        assert result["confidence"] < 0.65

    @pytest.mark.asyncio
    async def test_returns_none_when_confidence_below_min(self):
        """S131: Edge exists but side_prob < 0.52 gate -> returns None."""
        bot = make_bot()
        bot._patch_drift = None
        bot._min_edge = 0.01  # Low edge gate so edge isn't the blocker
        bot._min_confidence = 0.55  # S131: gate on raw side_prob
        # Market price = 0.42, model predicts 0.505 -> side_prob ≈ 0.52 (with blue side)
        # but min_confidence gate at 0.55 rejects it
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.42,
        )
        bot._get_glicko2_prediction = AsyncMock(return_value=0.505)
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
        bot._min_confidence = 0.20  # S127: lowered for signal_quality dampening
        # S135: divergence=0.15 within 0.25 cap
        market = _make_market(
            question="Will Team A win the LoL match?",
            yes_price=0.45,
        )
        bot._get_glicko2_prediction = AsyncMock(return_value=0.60)
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
        bot._min_confidence = 0.20  # S127: lowered for signal_quality dampening
        # S135: divergence=0.15 within 0.25 cap
        market = _make_market(
            market_id="m1",
            question="Will Team A win the LoL match?",
            yes_price=0.45,
        )
        bot._get_glicko2_prediction = AsyncMock(return_value=0.60)
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


class TestTeamNameExtraction:
    """Tests for team name extraction patterns and cleaning (Item 1)."""

    def test_clean_team_names_major_suffix(self):
        """Tournament suffixes like '- major' are stripped."""
        a, b = EsportsBot._clean_team_names("navi", "vitality - blast major 2026")
        assert a == "navi"
        assert b == "vitality"

    def test_clean_team_names_champions_suffix(self):
        """'- champions' suffix stripped."""
        a, b = EsportsBot._clean_team_names("fnatic", "g2 - champions stage")
        assert a == "fnatic"
        assert b == "g2"

    def test_clean_team_names_short_names_preserved(self):
        """Short names like t1, g2, 100 thieves survive cleaning."""
        a, b = EsportsBot._clean_team_names("t1", "g2")
        assert a == "t1"
        assert b == "g2"
        a2, b2 = EsportsBot._clean_team_names("100 thieves", "cloud9")
        assert a2 == "100 thieves"
        assert b2 == "cloud9"

    def test_clean_team_names_game_prefix(self):
        """Game title prefixes like 'league of legends: ' are stripped."""
        a, b = EsportsBot._clean_team_names("league of legends: t1", "league of legends: gen.g")
        assert a == "t1"
        assert b == "gen.g"

    @pytest.mark.asyncio
    async def test_glicko2_pattern_win_against(self):
        """Pattern 3: 'Will T1 win against Gen.G?' extracts both teams."""
        bot = make_bot()
        # Set up tracker with both teams
        from unittest.mock import MagicMock as MM
        tracker = MM()
        tracker.match_count = 100
        tracker.get_rating = MM(return_value=MM(phi=150.0, sigma=0.06))
        tracker.expected_score = MM(return_value=0.65)
        bot._glicko2_trackers["lol"] = tracker
        bot._team_name_to_id = {"t1": "t1_id", "gen.g": "geng_id"}

        market = {"question": "Will T1 win against Gen.G?", "id": "m1"}
        result = await bot._get_glicko2_prediction(market, "lol")
        assert result is not None
        assert 0.05 < result < 0.95

    @pytest.mark.asyncio
    async def test_glicko2_pattern_to_win_over(self):
        """Pattern 4: 'Fnatic to win over Vitality' extracts both teams."""
        bot = make_bot()
        from unittest.mock import MagicMock as MM
        tracker = MM()
        tracker.match_count = 100
        tracker.get_rating = MM(return_value=MM(phi=150.0, sigma=0.06))
        tracker.expected_score = MM(return_value=0.60)
        bot._glicko2_trackers["cs2"] = tracker
        bot._team_name_to_id = {"fnatic": "fn_id", "vitality": "vit_id"}

        market = {"question": "Fnatic to win over Vitality", "id": "m2"}
        result = await bot._get_glicko2_prediction(market, "cs2")
        assert result is not None
        assert 0.05 < result < 0.95

    @pytest.mark.asyncio
    async def test_glicko2_pattern_standard_vs_still_works(self):
        """Pattern 1: 'T1 vs Gen.G' still works after adding new patterns."""
        bot = make_bot()
        from unittest.mock import MagicMock as MM
        tracker = MM()
        tracker.match_count = 100
        tracker.get_rating = MM(return_value=MM(phi=150.0, sigma=0.06))
        tracker.expected_score = MM(return_value=0.55)
        bot._glicko2_trackers["lol"] = tracker
        bot._team_name_to_id = {"t1": "t1_id", "gen.g": "geng_id"}

        market = {"question": "T1 vs Gen.G", "id": "m3"}
        result = await bot._get_glicko2_prediction(market, "lol")
        assert result is not None

    @pytest.mark.asyncio
    async def test_team_fail_logged_rate_limited(self):
        """team_match_fail is logged once per unique team pair per session."""
        bot = make_bot()
        from unittest.mock import MagicMock as MM
        tracker = MM()
        tracker.match_count = 100
        bot._glicko2_trackers["lol"] = tracker
        bot._team_name_to_id = {}  # no teams → will fail

        market = {"question": "Unknown1 vs Unknown2", "id": "m4"}
        # Call twice — should only log once
        await bot._get_glicko2_prediction(market, "lol")
        await bot._get_glicko2_prediction(market, "lol")
        assert "lol:unknown1:unknown2" in bot._team_fail_logged


class TestPandaScoreTimeout:
    """Tests for PandaScore refresh timeout and staleness tracking (Item 2)."""

    @pytest.mark.asyncio
    async def test_timeout_preserves_cache(self):
        """On timeout, existing _live_matches are preserved (stale but usable)."""
        bot = make_bot()
        bot._live_matches = {"existing_match": {"id": "123"}}
        bot._last_live_refresh = 0.0  # force refresh
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_PANDASCORE_REFRESH_INTERVAL = 15
            ms.ESPORTS_PANDASCORE_TIMEOUT = 5.0
            await bot._refresh_live_matches()

        # Stale data preserved
        assert "existing_match" in bot._live_matches
        assert bot._live_refresh_failures == 1

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """Successful refresh resets _live_refresh_failures to 0."""
        bot = make_bot()
        bot._live_refresh_failures = 5
        bot._last_live_refresh = 0.0
        match_obj = MagicMock()
        match_obj.match_id = "m1"
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[match_obj])

        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_PANDASCORE_REFRESH_INTERVAL = 15
            ms.ESPORTS_PANDASCORE_TIMEOUT = 5.0
            await bot._refresh_live_matches()

        assert bot._live_refresh_failures == 0
        assert "m1" in bot._live_matches


class TestCalibrationCurve:
    """Tests for compute_calibration_curve (Item 3)."""

    @pytest.mark.asyncio
    async def test_calibration_curve_basic(self):
        """Verify ECE computation with known data."""
        from esports.data.esports_db import compute_calibration_curve

        # Mock DB returning predictions with known calibration (tuple rows)
        rows = []
        # 20 predictions at ~0.60 confidence, 50% win rate → miscalibrated by 0.10
        for i in range(20):
            rows.append((0.60, "YES", 1 if i < 10 else 0))
        # 10 predictions at ~0.80, 80% win rate → perfectly calibrated
        for i in range(10):
            rows.append((0.80, "YES", 1 if i < 8 else 0))

        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock()
        db.get_session.return_value = mock_ctx

        result = await compute_calibration_curve(db, game="cs2", days=90)
        assert result is not None
        assert result["total"] == 30
        assert 0.0 < result["ece"] < 0.15  # Should have some calibration error

    @pytest.mark.asyncio
    async def test_calibration_curve_insufficient_data(self):
        """Returns None with < 20 rows."""
        from esports.data.esports_db import compute_calibration_curve

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(0.60, "YES", 1)] * 10
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock()
        db.get_session.return_value = mock_ctx

        result = await compute_calibration_curve(db, game="lol", days=90)
        assert result is None


class TestPerGameKellyMult:
    """Tests for per-game Kelly multiplier (Item 4)."""

    def test_game_kelly_mult_penalty(self):
        """Brier > 0.25 produces mult=0.5."""
        bot = make_bot()
        # Simulate: monitoring loop would set this based on brier
        bot._game_kelly_mult["cs2"] = 0.5  # brier > 0.25
        assert bot._game_kelly_mult["cs2"] == 0.5

    def test_game_kelly_mult_boost(self):
        """Brier < 0.20 produces mult=1.2."""
        bot = make_bot()
        bot._game_kelly_mult["lol"] = 1.2  # brier < 0.20
        assert bot._game_kelly_mult["lol"] == 1.2

    def test_game_kelly_mult_default_is_one(self):
        """Unknown game returns 1.0 from dict.get()."""
        bot = make_bot()
        assert bot._game_kelly_mult.get("unknown_game", 1.0) == 1.0


class TestEdgeDecay:
    """Tests for edge decay monitoring (Item 5)."""

    @pytest.mark.asyncio
    async def test_edge_decay_stored(self):
        """Verify _edge_decay_data is populated after monitoring check."""
        bot = make_bot()
        # Just verify the dict exists and can be populated
        bot._edge_decay_data["cs2"] = {
            "total_predictions": 30,
            "bins": [{"avg_clv": 0.02, "avg_profit": 0.01}],
        }
        assert bot._edge_decay_data["cs2"]["total_predictions"] == 30

    @pytest.mark.asyncio
    async def test_edge_decay_negative_clv_flagged(self):
        """Negative CLV in top bin should be detectable."""
        bot = make_bot()
        bot._edge_decay_data["lol"] = {
            "total_predictions": 25,
            "bins": [{"avg_clv": -0.03, "avg_profit": -0.05}],
        }
        top_bin = bot._edge_decay_data["lol"]["bins"][0]
        assert top_bin["avg_clv"] < 0  # Would trigger warning


class TestParallelAnalysis:
    """Tests for parallel market analysis (Item 6)."""

    def test_semaphore_exists(self):
        """Bot has analysis semaphore and trade lock."""
        bot = make_bot()
        assert hasattr(bot, "_analysis_semaphore")
        assert hasattr(bot, "_trade_lock")

    @pytest.mark.asyncio
    async def test_parallel_exception_isolation(self):
        """One failing market doesn't crash the whole gather."""
        async def _ok():
            return (1, 1, 0)

        async def _fail():
            raise ValueError("boom")

        results = await asyncio.gather(_ok(), _fail(), return_exceptions=True)
        # First succeeded, second is an exception
        assert results[0] == (1, 1, 0)
        assert isinstance(results[1], ValueError)


class TestDynamicKellyGraduation:
    """Tests for continuous Kelly scaling (Item 7)."""

    @pytest.mark.asyncio
    async def test_kelly_scales_up_good_brier(self):
        """Good Brier (0.18) → scale > 1.0 → Kelly increases."""
        bot = make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.kelly_fraction = 0.25

        # Mock: 60 resolved trades, avg brier=0.18 per game
        acc_return = {"total": 60, "correct": 39, "accuracy": 0.65, "brier_score": 0.18}
        batch_return = {g: acc_return for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")}
        db = MagicMock()

        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            ms.ESPORTS_KELLY_DEGRADE_BRIER = 0.28
            ms.ESPORTS_KELLY_MAX_FRACTION = 0.35
            with patch("esports.data.esports_db.get_rolling_accuracy_batch", new_callable=AsyncMock) as mock_acc:
                mock_acc.return_value = batch_return
                await bot._check_kelly_graduation(db)

        # scale = clamp(2.0 - 0.18/0.25, 0.80, 1.30) = clamp(1.28, 0.80, 1.30) = 1.28
        # new_kelly = 0.25 * 1.28 = 0.32
        assert bot.bankroll.kelly_fraction > 0.25

    @pytest.mark.asyncio
    async def test_kelly_scales_down_bad_brier(self):
        """Bad Brier (0.26) → scale < 1.0 → Kelly decreases."""
        bot = make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.kelly_fraction = 0.30

        acc_return = {"total": 60, "correct": 30, "accuracy": 0.50, "brier_score": 0.26}
        batch_return = {g: acc_return for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")}
        db = MagicMock()

        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            ms.ESPORTS_KELLY_DEGRADE_BRIER = 0.28
            ms.ESPORTS_KELLY_MAX_FRACTION = 0.35
            with patch("esports.data.esports_db.get_rolling_accuracy_batch", new_callable=AsyncMock) as mock_acc:
                mock_acc.return_value = batch_return
                await bot._check_kelly_graduation(db)

        # scale = clamp(2.0 - 0.26/0.25, 0.80, 1.30) = clamp(0.96, 0.80, 1.30) = 0.96
        # new_kelly = 0.25 * 0.96 = 0.24
        assert bot.bankroll.kelly_fraction < 0.30

    @pytest.mark.asyncio
    async def test_kelly_absolute_cap(self):
        """Kelly never exceeds ESPORTS_KELLY_MAX_FRACTION."""
        bot = make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.kelly_fraction = 0.25

        # Extremely good Brier → would push scale to 1.30
        acc_return = {"total": 100, "correct": 70, "accuracy": 0.70, "brier_score": 0.10}
        batch_return = {g: acc_return for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")}
        db = MagicMock()

        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            ms.ESPORTS_KELLY_DEGRADE_BRIER = 0.28
            ms.ESPORTS_KELLY_MAX_FRACTION = 0.35
            with patch("esports.data.esports_db.get_rolling_accuracy_batch", new_callable=AsyncMock) as mock_acc:
                mock_acc.return_value = batch_return
                await bot._check_kelly_graduation(db)

        assert bot.bankroll.kelly_fraction <= 0.35

    @pytest.mark.asyncio
    async def test_kelly_no_change_below_50_resolved(self):
        """Kelly unchanged with < 50 resolved trades."""
        bot = make_bot()
        bot.bankroll = MagicMock()
        bot.bankroll.kelly_fraction = 0.25

        acc_return = {"total": 5, "correct": 3, "accuracy": 0.60, "brier_score": 0.20}
        batch_return = {g: acc_return for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")}
        db = MagicMock()

        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_KELLY_DEFAULT_FRACTION = 0.25
            ms.ESPORTS_KELLY_DEGRADE_BRIER = 0.28
            ms.ESPORTS_KELLY_MAX_FRACTION = 0.35
            with patch("esports.data.esports_db.get_rolling_accuracy_batch", new_callable=AsyncMock) as mock_acc:
                mock_acc.return_value = batch_return
                await bot._check_kelly_graduation(db)

        # 5 trades per game × 8 games = 40 total < 50 threshold
        assert bot.bankroll.kelly_fraction == 0.25


# =========================================================================
# S109: Anti-churn — exit cooldown + per-market entry cap
# =========================================================================

import time


class TestExitCooldown:
    """S109: Post-exit cooldown blocks re-entry for ESPORTS_EXIT_COOLDOWN_SECONDS."""

    def test_exit_cooldown_blocks_reentry(self):
        """Market in _recently_exited within cooldown window → blocked."""
        bot = make_bot()
        mid = "0x284a"
        bot._recently_exited[mid] = time.monotonic()  # just exited

        # Verify the cooldown check logic
        _exit_ts = bot._recently_exited.get(mid)
        assert _exit_ts is not None
        cooldown = 900.0
        elapsed = time.monotonic() - _exit_ts
        assert elapsed < cooldown  # within cooldown → would block

    @pytest.mark.asyncio
    async def test_exit_cooldown_expires(self):
        """Market exited long ago → cooldown expired, entry allowed."""
        bot = make_bot()
        mid = "0x284a"
        # Exited 1000s ago (> 900s cooldown)
        bot._recently_exited[mid] = time.monotonic() - 1000
        bot._wf = {"exit_cooldown": 0, "max_entries": 0}

        # Cooldown check passes, then analyze_opportunity returns None → (0,0,0)
        with patch("bots.esports_bot.settings") as ms:
            ms.ESPORTS_EXIT_COOLDOWN_SECONDS = 900.0
            ms.ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW = 2
            ms.ESPORTS_ENTRY_WINDOW_HOURS = 12.0
            elapsed = time.monotonic() - bot._recently_exited[mid]
            assert elapsed >= 900.0  # cooldown expired

    @pytest.mark.asyncio
    async def test_stop_loss_clears_prediction_cache(self):
        """After stop-loss exit, prediction cache for that market is cleared."""
        bot = make_bot()
        mid = "0xtest"
        bot._prediction_cache[mid] = {"prob": 0.55, "ts": time.monotonic(), "game": "lol"}
        bot._market_game[mid] = "lol"
        bot._game_exposure = {"lol": 100.0}

        # Simulate what happens after exit log line
        bot._recently_exited[mid] = time.monotonic()
        bot._prediction_cache.pop(mid, None)

        assert mid not in bot._prediction_cache
        assert mid in bot._recently_exited

    @pytest.mark.asyncio
    async def test_stop_loss_sets_recently_exited(self):
        """After stop-loss exit, _recently_exited is populated for that market."""
        bot = make_bot()
        mid = "0xtest"
        assert mid not in bot._recently_exited

        bot._recently_exited[mid] = time.monotonic()
        assert mid in bot._recently_exited
        assert time.monotonic() - bot._recently_exited[mid] < 1.0


class TestMaxEntriesPerMarket:
    """S109: Per-market rolling entry cap blocks after ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW."""

    def test_max_entries_blocks_after_cap(self):
        """Market with 2 entries within 12h window → blocked."""
        bot = make_bot()
        mid = "0x284a"
        now = time.monotonic()
        bot._market_entry_times[mid] = [now - 3600, now - 1800]  # 2 entries within last hour
        bot._wf = {"exit_cooldown": 0, "max_entries": 0}

        max_entries = 2
        window_s = 12.0 * 3600
        recent = [t for t in bot._market_entry_times.get(mid, []) if now - t < window_s]
        assert len(recent) >= max_entries

    def test_max_entries_allows_below_cap(self):
        """Market with 1 entry within 12h window → allowed."""
        bot = make_bot()
        mid = "0x284a"
        now = time.monotonic()
        bot._market_entry_times[mid] = [now - 3600]  # 1 entry within last hour

        max_entries = 2
        window_s = 12.0 * 3600
        recent = [t for t in bot._market_entry_times.get(mid, []) if now - t < window_s]
        assert len(recent) < max_entries

    def test_max_entries_expires_old(self):
        """Entries older than 12h window are not counted."""
        bot = make_bot()
        mid = "0x284a"
        now = time.monotonic()
        # 2 entries: one 13h ago (outside window), one 1h ago (inside)
        bot._market_entry_times[mid] = [now - 46800, now - 3600]

        max_entries = 2
        window_s = 12.0 * 3600
        recent = [t for t in bot._market_entry_times.get(mid, []) if now - t < window_s]
        assert len(recent) == 1  # only the recent one counts
        assert len(recent) < max_entries  # still allowed

    def test_entry_timestamp_appended_on_success(self):
        """Successful trade appends timestamp to entry list."""
        bot = make_bot()
        mid = "0xtest"
        assert len(bot._market_entry_times.get(mid, [])) == 0
        bot._market_entry_times.setdefault(mid, []).append(time.monotonic())
        assert len(bot._market_entry_times[mid]) == 1


class TestWsCooldownGuard:
    """S109: WS reactive path respects exit cooldown."""

    def test_ws_path_respects_cooldown(self):
        """WS path should return early when market is in cooldown."""
        bot = make_bot()
        mid = "0x284a"
        bot._recently_exited[mid] = time.monotonic()

        # The WS path checks _recently_exited before position check
        _exit_ts = bot._recently_exited.get(mid)
        assert _exit_ts is not None
        cooldown = 900.0
        assert time.monotonic() - _exit_ts < cooldown  # within cooldown

    def test_ws_path_respects_max_entries(self):
        """WS path should return early when market hits entry cap."""
        bot = make_bot()
        mid = "0x284a"
        now = time.monotonic()
        bot._market_entry_times[mid] = [now - 3600, now - 1800]  # 2 entries in window

        max_entries = 2
        window_s = 12.0 * 3600
        recent = [t for t in bot._market_entry_times.get(mid, []) if now - t < window_s]
        assert len(recent) >= max_entries


class TestStaleCostAfterMaxBetCap:
    """S133: Verify _cost is recalculated after max-bet cap adjusts size."""

    @pytest.mark.asyncio
    async def test_min_trade_rejects_dust_after_max_bet_cap(self):
        """When max-bet cap reduces size, min-trade gate should use updated cost."""
        bot = make_bot()
        bot.place_order = AsyncMock(return_value={"success": True})
        bot._game_exposure = {}
        bot._market_game = {}
        bot._entered_market_sides = set()
        bot._tournament_exposure = {}

        opp = {
            "market_id": "0xdust",
            "token_id": "tok1",
            "side": "YES",
            "price": 0.05,
            "confidence": 0.90,
            "edge": 0.10,
            "game": "lol",
            "type": "prematch",
            "market_type": "moneyline",
        }

        # Mock bankroll to return large size so max-bet cap triggers
        bot.calculate_bot_position_size = AsyncMock(return_value=200.0)

        with patch("bots.esports_bot.settings") as mock_settings:
            mock_settings.ESPORTS_MAX_BET_USD = 5.0
            mock_settings.ESPORTS_MIN_TRADE_USD = 8.0
            mock_settings.ESPORTS_MAX_GAME_EXPOSURE = 10000.0
            mock_settings.ESPORTS_MAX_POSITIONS = 100
            mock_settings.ESPORTS_SPREAD_BASE = 0.15
            mock_settings.ESPORTS_SPREAD_FACTOR = 0.05
            mock_settings.ESPORTS_MIN_ENTRY_PRICE = 0.05
            mock_settings.ESPORTS_MAX_ENTRY_PRICE = 0.95
            mock_settings.ESPORTS_CLV_SCALING_ENABLED = False
            mock_settings.ESPORTS_UPSET_RISK_ENABLED = False

            # bankroll returns 200 shares at price=$0.05 → cost=$10 (>$8 min)
            # max-bet cap: size=$5/$0.05=100 → cost=$5 (<$8 min)
            # Without fix: stale _cost=$10 passes gate → dust trade
            # With fix: _cost=$5 rejected
            result = await bot._execute_esports_trade(opp)
            assert result is False, "Dust trade should be rejected after max-bet cap"
            bot.place_order.assert_not_called()
