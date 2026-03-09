"""
Unit tests for bots/esports_live_bot.py (EsportsLiveBot).

Tests:
  - __init__ raises ValueError when PANDASCORE_API_KEY is missing/empty
  - __init__ succeeds with valid key
  - _get_scan_interval_seconds returns 10s during live games, 60s when idle
  - on_price_update skips when bot not running
  - on_price_update skips when price change below threshold
  - on_price_update skips during cooldown period
  - on_price_update logs significant price moves during active games
  - scan_and_trade returns cleanly with empty queue
  - scan_and_trade processes up to 20 game states from queue
  - scan_and_trade detects events via event_detector.detect()
  - scan_and_trade fires live_trigger.process_event for each detected event
  - scan_and_trade handles timeout from live_trigger gracefully
  - scan_and_trade handles exceptions from live_trigger gracefully
  - scan_and_trade updates scan metrics
  - start initializes components
  - stop cancels monitor task cleanly
  - analyze_opportunity always returns None
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bots.esports_live_bot import EsportsLiveBot
from esports.live.esports_game_monitor import EsportsGameState
from esports.live.esports_event_detector import EsportsLiveEvent


def make_bot():
    """Create an EsportsLiveBot with mocked base_engine and settings."""
    base_engine = MagicMock()
    base_engine.db = MagicMock()
    base_engine.order_gateway = MagicMock()
    base_engine.order_gateway._daily_exposure_usd = {}
    base_engine.get_markets = AsyncMock(return_value=[])
    base_engine.filter_markets_for_trading = MagicMock(return_value=[])
    base_engine.get_predictions = AsyncMock(return_value=None)
    with patch("bots.esports_live_bot.settings") as mock_settings:
        mock_settings.PANDASCORE_API_KEY = "test-key"
        mock_settings.SCAN_INTERVAL_ESPORTS_LIVE = 10
        mock_settings.ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005
        mock_settings.ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
        bot = EsportsLiveBot(base_engine)
    return bot


def _make_game_state(
    match_id="match-1",
    game="cs2",
    team_a="Team Alpha",
    team_b="Team Beta",
    status="running",
    score_maps_a=0,
    score_maps_b=0,
    best_of=3,
    current_map=1,
    elapsed_pct=0.5,
    game_state=None,
):
    """Create an EsportsGameState for testing."""
    return EsportsGameState(
        match_id=match_id,
        game=game,
        team_a=team_a,
        team_b=team_b,
        status=status,
        score_maps_a=score_maps_a,
        score_maps_b=score_maps_b,
        best_of=best_of,
        current_map=current_map,
        elapsed_pct=elapsed_pct,
        game_state=game_state or {},
    )


def _make_live_event(
    match_id="match-1",
    game="cs2",
    event_type="economy_break",
    description="Economy break detected",
    confidence=0.75,
    map_number=1,
    edge_estimate=0.10,
    market_side="YES",
):
    """Create an EsportsLiveEvent for testing."""
    return EsportsLiveEvent(
        match_id=match_id,
        game=game,
        event_type=event_type,
        description=description,
        confidence=confidence,
        map_number=map_number,
        edge_estimate=edge_estimate,
        market_side=market_side,
    )


# =========================================================================
# Initialization
# =========================================================================


class TestEsportsLiveBotInit:
    def test_raises_without_api_key(self):
        """__init__ raises ValueError when PANDASCORE_API_KEY is not set."""
        base_engine = MagicMock()
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.PANDASCORE_API_KEY = None
            with pytest.raises(ValueError, match="PANDASCORE_API_KEY"):
                EsportsLiveBot(base_engine)

    def test_raises_with_empty_api_key(self):
        """__init__ raises ValueError when PANDASCORE_API_KEY is empty string."""
        base_engine = MagicMock()
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.PANDASCORE_API_KEY = ""
            with pytest.raises(ValueError, match="PANDASCORE_API_KEY"):
                EsportsLiveBot(base_engine)

    def test_init_succeeds_with_valid_key(self):
        """__init__ succeeds when PANDASCORE_API_KEY is set."""
        bot = make_bot()
        assert bot._api_key == "test-key"
        assert bot.bot_name == "EsportsLiveBot"

    def test_init_creates_queue(self):
        """__init__ creates a game update queue with maxsize 200."""
        bot = make_bot()
        assert isinstance(bot._game_update_queue, asyncio.Queue)
        assert bot._game_update_queue.maxsize == 200

    def test_init_components_none(self):
        """Components are None until start() is called."""
        bot = make_bot()
        assert bot._game_monitor is None
        assert bot._event_detector is None
        assert bot._live_trigger is None
        assert bot._scanner is None
        assert bot._bankroll_mgr is None
        assert bot._monitor_task is None


# =========================================================================
# Scan Interval
# =========================================================================


class TestScanInterval:
    def test_idle_scan_interval_no_game_monitor(self):
        """Returns 60s when game monitor is not set."""
        bot = make_bot()
        bot._game_monitor = None
        assert bot._get_scan_interval_seconds() == pytest.approx(60.0)

    def test_idle_scan_interval_no_active_games(self):
        """Returns 60s when game monitor has no active games."""
        bot = make_bot()
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}
        assert bot._get_scan_interval_seconds() == pytest.approx(60.0)

    def test_live_scan_interval_with_active_games(self):
        """Returns 10s when game monitor has active games."""
        bot = make_bot()
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": _make_game_state()}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.SCAN_INTERVAL_ESPORTS_LIVE = 10
            assert bot._get_scan_interval_seconds() == pytest.approx(10.0)

    def test_live_scan_interval_custom_value(self):
        """Returns custom interval from settings when live games active."""
        bot = make_bot()
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": _make_game_state()}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.SCAN_INTERVAL_ESPORTS_LIVE = 5
            assert bot._get_scan_interval_seconds() == pytest.approx(5.0)


# =========================================================================
# on_price_update
# =========================================================================


class TestOnPriceUpdate:
    @pytest.mark.asyncio
    async def test_skips_when_not_running(self):
        """on_price_update returns early when bot is not running."""
        bot = make_bot()
        bot.running = False
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": {}}
        event = {"market_id": "m1", "price": 0.60}
        # Should not raise, should just return silently
        await bot.on_price_update(event)
        # Verify no _ws_prev_prices tracking was done (skipped before that)
        assert not hasattr(bot, "_ws_prev_prices")

    @pytest.mark.asyncio
    async def test_skips_when_no_game_monitor(self):
        """on_price_update returns early when game monitor is None."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = None
        event = {"market_id": "m1", "price": 0.60}
        await bot.on_price_update(event)
        assert not hasattr(bot, "_ws_prev_prices")

    @pytest.mark.asyncio
    async def test_skips_when_no_market_id(self):
        """on_price_update returns early when market_id is missing."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        event = {"price": 0.60}
        await bot.on_price_update(event)

    @pytest.mark.asyncio
    async def test_skips_when_price_zero(self):
        """on_price_update returns early when price is 0."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        event = {"market_id": "m1", "price": 0}
        await bot.on_price_update(event)

    @pytest.mark.asyncio
    async def test_skips_first_price_update_no_previous(self):
        """First price update has no previous price, so it is stored but no log."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": {}}
        event = {"market_id": "m1", "price": 0.50}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005
            mock_settings.ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
            await bot.on_price_update(event)
        # Price should be stored
        assert bot._ws_prev_prices["m1"] == pytest.approx(0.50)

    @pytest.mark.asyncio
    async def test_skips_below_threshold(self):
        """on_price_update skips when price change is below threshold."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": {}}
        # Pre-seed price so we have a previous
        bot._ws_prev_prices = {"m1": 0.500}
        # 0.1% change (below 0.5% threshold)
        event = {"market_id": "m1", "price": 0.5005}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005
            mock_settings.ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
            await bot.on_price_update(event)
        # Price is updated but no cooldown tracking since it was below threshold
        assert not hasattr(bot, "_ws_cooldowns")

    @pytest.mark.asyncio
    async def test_skips_during_cooldown(self):
        """on_price_update skips when within cooldown period."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": {}}
        # Pre-seed prices and cooldowns
        bot._ws_prev_prices = {"m1": 0.50}
        bot._ws_cooldowns = {"m1": time.monotonic()}  # just now
        # Big price change (above threshold)
        event = {"market_id": "m1", "price": 0.60}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005
            mock_settings.ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
            await bot.on_price_update(event)
        # Cooldown was NOT updated (still the old value, because we returned early)
        # The price was updated though (line 159 runs before cooldown check)
        assert bot._ws_prev_prices["m1"] == pytest.approx(0.60)

    @pytest.mark.asyncio
    async def test_logs_significant_price_move_with_active_games(self):
        """on_price_update logs when significant price move AND active games."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {"match-1": {}}
        # Pre-seed old price
        bot._ws_prev_prices = {"m1": 0.50}
        bot._ws_cooldowns = {"m1": 0.0}  # expired cooldown
        event = {"market_id": "m1", "price": 0.60}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005
            mock_settings.ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
            with patch("bots.esports_live_bot.logger") as mock_logger:
                await bot.on_price_update(event)
                # Check that the significant price move was logged
                mock_logger.info.assert_called()
                call_args = mock_logger.info.call_args
                assert "significant price move" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_log_when_no_active_games(self):
        """on_price_update does NOT log when no active games even with big move."""
        bot = make_bot()
        bot.running = True
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}  # no active games
        # Pre-seed old price
        bot._ws_prev_prices = {"m1": 0.50}
        bot._ws_cooldowns = {"m1": 0.0}  # expired cooldown
        event = {"market_id": "m1", "price": 0.60}
        with patch("bots.esports_live_bot.settings") as mock_settings:
            mock_settings.ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005
            mock_settings.ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
            with patch("bots.esports_live_bot.logger") as mock_logger:
                await bot.on_price_update(event)
                # No info log about price moves (no active games)
                for call in mock_logger.info.call_args_list:
                    if call[0]:
                        assert "significant price move" not in call[0][0]


# =========================================================================
# scan_and_trade
# =========================================================================


class TestScanAndTrade:
    @pytest.mark.asyncio
    async def test_empty_queue_returns_cleanly(self):
        """No game state updates in queue -> returns without error."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._event_detector = MagicMock()
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}
        await bot.scan_and_trade()
        assert bot._last_scan_markets == 0
        assert bot._last_scan_opportunities == 0
        assert bot._last_scan_trades == 0

    @pytest.mark.asyncio
    async def test_prunes_cooldowns_on_scan(self):
        """scan_and_trade calls prune_cooldowns on live_trigger."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._event_detector = MagicMock()
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}
        await bot.scan_and_trade()
        bot._live_trigger.prune_cooldowns.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_game_states_from_queue(self):
        """scan_and_trade processes game state updates from queue."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[])
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        # Put 3 game states in queue
        for i in range(3):
            await bot._game_update_queue.put(
                _make_game_state(match_id=f"match-{i}")
            )

        await bot.scan_and_trade()
        assert bot._last_scan_markets == 3
        assert bot._event_detector.detect.call_count == 3

    @pytest.mark.asyncio
    async def test_processes_max_20_game_states(self):
        """scan_and_trade processes at most 20 game states per cycle."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[])
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        # Put 25 game states in queue
        for i in range(25):
            await bot._game_update_queue.put(
                _make_game_state(match_id=f"match-{i}")
            )

        await bot.scan_and_trade()
        # Only 20 should be processed
        assert bot._last_scan_markets == 20
        assert bot._event_detector.detect.call_count == 20
        # 5 remain in queue
        assert bot._game_update_queue.qsize() == 5

    @pytest.mark.asyncio
    async def test_detects_events_via_event_detector(self):
        """scan_and_trade uses event_detector.detect() on each game state."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._live_trigger.process_event = AsyncMock(return_value=None)
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        game_state = _make_game_state()
        live_event = _make_live_event()
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[live_event])

        await bot._game_update_queue.put(game_state)
        await bot.scan_and_trade()

        bot._event_detector.detect.assert_called_once_with(game_state)
        assert bot._last_scan_opportunities == 1

    @pytest.mark.asyncio
    async def test_fires_live_trigger_for_each_event(self):
        """scan_and_trade calls live_trigger.process_event for each detected event."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._live_trigger.process_event = AsyncMock(return_value=None)
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        event_1 = _make_live_event(event_type="economy_break")
        event_2 = _make_live_event(event_type="round_streak")
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[event_1, event_2])

        await bot._game_update_queue.put(_make_game_state())
        await bot.scan_and_trade()

        assert bot._live_trigger.process_event.await_count == 2
        assert bot._last_scan_opportunities == 2

    @pytest.mark.asyncio
    async def test_counts_trades_when_trigger_returns_result(self):
        """scan_and_trade increments _last_scan_trades when live_trigger returns truthy."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._live_trigger.process_event = AsyncMock(return_value={"trade": "placed"})
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[_make_live_event()])

        await bot._game_update_queue.put(_make_game_state())
        await bot.scan_and_trade()

        assert bot._last_scan_trades == 1

    @pytest.mark.asyncio
    async def test_handles_timeout_from_live_trigger(self):
        """scan_and_trade handles asyncio.TimeoutError from live_trigger gracefully."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._live_trigger.process_event = AsyncMock(side_effect=asyncio.TimeoutError)
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[_make_live_event()])

        await bot._game_update_queue.put(_make_game_state())
        # Should not raise
        await bot.scan_and_trade()
        assert bot._last_scan_trades == 0
        assert bot._last_scan_markets == 1

    @pytest.mark.asyncio
    async def test_handles_exception_from_live_trigger(self):
        """scan_and_trade handles generic exceptions from live_trigger gracefully."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._live_trigger.process_event = AsyncMock(
            side_effect=RuntimeError("trigger error")
        )
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[_make_live_event()])

        await bot._game_update_queue.put(_make_game_state())
        # Should not raise
        await bot.scan_and_trade()
        assert bot._last_scan_trades == 0
        assert bot._last_scan_markets == 1

    @pytest.mark.asyncio
    async def test_continues_processing_after_trigger_error(self):
        """After live_trigger error on event 1, event 2 is still processed."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()

        call_count = 0

        async def flaky_process_event(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("flaky error")
            return {"trade": "placed"}

        bot._live_trigger.process_event = flaky_process_event
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        event_1 = _make_live_event(event_type="economy_break")
        event_2 = _make_live_event(event_type="round_streak")
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[event_1, event_2])

        await bot._game_update_queue.put(_make_game_state())
        await bot.scan_and_trade()

        assert call_count == 2
        assert bot._last_scan_trades == 1  # Only second event succeeded

    @pytest.mark.asyncio
    async def test_updates_scan_metrics(self):
        """scan_and_trade updates _last_scan_markets, _last_scan_opportunities, _last_scan_trades."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._live_trigger.process_event = AsyncMock(return_value={"trade": "done"})
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        # 2 game states, each producing 1 event, all trades succeed
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[_make_live_event()])

        await bot._game_update_queue.put(_make_game_state(match_id="match-1"))
        await bot._game_update_queue.put(_make_game_state(match_id="match-2"))
        await bot.scan_and_trade()

        assert bot._last_scan_markets == 2
        assert bot._last_scan_opportunities == 2
        assert bot._last_scan_trades == 2

    @pytest.mark.asyncio
    async def test_no_event_detector_skips_detection(self):
        """scan_and_trade skips event detection when _event_detector is None."""
        bot = make_bot()
        bot._live_trigger = MagicMock()
        bot._live_trigger.prune_cooldowns = MagicMock()
        bot._event_detector = None
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        await bot._game_update_queue.put(_make_game_state())
        await bot.scan_and_trade()

        assert bot._last_scan_markets == 1
        assert bot._last_scan_opportunities == 0

    @pytest.mark.asyncio
    async def test_no_live_trigger_skips_trade(self):
        """scan_and_trade detects events but skips trade when _live_trigger is None."""
        bot = make_bot()
        bot._live_trigger = None
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}

        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[_make_live_event()])

        await bot._game_update_queue.put(_make_game_state())
        await bot.scan_and_trade()

        assert bot._last_scan_markets == 1
        assert bot._last_scan_opportunities == 1
        assert bot._last_scan_trades == 0

    @pytest.mark.asyncio
    async def test_no_prune_when_no_live_trigger(self):
        """scan_and_trade does not crash when _live_trigger is None (no prune_cooldowns)."""
        bot = make_bot()
        bot._live_trigger = None
        bot._event_detector = MagicMock()
        bot._event_detector.detect = MagicMock(return_value=[])
        bot._game_monitor = MagicMock()
        bot._game_monitor.active_games = {}
        # Should not raise
        await bot.scan_and_trade()


# =========================================================================
# start
# =========================================================================


class TestStart:
    @pytest.mark.asyncio
    async def test_start_initializes_components(self):
        """start() initializes game_monitor, event_detector, live_trigger, scanner, bankroll_mgr."""
        bot = make_bot()

        mock_pandascore_cls = MagicMock()
        mock_pandascore_inst = MagicMock()
        mock_pandascore_inst.init = AsyncMock()
        mock_pandascore_cls.return_value = mock_pandascore_inst

        mock_monitor_cls = MagicMock()
        mock_monitor_inst = MagicMock()
        mock_monitor_inst.run_forever = AsyncMock()
        mock_monitor_cls.return_value = mock_monitor_inst

        mock_detector_cls = MagicMock()
        mock_detector_inst = MagicMock()
        mock_detector_cls.return_value = mock_detector_inst

        mock_trigger_cls = MagicMock()
        mock_trigger_inst = MagicMock()
        mock_trigger_cls.return_value = mock_trigger_inst

        mock_scanner_cls = MagicMock()
        mock_scanner_inst = MagicMock()
        mock_scanner_cls.return_value = mock_scanner_inst

        mock_bankroll_cls = MagicMock()
        mock_bankroll_inst = MagicMock()
        mock_bankroll_cls.return_value = mock_bankroll_inst

        with patch(
            "esports.data.pandascore_client.PandaScoreClient", mock_pandascore_cls
        ), patch(
            "esports.live.esports_game_monitor.EsportsGameMonitor", mock_monitor_cls
        ), patch(
            "esports.live.esports_event_detector.EsportsEventDetector", mock_detector_cls
        ), patch(
            "esports.live.esports_live_trigger.EsportsLiveTrigger", mock_trigger_cls
        ), patch(
            "esports.markets.esports_market_scanner.EsportsMarketScanner", mock_scanner_cls
        ), patch(
            "esports.kelly.esports_bankroll_manager.EsportsBankrollManager", mock_bankroll_cls
        ), patch(
            "esports.models.lol_win_model.LoLWinModel"
        ) as mock_lol, patch(
            "esports.models.cs2_economy_model.CS2EconomyModel"
        ) as mock_cs2:
            mock_lol.return_value.load.return_value = False
            mock_cs2.return_value.load.return_value = False
            await bot.start()

        assert bot._game_monitor is mock_monitor_inst
        assert bot._event_detector is mock_detector_inst
        assert bot._live_trigger is mock_trigger_inst
        assert bot._scanner is mock_scanner_inst
        assert bot._bankroll_mgr is mock_bankroll_inst
        assert bot._monitor_task is not None
        assert bot.running is True

        # Cleanup: cancel the monitor task
        bot._monitor_task.cancel()
        try:
            await bot._monitor_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_calls_pandascore_init(self):
        """start() calls PandaScoreClient.init()."""
        bot = make_bot()

        mock_pandascore_cls = MagicMock()
        mock_pandascore_inst = MagicMock()
        mock_pandascore_inst.init = AsyncMock()
        mock_pandascore_cls.return_value = mock_pandascore_inst

        mock_monitor_cls = MagicMock()
        mock_monitor_inst = MagicMock()
        mock_monitor_inst.run_forever = AsyncMock()
        mock_monitor_cls.return_value = mock_monitor_inst

        with patch(
            "esports.data.pandascore_client.PandaScoreClient", mock_pandascore_cls
        ), patch(
            "esports.live.esports_game_monitor.EsportsGameMonitor", mock_monitor_cls
        ), patch(
            "esports.live.esports_event_detector.EsportsEventDetector"
        ), patch(
            "esports.live.esports_live_trigger.EsportsLiveTrigger"
        ), patch(
            "esports.markets.esports_market_scanner.EsportsMarketScanner"
        ), patch(
            "esports.kelly.esports_bankroll_manager.EsportsBankrollManager"
        ), patch(
            "esports.models.lol_win_model.LoLWinModel"
        ) as mock_lol, patch(
            "esports.models.cs2_economy_model.CS2EconomyModel"
        ) as mock_cs2:
            mock_lol.return_value.load.return_value = False
            mock_cs2.return_value.load.return_value = False
            await bot.start()

        mock_pandascore_inst.init.assert_awaited_once()

        # Cleanup
        bot._monitor_task.cancel()
        try:
            await bot._monitor_task
        except asyncio.CancelledError:
            pass


# =========================================================================
# stop
# =========================================================================


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_monitor_task(self):
        """stop() cancels the monitor task and calls game_monitor.stop()."""
        bot = make_bot()
        bot._game_monitor = MagicMock()
        bot._game_monitor.stop = AsyncMock()
        bot.running = True

        # Create a real async task to cancel
        async def dummy():
            await asyncio.sleep(100)

        bot._monitor_task = asyncio.create_task(dummy())
        # Also need scan_task for BaseBot.stop()
        bot.scan_task = None

        await bot.stop()

        bot._game_monitor.stop.assert_awaited_once()
        assert bot._monitor_task.cancelled() or bot._monitor_task.done()
        assert bot.running is False

    @pytest.mark.asyncio
    async def test_stop_without_monitor_task(self):
        """stop() does not crash when monitor task is None."""
        bot = make_bot()
        bot._game_monitor = None
        bot._monitor_task = None
        bot.scan_task = None
        bot.running = True
        await bot.stop()
        assert bot.running is False

    @pytest.mark.asyncio
    async def test_stop_with_already_done_task(self):
        """stop() handles monitor task that is already done."""
        bot = make_bot()
        bot._game_monitor = MagicMock()
        bot._game_monitor.stop = AsyncMock()
        bot.running = True
        bot.scan_task = None

        # Create a task that finishes immediately
        async def instant():
            return None

        bot._monitor_task = asyncio.create_task(instant())
        await asyncio.sleep(0.01)  # let it finish
        assert bot._monitor_task.done()

        await bot.stop()
        assert bot.running is False


# =========================================================================
# analyze_opportunity
# =========================================================================


class TestAnalyzeOpportunity:
    @pytest.mark.asyncio
    async def test_always_returns_none(self):
        """analyze_opportunity always returns None (event-driven bot)."""
        bot = make_bot()
        result = await bot.analyze_opportunity({"id": "m1", "question": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_with_empty_dict(self):
        """analyze_opportunity returns None even with empty dict."""
        bot = make_bot()
        result = await bot.analyze_opportunity({})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_with_full_market_data(self):
        """analyze_opportunity returns None regardless of market data content."""
        bot = make_bot()
        market = {
            "id": "m1",
            "question": "Will Team A win the LoL match?",
            "tokens": [
                {"tokenId": "tok-yes", "outcomePrice": "0.50"},
                {"tokenId": "tok-no", "outcomePrice": "0.50"},
            ],
        }
        result = await bot.analyze_opportunity(market)
        assert result is None


# =========================================================================
# _on_bg_task_done
# =========================================================================


class TestOnBgTaskDone:
    def test_cancelled_task_no_log(self):
        """_on_bg_task_done does not log when task is cancelled."""
        bot = make_bot()
        task = MagicMock()
        task.cancelled.return_value = True
        with patch("bots.esports_live_bot.logger") as mock_logger:
            bot._on_bg_task_done(task, "test_task")
            mock_logger.warning.assert_not_called()

    def test_failed_task_logs_warning(self):
        """_on_bg_task_done logs warning when task has exception and schedules restart."""
        bot = make_bot()
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("task failed")
        mock_loop = MagicMock()
        with patch("bots.esports_live_bot.logger") as mock_logger, \
             patch("bots.esports_live_bot.asyncio.get_event_loop", return_value=mock_loop):
            bot._on_bg_task_done(task, "test_task")
            mock_logger.warning.assert_called_once()
            call_kwargs = mock_logger.warning.call_args[1]
            assert call_kwargs["task_name"] == "test_task"
            assert "task failed" in call_kwargs["error"]
            # Verify restart was scheduled
            mock_loop.call_later.assert_called_once()
            assert bot._monitor_restart_count == 1

    def test_successful_task_no_log(self):
        """_on_bg_task_done does not log when task succeeded (no exception)."""
        bot = make_bot()
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None
        with patch("bots.esports_live_bot.logger") as mock_logger:
            bot._on_bg_task_done(task, "test_task")
            mock_logger.warning.assert_not_called()
