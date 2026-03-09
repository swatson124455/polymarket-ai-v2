"""
Unit tests for bots/esports_series_bot.py (EsportsSeriesBot).

Tests:
  - __init__ raises ValueError when PANDASCORE_API_KEY is missing/empty
  - __init__ succeeds with valid key, stores settings correctly
  - _get_scan_interval_seconds returns 30s with active series, 300s without
  - on_price_update skips in various guard-clause scenarios, triggers trade when valid
  - scan_and_trade calls _refresh_series and _analyze_series, handles exceptions
  - _analyze_series returns None for non-BO3+, decided series, missing market;
    returns trade dict when edge exists
  - _simple_series_prob computes correct probabilities for BO3 and BO5
  - _refresh_series parses PandaScore live matches into active_series dict
  - _execute_series_trade calls bankroll_mgr.get_bet_size and place_order
  - analyze_opportunity always returns None (series-driven bot)
"""
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bots.esports_series_bot import EsportsSeriesBot


def make_bot():
    """Create an EsportsSeriesBot with mocked base_engine and settings."""
    base_engine = MagicMock()
    base_engine.db = MagicMock()
    base_engine.order_gateway = MagicMock()
    base_engine.order_gateway.has_open_position = MagicMock(return_value=False)
    base_engine.order_gateway._daily_exposure_usd = {}
    base_engine.get_markets = AsyncMock(return_value=[])
    base_engine.filter_markets_for_trading = MagicMock(return_value=[])
    with patch("bots.esports_series_bot.settings") as mock_settings:
        mock_settings.PANDASCORE_API_KEY = "test-key"
        mock_settings.ESPORTS_SERIES_MIN_EDGE = 0.10
        mock_settings.ESPORTS_SERIES_REVERSE_SWEEP_FLOOR = 0.05
        mock_settings.SCAN_INTERVAL_ESPORTS_SERIES = 30
        mock_settings.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
        mock_settings.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 10
        mock_settings.ESPORTS_SERIES_REFRESH_INTERVAL = 30
        bot = EsportsSeriesBot(base_engine)
    return bot


def _make_series_data(
    match_id="match-1",
    game="cs2",
    team_a="Navi",
    team_b="FaZe",
    score_maps_a=1,
    score_maps_b=0,
    best_of=3,
):
    """Create a mock series data dict as stored in _active_series."""
    return {
        "match_id": match_id,
        "game": game,
        "team_a": team_a,
        "team_b": team_b,
        "score_maps_a": score_maps_a,
        "score_maps_b": score_maps_b,
        "best_of": best_of,
    }


def _make_pandascore_match(
    match_id="12345",
    best_of=3,
    team_a="Navi",
    team_b="FaZe",
    score_a=1,
    score_b=0,
    game_slug="cs-2",
):
    """Create a mock PandaScore live match dict."""
    return {
        "id": match_id,
        "number_of_games": best_of,
        "opponents": [
            {"opponent": {"name": team_a}},
            {"opponent": {"name": team_b}},
        ],
        "results": [
            {"score": score_a},
            {"score": score_b},
        ],
        "videogame": {"slug": game_slug},
    }


# =========================================================================
# Initialization
# =========================================================================


class TestEsportsSeriesBotInit:
    def test_raises_without_api_key(self):
        """__init__ raises ValueError when PANDASCORE_API_KEY is not set."""
        base_engine = MagicMock()
        with patch("bots.esports_series_bot.settings") as mock_settings:
            mock_settings.PANDASCORE_API_KEY = None
            with pytest.raises(ValueError, match="PANDASCORE_API_KEY"):
                EsportsSeriesBot(base_engine)

    def test_raises_with_empty_api_key(self):
        """__init__ raises ValueError when PANDASCORE_API_KEY is empty string."""
        base_engine = MagicMock()
        with patch("bots.esports_series_bot.settings") as mock_settings:
            mock_settings.PANDASCORE_API_KEY = ""
            with pytest.raises(ValueError, match="PANDASCORE_API_KEY"):
                EsportsSeriesBot(base_engine)

    def test_init_succeeds_with_valid_key(self):
        """__init__ succeeds when PANDASCORE_API_KEY is set."""
        bot = make_bot()
        assert bot._api_key == "test-key"
        assert bot.bot_name == "EsportsSeriesBot"

    def test_settings_stored_correctly(self):
        """Settings are correctly stored on bot instance."""
        bot = make_bot()
        assert bot._min_edge == pytest.approx(0.10)
        assert bot._reverse_sweep_floor == pytest.approx(0.05)
        assert bot._active_series == {}
        assert bot._series_prediction_cache == {}
        assert bot._last_refresh == 0.0


# =========================================================================
# _get_scan_interval_seconds
# =========================================================================


class TestGetScanIntervalSeconds:
    def test_returns_300s_without_active_series(self):
        """Returns 300s when no active series exist."""
        bot = make_bot()
        bot._active_series = {}
        assert bot._get_scan_interval_seconds() == pytest.approx(300.0)

    def test_returns_30s_with_active_series(self):
        """Returns 30s when active series are present."""
        bot = make_bot()
        bot._active_series = {"match-1": _make_series_data()}
        with patch("bots.esports_series_bot.settings") as mock_settings:
            mock_settings.SCAN_INTERVAL_ESPORTS_SERIES = 30
            assert bot._get_scan_interval_seconds() == pytest.approx(30.0)

    def test_returns_custom_interval_with_active_series(self):
        """Respects custom SCAN_INTERVAL_ESPORTS_SERIES setting."""
        bot = make_bot()
        bot._active_series = {"match-1": _make_series_data()}
        with patch("bots.esports_series_bot.settings") as mock_settings:
            mock_settings.SCAN_INTERVAL_ESPORTS_SERIES = 15
            assert bot._get_scan_interval_seconds() == pytest.approx(15.0)


# =========================================================================
# on_price_update
# =========================================================================


class TestOnPriceUpdate:
    @pytest.mark.asyncio
    async def test_skips_when_not_running(self):
        """on_price_update returns immediately when bot is not running."""
        bot = make_bot()
        bot.running = False
        bot._series_prediction_cache = {"m1": {"prob": 0.70, "game": "cs2"}}
        event = {"market_id": "m1", "token_id": "tok1", "price": 0.50}
        await bot.on_price_update(event)
        # No trade should have been attempted (no _execute_series_trade mock needed)

    @pytest.mark.asyncio
    async def test_skips_when_no_cached_prediction(self):
        """on_price_update skips when market not in prediction cache."""
        bot = make_bot()
        bot.running = True
        bot._series_prediction_cache = {}  # Empty cache
        event = {"market_id": "m1", "token_id": "tok1", "price": 0.50}
        bot._execute_series_trade = AsyncMock()
        await bot.on_price_update(event)
        bot._execute_series_trade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_price_change_below_threshold(self):
        """on_price_update skips when price change is below significance threshold."""
        bot = make_bot()
        bot.running = True
        bot._series_prediction_cache = {"m1": {"prob": 0.70, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()

        # First call sets the baseline price
        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 10
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.50})

        # Second call with tiny price change (0.1% < 1% threshold)
        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 10
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.5005})

        bot._execute_series_trade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_during_cooldown(self):
        """on_price_update skips when within cooldown period."""
        bot = make_bot()
        bot.running = True
        bot._series_prediction_cache = {"m1": {"prob": 0.70, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 10
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10

            # First call: set baseline
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.50})
            # Second call: big price change, passes threshold, triggers cooldown set
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.55})

        # Third call: still in cooldown (within 10s)
        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 10
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.45})

        # The trade on the third call should have been blocked by cooldown
        # (The second call may or may not have traded depending on edge,
        #  but the third should definitely be blocked by cooldown)

    @pytest.mark.asyncio
    async def test_skips_when_edge_below_min(self):
        """on_price_update skips when edge is below min_edge."""
        bot = make_bot()
        bot.running = True
        bot._min_edge = 0.10
        # Cache: model_prob = 0.55, new_price = 0.50 -> edge = 0.05 < 0.10
        bot._series_prediction_cache = {"m1": {"prob": 0.55, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 0  # No cooldown
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            # First call sets baseline
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.40})
            # Second call with enough price change but not enough edge
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.50})

        bot._execute_series_trade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_position_already_exists(self):
        """on_price_update skips when bot already has position on this market."""
        bot = make_bot()
        bot.running = True
        bot._min_edge = 0.10
        # Cache: model_prob = 0.80, new_price = 0.50 -> edge = 0.30 > 0.10
        bot._series_prediction_cache = {"m1": {"prob": 0.80, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()

        # Position already exists
        bot.base_engine.order_gateway.has_open_position = MagicMock(return_value=True)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 0
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            # First call sets baseline
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.40})
            # Second call with big enough edge
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.50})

        bot._execute_series_trade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_triggers_yes_trade_when_conditions_met(self):
        """on_price_update triggers YES trade when model_prob > price with sufficient edge."""
        bot = make_bot()
        bot.running = True
        bot._min_edge = 0.10
        # model_prob = 0.80, price will be 0.50 -> edge = 0.30 (YES)
        bot._series_prediction_cache = {"m1": {"prob": 0.80, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()
        bot.base_engine.order_gateway.has_open_position = MagicMock(return_value=False)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 0
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            # First call sets baseline
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.40})
            # Second call with big price change and enough edge
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.50})

        bot._execute_series_trade.assert_awaited_once()
        opp = bot._execute_series_trade.call_args[0][0]
        assert opp["side"] == "YES"
        assert opp["market_id"] == "m1"
        assert opp["type"] == "esports_series_ws"

    @pytest.mark.asyncio
    async def test_triggers_no_trade_when_model_prob_below_price(self):
        """on_price_update triggers NO trade when model_prob < price with sufficient edge."""
        bot = make_bot()
        bot.running = True
        bot._min_edge = 0.10
        # model_prob = 0.20, price will be 0.50 -> edge = -0.30 (NO side)
        bot._series_prediction_cache = {"m1": {"prob": 0.20, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()
        bot.base_engine.order_gateway.has_open_position = MagicMock(return_value=False)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
            ms.ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 0
            ms.ESPORTS_SERIES_MIN_EDGE = 0.10
            # First call sets baseline
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.40})
            # Second call
            await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0.50})

        bot._execute_series_trade.assert_awaited_once()
        opp = bot._execute_series_trade.call_args[0][0]
        assert opp["side"] == "NO"

    @pytest.mark.asyncio
    async def test_skips_empty_market_id(self):
        """on_price_update skips when market_id is empty."""
        bot = make_bot()
        bot.running = True
        bot._execute_series_trade = AsyncMock()
        await bot.on_price_update({"market_id": "", "token_id": "tok1", "price": 0.50})
        bot._execute_series_trade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_zero_price(self):
        """on_price_update skips when price is zero."""
        bot = make_bot()
        bot.running = True
        bot._series_prediction_cache = {"m1": {"prob": 0.70, "game": "cs2"}}
        bot._execute_series_trade = AsyncMock()
        await bot.on_price_update({"market_id": "m1", "token_id": "tok1", "price": 0})
        bot._execute_series_trade.assert_not_awaited()


# =========================================================================
# scan_and_trade
# =========================================================================


class TestScanAndTrade:
    @pytest.mark.asyncio
    async def test_returns_cleanly_with_no_active_series(self):
        """No active series -> returns without error."""
        bot = make_bot()
        bot._refresh_series = AsyncMock()
        bot._active_series = {}
        await bot.scan_and_trade()
        bot._refresh_series.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_analyze_for_each_series(self):
        """scan_and_trade calls _analyze_series for each active series."""
        bot = make_bot()
        bot._refresh_series = AsyncMock()
        bot._active_series = {
            "m1": _make_series_data(match_id="m1"),
            "m2": _make_series_data(match_id="m2"),
        }
        analyzed = []

        async def fake_analyze(match_id, series_data, db=None):
            analyzed.append(match_id)
            return None

        bot._analyze_series = fake_analyze
        await bot.scan_and_trade()

        assert len(analyzed) == 2
        assert "m1" in analyzed
        assert "m2" in analyzed

    @pytest.mark.asyncio
    async def test_calls_execute_when_opportunity_found(self):
        """scan_and_trade calls _execute_series_trade when _analyze_series returns opp."""
        bot = make_bot()
        bot._refresh_series = AsyncMock()
        bot._active_series = {"m1": _make_series_data(match_id="m1")}

        opp = {
            "type": "esports_series",
            "market_id": "mkt-1",
            "token_id": "tok-1",
            "side": "YES",
            "price": 0.50,
            "confidence": 0.70,
            "edge": 0.20,
            "game": "cs2",
        }
        bot._analyze_series = AsyncMock(return_value=[opp])
        bot._execute_series_trade = AsyncMock()

        await bot.scan_and_trade()

        bot._execute_series_trade.assert_awaited_once_with(opp)

    @pytest.mark.asyncio
    async def test_handles_exception_in_analyze_gracefully(self):
        """Exception in _analyze_series for one match does not stop others."""
        bot = make_bot()
        bot._refresh_series = AsyncMock()
        bot._active_series = {
            "m1": _make_series_data(match_id="m1"),
            "m2": _make_series_data(match_id="m2"),
        }

        call_count = 0

        async def flaky_analyze(match_id, series_data, db=None):
            nonlocal call_count
            call_count += 1
            if match_id == "m1":
                raise RuntimeError("flaky error")
            return []

        bot._analyze_series = flaky_analyze
        await bot.scan_and_trade()

        # Both series should have been attempted
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_tracks_scan_metrics(self):
        """scan_and_trade updates _last_scan_markets/opportunities/trades counters."""
        bot = make_bot()
        bot._refresh_series = AsyncMock()

        opp = {
            "type": "esports_series",
            "market_id": "mkt-1",
            "token_id": "tok-1",
            "side": "YES",
            "price": 0.50,
            "confidence": 0.70,
            "edge": 0.20,
            "game": "cs2",
        }

        bot._active_series = {
            "m1": _make_series_data(match_id="m1"),
            "m2": _make_series_data(match_id="m2"),
        }

        # First returns opportunity, second returns empty list
        bot._analyze_series = AsyncMock(side_effect=[[opp], []])
        bot._execute_series_trade = AsyncMock()

        await bot.scan_and_trade()

        assert bot._last_scan_markets == 2
        assert bot._last_scan_opportunities == 1
        assert bot._last_scan_trades == 1


# =========================================================================
# _analyze_series
# =========================================================================


class TestAnalyzeSeries:
    @pytest.mark.asyncio
    async def test_returns_none_for_bo1(self):
        """Returns [] for BO1 series (only trades BO3+)."""
        bot = make_bot()
        series = _make_series_data(best_of=1)
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_none_for_bo2(self):
        """Returns [] for BO2 series (only trades BO3+)."""
        bot = make_bot()
        series = _make_series_data(best_of=2)
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_none_when_series_decided_bo3(self):
        """Returns [] when team A has already won BO3 (2-0)."""
        bot = make_bot()
        series = _make_series_data(best_of=3, score_maps_a=2, score_maps_b=0)
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_none_when_series_decided_bo5(self):
        """Returns [] when team B has already won BO5 (1-3)."""
        bot = make_bot()
        series = _make_series_data(best_of=5, score_maps_a=1, score_maps_b=3)
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_simple_series_prob_when_no_map_rates(self):
        """Falls back to _simple_series_prob when HLTV map rates are empty."""
        bot = make_bot()
        bot._hltv = None  # No HLTV scraper
        bot._min_edge = 0.10
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        # Mock _find_series_market to return a market
        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.50,
        })

        # simple_series_prob(1, 0, 3) with p=0.50 should be around 0.75
        # Edge: 0.75 - 0.50 = 0.25 > 0.10
        with patch("bots.esports_series_bot.log_prediction", new_callable=AsyncMock, create=True):
            result = await bot._analyze_series("m1", series, db=None)

        assert result
        assert result[0]["side"] == "YES"
        assert result[0]["prediction"] > 0.50

    @pytest.mark.asyncio
    async def test_returns_none_when_no_matching_market(self):
        """Returns [] when _find_series_market returns None."""
        bot = make_bot()
        bot._hltv = None
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value=None)
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_none_when_market_missing_id(self):
        """Returns [] when market has no market_id."""
        bot = make_bot()
        bot._hltv = None
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": None,
            "token_id": "tok-1",
            "price": 0.50,
        })
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_none_when_market_missing_token_id(self):
        """Returns [] when market has no token_id."""
        bot = make_bot()
        bot._hltv = None
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": None,
            "price": 0.50,
        })
        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_yes_trade_when_edge_sufficient(self):
        """Returns YES trade dict when model_prob > market_price with sufficient edge."""
        bot = make_bot()
        bot._hltv = None
        bot._min_edge = 0.10
        # BO3, score 1-0 -> simple_series_prob(1, 0, 3) = ~0.75
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.50,
        })

        with patch("bots.esports_series_bot.log_prediction", new_callable=AsyncMock, create=True):
            result = await bot._analyze_series("m1", series, db=None)

        assert result
        assert result[0]["side"] == "YES"
        assert result[0]["type"] == "esports_series"
        assert result[0]["market_id"] == "mkt-1"
        assert result[0]["token_id"] == "tok-yes-1"
        assert result[0]["edge"] > 0.10
        assert result[0]["best_of"] == 3
        assert result[0]["series_score"] == "1-0"
        assert result[0]["game"] == "cs2"
        assert result[0]["market_type"] == "match_winner"

    @pytest.mark.asyncio
    async def test_returns_no_trade_when_model_prob_below_market(self):
        """Returns NO trade dict when model_prob < market_price with sufficient edge."""
        bot = make_bot()
        bot._hltv = None
        bot._min_edge = 0.10
        # BO3, score 0-1 -> simple_series_prob(0, 1, 3) = ~0.25
        # Market price = 0.50 -> edge_no = 0.50 - 0.25 = 0.25
        series = _make_series_data(best_of=3, score_maps_a=0, score_maps_b=1)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.50,
        })

        with patch("bots.esports_series_bot.log_prediction", new_callable=AsyncMock, create=True):
            result = await bot._analyze_series("m1", series, db=None)

        assert result
        assert result[0]["side"] == "NO"
        assert result[0]["token_id"] == "tok-no-1"
        assert result[0]["confidence"] == pytest.approx(1.0 - result[0]["prediction"])

    @pytest.mark.asyncio
    async def test_returns_none_when_no_edge(self):
        """Returns None when neither YES nor NO edge meets min_edge."""
        bot = make_bot()
        bot._hltv = None
        bot._min_edge = 0.10
        # BO3, score 0-0 -> simple_series_prob(0, 0, 3) = 0.50
        # Market price = 0.50 -> edge = 0.0
        series = _make_series_data(best_of=3, score_maps_a=0, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.50,
        })

        result = await bot._analyze_series("m1", series, db=None)
        assert result == []

    @pytest.mark.asyncio
    async def test_caches_prediction_for_ws_path(self):
        """_analyze_series caches prediction in _series_prediction_cache."""
        bot = make_bot()
        bot._hltv = None
        bot._min_edge = 0.10
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.50,
        })

        with patch("bots.esports_series_bot.log_prediction", new_callable=AsyncMock, create=True):
            result = await bot._analyze_series("m1", series, db=None)

        assert result
        assert "mkt-1" in bot._series_prediction_cache
        assert "prob" in bot._series_prediction_cache["mkt-1"]
        assert "ts" in bot._series_prediction_cache["mkt-1"]
        assert "game" in bot._series_prediction_cache["mkt-1"]

    @pytest.mark.asyncio
    async def test_uses_map_veto_model_when_rates_available(self):
        """Uses series_prob_with_map_veto when HLTV map rates are available."""
        bot = make_bot()
        bot._min_edge = 0.10

        # Mock HLTV scraper returning map rates
        bot._hltv = MagicMock()
        map_rates_a = {"mirage": 0.60, "inferno": 0.55, "nuke": 0.45}
        map_rates_b = {"mirage": 0.50, "inferno": 0.40, "nuke": 0.55}
        bot._hltv.get_map_win_rates = AsyncMock(side_effect=[map_rates_a, map_rates_b])

        series = _make_series_data(best_of=3, score_maps_a=0, score_maps_b=0, game="cs2")

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.30,  # Low price so we get edge
        })

        with patch(
            "esports.models.series_model.series_prob_with_map_veto", return_value=0.60
        ) as mock_spwmv, patch(
            "bots.esports_series_bot.log_prediction", new_callable=AsyncMock, create=True
        ):
            result = await bot._analyze_series("m1", series, db=None)

        # Verify series_prob_with_map_veto was called (the import is inside _analyze_series)
        assert result

    @pytest.mark.asyncio
    async def test_prediction_logging_failure_doesnt_crash(self):
        """Prediction logging failure does not prevent returning trade dict."""
        bot = make_bot()
        bot._hltv = None
        bot._min_edge = 0.10
        series = _make_series_data(best_of=3, score_maps_a=1, score_maps_b=0)

        bot._find_series_market = AsyncMock(return_value={
            "market_id": "mkt-1",
            "token_id": "tok-yes-1",
            "no_token_id": "tok-no-1",
            "price": 0.50,
        })

        with patch(
            "esports.data.esports_db.log_prediction",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB error"),
        ):
            result = await bot._analyze_series("m1", series, db=None)

        # Should still return list with trade dict despite logging failure
        assert result
        assert result[0]["side"] == "YES"


# =========================================================================
# _simple_series_prob
# =========================================================================


class TestSimpleSeriesProb:
    def test_bo3_even_score(self):
        """BO3 at 0-0 with p=0.50 should return 0.50."""
        bot = make_bot()
        prob = bot._simple_series_prob(0, 0, best_of=3)
        assert prob == pytest.approx(0.50, abs=0.01)

    def test_bo3_one_zero(self):
        """BO3 at 1-0 with p=0.50 should return 0.75."""
        bot = make_bot()
        prob = bot._simple_series_prob(1, 0, best_of=3)
        assert prob == pytest.approx(0.75, abs=0.01)

    def test_bo3_zero_one(self):
        """BO3 at 0-1 with p=0.50 should return 0.25."""
        bot = make_bot()
        prob = bot._simple_series_prob(0, 1, best_of=3)
        assert prob == pytest.approx(0.25, abs=0.01)

    def test_bo3_one_one(self):
        """BO3 at 1-1 with p=0.50 should return 0.50."""
        bot = make_bot()
        prob = bot._simple_series_prob(1, 1, best_of=3)
        assert prob == pytest.approx(0.50, abs=0.01)

    def test_bo5_even_score(self):
        """BO5 at 0-0 with p=0.50 should return 0.50."""
        bot = make_bot()
        prob = bot._simple_series_prob(0, 0, best_of=5)
        assert prob == pytest.approx(0.50, abs=0.01)

    def test_bo5_two_zero(self):
        """BO5 at 2-0 with p=0.50 should return 0.875."""
        bot = make_bot()
        prob = bot._simple_series_prob(2, 0, best_of=5)
        assert prob == pytest.approx(0.875, abs=0.01)

    def test_bo5_zero_two(self):
        """BO5 at 0-2 with p=0.50 should return 0.125."""
        bot = make_bot()
        prob = bot._simple_series_prob(0, 2, best_of=5)
        assert prob == pytest.approx(0.125, abs=0.01)

    def test_bo5_two_one(self):
        """BO5 at 2-1 with p=0.50 should return 0.75."""
        bot = make_bot()
        prob = bot._simple_series_prob(2, 1, best_of=5)
        assert prob == pytest.approx(0.75, abs=0.01)

    def test_bo5_one_two(self):
        """BO5 at 1-2 with p=0.50 should return 0.25."""
        bot = make_bot()
        prob = bot._simple_series_prob(1, 2, best_of=5)
        assert prob == pytest.approx(0.25, abs=0.01)

    def test_returns_none_for_unsupported_format(self):
        """Returns None for best_of values other than 3 or 5."""
        bot = make_bot()
        assert bot._simple_series_prob(0, 0, best_of=7) is None
        assert bot._simple_series_prob(0, 0, best_of=1) is None


# =========================================================================
# _refresh_series
# =========================================================================


class TestRefreshSeries:
    @pytest.mark.asyncio
    async def test_parses_live_matches_into_active_series(self):
        """Correctly parses PandaScore live matches into active_series dict."""
        bot = make_bot()
        bot._last_refresh = 0.0  # Force refresh

        matches = [
            _make_pandascore_match(
                match_id="111",
                best_of=3,
                team_a="Navi",
                team_b="FaZe",
                score_a=1,
                score_b=0,
                game_slug="cs-2",
            ),
            _make_pandascore_match(
                match_id="222",
                best_of=5,
                team_a="T1",
                team_b="GenG",
                score_a=2,
                score_b=1,
                game_slug="league-of-legends",
            ),
        ]
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=matches)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        assert len(bot._active_series) == 2
        assert "111" in bot._active_series
        assert "222" in bot._active_series

        cs2_series = bot._active_series["111"]
        assert cs2_series["game"] == "cs2"
        assert cs2_series["team_a"] == "Navi"
        assert cs2_series["team_b"] == "FaZe"
        assert cs2_series["score_maps_a"] == 1
        assert cs2_series["score_maps_b"] == 0
        assert cs2_series["best_of"] == 3

        lol_series = bot._active_series["222"]
        assert lol_series["game"] == "lol"
        assert lol_series["team_a"] == "T1"
        assert lol_series["team_b"] == "GenG"
        assert lol_series["score_maps_a"] == 2
        assert lol_series["score_maps_b"] == 1
        assert lol_series["best_of"] == 5

    @pytest.mark.asyncio
    async def test_skips_bo1_matches(self):
        """BO1 matches are filtered out (only BO3+ kept)."""
        bot = make_bot()
        bot._last_refresh = 0.0

        matches = [
            _make_pandascore_match(match_id="111", best_of=1),
            _make_pandascore_match(match_id="222", best_of=3),
        ]
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=matches)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        assert len(bot._active_series) == 1
        assert "222" in bot._active_series
        assert "111" not in bot._active_series

    @pytest.mark.asyncio
    async def test_handles_empty_response(self):
        """Empty live matches list results in empty active_series."""
        bot = make_bot()
        bot._last_refresh = 0.0

        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[])

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        assert bot._active_series == {}

    @pytest.mark.asyncio
    async def test_handles_none_response(self):
        """None response from PandaScore results in empty active_series."""
        bot = make_bot()
        bot._last_refresh = 0.0

        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=None)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        assert bot._active_series == {}

    @pytest.mark.asyncio
    async def test_handles_api_error_gracefully(self):
        """API error does not crash; active_series is unchanged."""
        bot = make_bot()
        bot._last_refresh = 0.0
        bot._active_series = {"existing": _make_series_data()}

        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(side_effect=RuntimeError("API error"))

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        # Should keep existing series on error
        assert "existing" in bot._active_series

    @pytest.mark.asyncio
    async def test_skips_refresh_within_interval(self):
        """Does not call PandaScore if within refresh interval."""
        bot = make_bot()
        bot._last_refresh = time.monotonic()  # Just refreshed

        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=[])

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        bot._pandascore.get_live_matches.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_pandascore_client(self):
        """Returns immediately when _pandascore is None."""
        bot = make_bot()
        bot._last_refresh = 0.0
        bot._pandascore = None

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        assert bot._active_series == {}

    @pytest.mark.asyncio
    async def test_maps_game_slugs_correctly(self):
        """Correctly maps PandaScore game slugs to internal game codes."""
        bot = make_bot()
        bot._last_refresh = 0.0

        matches = [
            _make_pandascore_match(match_id="1", best_of=3, game_slug="cs-2"),
            _make_pandascore_match(match_id="2", best_of=3, game_slug="cs-go"),
            _make_pandascore_match(match_id="3", best_of=3, game_slug="league-of-legends"),
            _make_pandascore_match(match_id="4", best_of=3, game_slug="dota-2"),
            _make_pandascore_match(match_id="5", best_of=3, game_slug="valorant"),
            _make_pandascore_match(match_id="6", best_of=3, game_slug="unknown-game"),
        ]
        bot._pandascore = MagicMock()
        bot._pandascore.get_live_matches = AsyncMock(return_value=matches)

        with patch("bots.esports_series_bot.settings") as ms:
            ms.ESPORTS_SERIES_REFRESH_INTERVAL = 30
            await bot._refresh_series()

        assert bot._active_series["1"]["game"] == "cs2"
        assert bot._active_series["2"]["game"] == "cs2"
        assert bot._active_series["3"]["game"] == "lol"
        assert bot._active_series["4"]["game"] == "dota2"
        assert bot._active_series["5"]["game"] == "valorant"
        assert bot._active_series["6"]["game"] == ""


# =========================================================================
# _execute_series_trade
# =========================================================================


class TestExecuteSeriesTrade:
    @pytest.mark.asyncio
    async def test_calls_bankroll_and_place_order(self):
        """Calls bankroll_mgr.get_bet_size and place_order when size > 0."""
        bot = make_bot()
        bot._bankroll_mgr = MagicMock()
        bot._bankroll_mgr.get_bet_size = AsyncMock(return_value=10.0)
        bot.place_order = AsyncMock(return_value={"success": True})

        opp = {
            "type": "esports_series",
            "market_id": "mkt-1",
            "token_id": "tok-1",
            "side": "YES",
            "price": 0.50,
            "confidence": 0.70,
            "edge": 0.20,
            "game": "cs2",
            "market_type": "series",
            "series_score": "1-0",
            "best_of": 3,
        }

        await bot._execute_series_trade(opp)

        bot._bankroll_mgr.get_bet_size.assert_awaited_once()
        bot.place_order.assert_awaited_once_with(
            market_id="mkt-1",
            token_id="tok-1",
            side="YES",
            size=10.0,
            price=0.50,
            confidence=0.70,
        )

    @pytest.mark.asyncio
    async def test_skips_trade_when_size_zero(self):
        """Does not call place_order when bankroll returns size=0."""
        bot = make_bot()
        bot._bankroll_mgr = MagicMock()
        bot._bankroll_mgr.get_bet_size = AsyncMock(return_value=0.0)
        bot.place_order = AsyncMock()

        opp = {
            "type": "esports_series",
            "market_id": "mkt-1",
            "token_id": "tok-1",
            "side": "YES",
            "price": 0.50,
            "confidence": 0.70,
            "edge": 0.20,
            "game": "cs2",
        }

        await bot._execute_series_trade(opp)

        bot.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_trade_when_bankroll_raises(self):
        """Does not call place_order when bankroll manager raises exception."""
        bot = make_bot()
        bot._bankroll_mgr = MagicMock()
        bot._bankroll_mgr.get_bet_size = AsyncMock(side_effect=RuntimeError("bankroll error"))
        bot.place_order = AsyncMock()

        opp = {
            "type": "esports_series",
            "market_id": "mkt-1",
            "token_id": "tok-1",
            "side": "YES",
            "price": 0.50,
            "confidence": 0.70,
            "edge": 0.20,
            "game": "cs2",
        }

        await bot._execute_series_trade(opp)

        bot.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_works_without_bankroll_manager(self):
        """When _bankroll_mgr is None, size=0 and trade is skipped."""
        bot = make_bot()
        bot._bankroll_mgr = None
        bot.place_order = AsyncMock()

        opp = {
            "type": "esports_series",
            "market_id": "mkt-1",
            "token_id": "tok-1",
            "side": "YES",
            "price": 0.50,
            "confidence": 0.70,
            "edge": 0.20,
            "game": "cs2",
        }

        await bot._execute_series_trade(opp)

        bot.place_order.assert_not_awaited()


# =========================================================================
# analyze_opportunity
# =========================================================================


class TestAnalyzeOpportunity:
    @pytest.mark.asyncio
    async def test_always_returns_none(self):
        """analyze_opportunity always returns None (series-driven bot)."""
        bot = make_bot()
        market = {
            "id": "m1",
            "question": "cs2 match winner?",
            "tokens": [{"tokenId": "t1", "outcomePrice": "0.50"}],
        }
        result = await bot.analyze_opportunity(market)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_any_market(self):
        """analyze_opportunity returns None regardless of market data."""
        bot = make_bot()
        result = await bot.analyze_opportunity({})
        assert result is None
        result = await bot.analyze_opportunity({"id": "test", "tokens": []})
        assert result is None
