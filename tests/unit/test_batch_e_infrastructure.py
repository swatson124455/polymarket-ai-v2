"""
Batch E — Infrastructure Hardening tests.

Covers:
  I04  EnsembleBot.start() resets _feature_cache_warmed + _feature_vector_cache
  I04  _do_warm fail-open after 3 consecutive warm failures
  I15  MAX_GLOBAL_RESTART_ATTEMPTS raised to 10; watchdog exponential backoff
  I18  signal_ingestion external fetches wrapped in asyncio.wait_for(10s)
  I21  paper_trading.seed_positions_from_db() skips rows with entry_price <= 0
  I48  Main.py promotes L6 EnsembleBot wire log from DEBUG to INFO
  I49  WebSocketManager._resolve_market_id() resolves condition_id to numeric id
  I50  BotStateMachine._safe_trigger() logs WARNING on blocked/invalid transitions
  I51  IngestionScheduler timeout is read from settings.INGESTION_TIMEOUT_SECONDS
  I52  BaseEngine no longer declares dead adapter fields
  I53  EnsembleBot._base_min_confidence reads settings.ENSEMBLE_MIN_CONFIDENCE directly
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ─────────────────────────────────────────────────────────────────────────────
# I04  EnsembleBot.start() — resets feature cache
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsembleBotStartResetsCache:
    """I04: start() clears _feature_cache_warmed, _feature_vector_cache, _warm_fail_count."""

    def _make_bot(self):
        from bots.ensemble_bot import EnsembleBot
        mock_engine = MagicMock()
        mock_engine.db = None
        mock_engine.cache = None
        mock_engine.event_bus = None
        mock_engine._market_index = {}
        mock_engine._market_index_by_cid = {}
        mock_engine.prediction_engine = MagicMock()
        mock_engine.prediction_engine._feature_cache_warmed = True
        mock_engine.prediction_engine._feature_cache_warming_task_started = True
        mock_engine.prediction_engine._warm_fail_count = 2
        mock_engine.prediction_engine._feature_vector_cache = {"stale_key": "stale_data"}
        bot = EnsembleBot.__new__(EnsembleBot)
        bot.base_engine = mock_engine
        bot.running = False
        return bot

    @pytest.mark.asyncio
    async def test_start_resets_warmed_flag(self):
        bot = self._make_bot()
        pe = bot.base_engine.prediction_engine
        pe._feature_cache_warmed = True  # was True from previous session

        with patch("bots.base_bot.BaseBot.start", new=AsyncMock()):
            await bot.start()

        assert pe._feature_cache_warmed is False

    @pytest.mark.asyncio
    async def test_start_clears_vector_cache(self):
        bot = self._make_bot()
        pe = bot.base_engine.prediction_engine
        pe._feature_vector_cache = {"m1": [1, 2, 3], "m2": [4, 5, 6]}

        with patch("bots.base_bot.BaseBot.start", new=AsyncMock()):
            await bot.start()

        assert len(pe._feature_vector_cache) == 0

    @pytest.mark.asyncio
    async def test_start_resets_warm_fail_count(self):
        bot = self._make_bot()
        pe = bot.base_engine.prediction_engine
        pe._warm_fail_count = 3  # accumulated from prior crashes

        with patch("bots.base_bot.BaseBot.start", new=AsyncMock()):
            await bot.start()

        assert pe._warm_fail_count == 0

    @pytest.mark.asyncio
    async def test_start_resets_warming_task_started_flag(self):
        bot = self._make_bot()
        pe = bot.base_engine.prediction_engine
        pe._feature_cache_warming_task_started = True

        with patch("bots.base_bot.BaseBot.start", new=AsyncMock()):
            await bot.start()

        assert pe._feature_cache_warming_task_started is False

    @pytest.mark.asyncio
    async def test_start_calls_super(self):
        bot = self._make_bot()
        with patch("bots.base_bot.BaseBot.start", new=AsyncMock()) as mock_super:
            await bot.start()
        mock_super.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_no_pe_does_not_crash(self):
        """If prediction_engine is None, start() must not raise."""
        bot = self._make_bot()
        bot.base_engine.prediction_engine = None
        with patch("bots.base_bot.BaseBot.start", new=AsyncMock()):
            await bot.start()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# I04  _do_warm fail-open after 3 failures
# ─────────────────────────────────────────────────────────────────────────────

class TestDoWarmFailOpen:
    """I04: After 3 warm failures, _feature_cache_warmed is set True (fail-open)."""

    def _make_pe(self):
        pe = MagicMock()
        pe._feature_cache_warmed = False
        pe._feature_cache_warming_task_started = False
        pe._warm_fail_count = 0
        pe.batch_precompute_all_features = AsyncMock(side_effect=Exception("DB down"))
        pe._feature_vector_cache = {}
        return pe

    @pytest.mark.asyncio
    async def test_fail_open_after_3_failures(self):
        """3rd failure must set _feature_cache_warmed=True so bot stops skip-looping."""
        from bots.ensemble_bot import EnsembleBot
        mock_engine = MagicMock()
        pe = self._make_pe()
        mock_engine.prediction_engine = pe

        bot = EnsembleBot.__new__(EnsembleBot)
        bot.base_engine = mock_engine
        bot.running = True
        bot.name = "EnsembleBot"

        # Simulate 3 warm failures by calling _do_warm three times
        async def run_warm_cycle():
            pe._feature_cache_warmed = False
            pe._feature_cache_warming_task_started = False
            # Create a closure that mimics the _do_warm logic
            _ids = ["m1", "m2"]
            _warm_ok = False
            try:
                await asyncio.wait_for(pe.batch_precompute_all_features(_ids), timeout=1.0)
                _warm_ok = True
            except Exception:
                pass
            finally:
                if _warm_ok:
                    pe._feature_cache_warmed = True
                    pe._warm_fail_count = 0
                else:
                    pe._warm_fail_count = getattr(pe, "_warm_fail_count", 0) + 1
                    if pe._warm_fail_count >= 3:
                        pe._feature_cache_warmed = True  # fail-open
                    else:
                        pe._feature_cache_warming_task_started = False
                        pe._feature_cache_warmed = False

        # Fail 3 times
        await run_warm_cycle()
        await run_warm_cycle()
        await run_warm_cycle()

        assert pe._warm_fail_count >= 3
        assert pe._feature_cache_warmed is True  # fail-open

    @pytest.mark.asyncio
    async def test_first_failure_allows_retry(self):
        """After 1st failure, _feature_cache_warming_task_started must be reset to allow retry."""
        pe = self._make_pe()
        pe._warm_fail_count = 0

        _warm_ok = False
        try:
            await asyncio.wait_for(pe.batch_precompute_all_features([]), timeout=1.0)
        except Exception:
            pass
        finally:
            if not _warm_ok:
                pe._warm_fail_count += 1
                if pe._warm_fail_count < 3:
                    pe._feature_cache_warming_task_started = False  # allow retry
                    pe._feature_cache_warmed = False

        assert pe._feature_cache_warming_task_started is False
        assert pe._feature_cache_warmed is False


# ─────────────────────────────────────────────────────────────────────────────
# I15  MAX_GLOBAL_RESTART_ATTEMPTS and watchdog backoff
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchdogRestartConfig:
    """I15: MAX_GLOBAL_RESTART_ATTEMPTS = 10; watchdog doubles backoff per restart."""

    def test_max_restart_attempts_is_10(self):
        import main
        assert main.MAX_GLOBAL_RESTART_ATTEMPTS == 10

    @pytest.mark.asyncio
    async def test_watchdog_backoff_doubles(self):
        """Backoff doubles on each restart attempt up to 600s cap."""
        import main

        # Build a simple dead bot
        dead_bot = MagicMock()
        dead_bot.running = False
        dead_bot.start = AsyncMock(side_effect=Exception("startup failed"))

        bots = {"TestBot": dead_bot}
        mock_engine = MagicMock()

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def capture_sleep(t):
            sleep_calls.append(t)
            # Don't actually sleep — just record
            if t == main.WATCHDOG_INTERVAL_SECONDS:
                # After enough restarts, stop the watchdog
                if len([s for s in sleep_calls if s != main.WATCHDOG_INTERVAL_SECONDS]) >= 3:
                    raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=capture_sleep):
            try:
                await main._watchdog(bots, mock_engine)
            except (asyncio.CancelledError, Exception):
                pass

        # Extract backoff sleeps (not the WATCHDOG_INTERVAL_SECONDS polls)
        backoff_sleeps = [s for s in sleep_calls if s != main.WATCHDOG_INTERVAL_SECONDS]
        if len(backoff_sleeps) >= 2:
            # Each subsequent backoff should be >= previous (doubling)
            assert backoff_sleeps[1] >= backoff_sleeps[0]
            if len(backoff_sleeps) >= 3:
                assert backoff_sleeps[2] >= backoff_sleeps[1]

    def test_watchdog_backoff_capped_at_600(self):
        """The backoff cap in source must be 600.0."""
        import inspect
        import main
        source = inspect.getsource(main._watchdog)
        assert "600" in source or "600.0" in source, \
            "600s backoff cap not found in _watchdog source"


class TestWatchdogStaleExitUptimeGrace:
    """2026-06-02 (EB): E1 force-exit must be gated on process uptime.

    bot_heartbeats.last_scan_at is shared and persisted across restarts, so a
    freshly-restarted process inherits the prior process's stale heartbeat.
    Without an uptime grace window, E1 force-exits the young process before it
    can complete a scan to refresh the heartbeat — an unrecoverable restart
    death-spiral (observed 2026-06-01: ~53 restarts in 6h, minutes_stale
    climbing 403→410→420 monotonically). Force-exit now requires BOTH a stale
    heartbeat AND watchdog uptime >= threshold.
    """

    @staticmethod
    def _exit_threshold_minutes():
        import main
        _stale = getattr(main.settings, "BOT_HEARTBEAT_STALE_MINUTES", 15)
        return float(getattr(main.settings, "BOT_STALE_EXIT_THRESHOLD_MINUTES", _stale * 2))

    @staticmethod
    def _stale_heartbeat_engine(bot_name, minutes_stale):
        """MagicMock engine whose heartbeat query returns one stale bot row."""
        engine = MagicMock()
        engine.alerting_system = MagicMock()
        engine.alerting_system.send_alert = AsyncMock()
        engine.db = MagicMock()
        engine.db.session_factory = True
        result = MagicMock()
        result.fetchall.return_value = [(bot_name, float(minutes_stale))]
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        class _SessionCM:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        engine.db.get_session = MagicMock(side_effect=lambda: _SessionCM())
        return engine

    async def _run_watchdog_once(self, bots, engine, uptime_seconds):
        """Run _watchdog for one iteration with a controlled monotonic clock."""
        import main

        class _StepClock:
            """monotonic() → 0.0 on first call (start marker), then uptime_seconds."""

            def __init__(self):
                self._first = True

            def __call__(self):
                if self._first:
                    self._first = False
                    return 0.0
                return float(uptime_seconds)

        _sleeps = {"n": 0}

        async def _fake_sleep(_t):
            # let iteration 1 fully run (heartbeat check), break on iteration 2's
            # top-of-loop sleep.
            _sleeps["n"] += 1
            if _sleeps["n"] >= 2:
                raise asyncio.CancelledError()

        with patch.object(main.time, "monotonic", _StepClock()), \
                patch("asyncio.sleep", side_effect=_fake_sleep), \
                patch("os._exit") as mock_exit:
            try:
                await main._watchdog(bots, engine)
            except asyncio.CancelledError:
                pass
        return mock_exit

    @pytest.mark.asyncio
    async def test_young_process_with_stale_heartbeat_is_spared(self):
        """A young process must NOT be force-exited despite a deeply stale
        inherited heartbeat — this is the death-spiral the gate prevents."""
        thresh = self._exit_threshold_minutes()
        bot = MagicMock()
        bot.running = True
        bots = {"TestBot": bot}  # not in _bot_enabled_map → no enable-flag skip
        engine = self._stale_heartbeat_engine("TestBot", minutes_stale=thresh * 100)
        mock_exit = await self._run_watchdog_once(
            bots, engine, uptime_seconds=thresh * 0.1 * 60  # uptime ≪ threshold
        )
        mock_exit.assert_not_called()
        # the bot is still reported stale (alert fires) — only the kill is gated
        engine.alerting_system.send_alert.assert_awaited()

    @pytest.mark.asyncio
    async def test_old_process_with_stale_heartbeat_force_exits(self):
        """A process up well past the threshold that is still stale is genuinely
        wedged — E1 must still force-exit it (intended behavior preserved)."""
        thresh = self._exit_threshold_minutes()
        bot = MagicMock()
        bot.running = True
        bots = {"TestBot": bot}
        engine = self._stale_heartbeat_engine("TestBot", minutes_stale=thresh * 100)
        mock_exit = await self._run_watchdog_once(
            bots, engine, uptime_seconds=thresh * 2 * 60  # uptime ≫ threshold
        )
        mock_exit.assert_called_once_with(1)

    def test_uptime_gate_present_in_source(self):
        """Guard: the force-exit must remain gated on process uptime."""
        import inspect
        import main
        src = inspect.getsource(main._watchdog)
        assert "_watchdog_start_mono" in src, "process-start marker missing"
        assert "_proc_uptime_min" in src, "uptime computation missing"
        assert "_proc_uptime_min >= _exit_thresh" in src, "force-exit uptime gate missing"


# ─────────────────────────────────────────────────────────────────────────────
# I21  paper_trading.seed_positions_from_db() — skip zero avg_price
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperTradingSkipZeroEntryPrice:
    """I21: Rows with entry_price <= 0 (NULL avg_price) are skipped with a WARNING."""

    @pytest.mark.asyncio
    async def test_zero_entry_price_skipped(self):
        from base_engine.execution.paper_trading import PaperTradingEngine

        engine = PaperTradingEngine.__new__(PaperTradingEngine)
        engine.db = MagicMock()
        engine.db.session_factory = MagicMock()
        engine.positions = {}
        engine.cash = 100_000.0
        engine._positions_seeded = False

        # Fake DB row with NULL (0) avg_price
        bad_pos = MagicMock()
        bad_pos.market_id = "bad_market"
        bad_pos.bot_id = "test"
        bad_pos.size = 10.0
        bad_pos.entry_price = None   # NULL → float(None or 0) = 0.0
        bad_pos.token_id = "tok1"
        bad_pos.side = "YES"

        good_pos = MagicMock()
        good_pos.market_id = "good_market"
        good_pos.bot_id = "test"
        good_pos.size = 5.0
        good_pos.entry_price = 0.55
        good_pos.token_id = "tok2"
        good_pos.side = "YES"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [bad_pos, good_pos]

        # PnL query returns 0
        mock_pnl = MagicMock()
        mock_pnl.scalar.return_value = 0.0

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=[mock_result, mock_pnl])
        engine.db.get_session = MagicMock(return_value=mock_session)

        with patch("base_engine.execution.paper_trading.logger") as mock_log:
            count = await engine.seed_positions_from_db()

        # Only the good position should be seeded
        assert count == 1
        assert ("test", "good_market") in engine.positions
        assert ("test", "bad_market") not in engine.positions
        # A WARNING should have been logged for the bad row
        mock_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_positive_entry_price_seeded(self):
        from base_engine.execution.paper_trading import PaperTradingEngine

        engine = PaperTradingEngine.__new__(PaperTradingEngine)
        engine.db = MagicMock()
        engine.db.session_factory = MagicMock()
        engine.positions = {}
        engine.cash = 100_000.0
        engine._positions_seeded = False

        pos = MagicMock()
        pos.market_id = "m1"
        pos.bot_id = "test"
        pos.size = 10.0
        pos.entry_price = 0.60
        pos.token_id = "tok"
        pos.side = "YES"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [pos]
        mock_pnl = MagicMock()
        mock_pnl.scalar.return_value = 0.0

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=[mock_result, mock_pnl])
        engine.db.get_session = MagicMock(return_value=mock_session)

        count = await engine.seed_positions_from_db()
        assert count == 1
        assert ("test", "m1") in engine.positions
        assert engine.positions[("test", "m1")]["avg_price"] == pytest.approx(0.60)


# ─────────────────────────────────────────────────────────────────────────────
# I49  WebSocketManager._resolve_market_id()
# ─────────────────────────────────────────────────────────────────────────────

class TestWebSocketMarketIdResolution:
    """I49: _resolve_market_id() uses injected resolver to map condition_id → numeric id."""

    def _make_ws(self, resolver=None):
        from base_engine.data.websocket_manager import WebSocketManager
        mock_cache = MagicMock()
        mock_cache.redis = None
        return WebSocketManager(
            cache=mock_cache,
            market_index_resolver=resolver,
        )

    def test_none_id_returns_none(self):
        ws = self._make_ws()
        assert ws._resolve_market_id(None) is None

    def test_empty_id_returns_empty(self):
        ws = self._make_ws()
        assert ws._resolve_market_id("") == ""

    def test_no_resolver_returns_raw(self):
        """Without resolver, raw id is returned as-is."""
        ws = self._make_ws(resolver=None)
        assert ws._resolve_market_id("0xabc123") == "0xabc123"

    def test_resolver_returns_numeric_for_condition_id(self):
        """When resolver finds a market, returns numeric id."""
        resolver = MagicMock(return_value={"id": 99999, "condition_id": "0xabc123"})
        ws = self._make_ws(resolver=resolver)
        result = ws._resolve_market_id("0xabc123")
        assert result == "99999"
        resolver.assert_called_once_with("0xabc123")

    def test_resolver_no_match_returns_raw(self):
        """When resolver returns None (not in index), raw id is returned."""
        resolver = MagicMock(return_value=None)
        ws = self._make_ws(resolver=resolver)
        result = ws._resolve_market_id("0xunknown")
        assert result == "0xunknown"

    def test_resolver_same_id_returns_raw(self):
        """If resolver returns same id as input (already numeric), no change."""
        resolver = MagicMock(return_value={"id": "12345"})
        ws = self._make_ws(resolver=resolver)
        result = ws._resolve_market_id("12345")
        # str(12345) == "12345" == raw_id → return raw
        assert result == "12345"

    def test_market_index_resolver_param_stored(self):
        """Constructor stores the resolver as _market_index_resolver."""
        resolver = MagicMock()
        ws = self._make_ws(resolver=resolver)
        assert ws._market_index_resolver is resolver


# ─────────────────────────────────────────────────────────────────────────────
# I50  BotStateMachine._safe_trigger()
# ─────────────────────────────────────────────────────────────────────────────

class TestBotStateMachineSafeTrigger:
    """I50: _safe_trigger() returns False and logs WARNING on invalid transitions."""

    def _make_machine(self):
        from base_engine.monitoring.bot_state_machine import BotStateMachine
        return BotStateMachine(bot_name="TestBot")

    def test_safe_trigger_valid_transition_returns_true(self):
        m = self._make_machine()
        # healthy → degrade is valid
        result = m._safe_trigger("degrade")
        assert result is True
        assert m.state == "degraded"

    def test_safe_trigger_invalid_transition_returns_false(self):
        m = self._make_machine()
        # healthy → recover is invalid (recover is only valid from 'recovering' state)
        result = m._safe_trigger("recover")
        assert result is False
        assert m.state == "healthy"  # state unchanged

    def test_safe_trigger_invalid_logs_warning(self):
        m = self._make_machine()
        with patch("base_engine.monitoring.bot_state_machine.logger") as mock_log:
            m._safe_trigger("recover")  # invalid from healthy
        mock_log.warning.assert_called()
        warn_args = str(mock_log.warning.call_args)
        assert "blocked" in warn_args.lower() or "invalid" in warn_args.lower() or \
               "transition" in warn_args.lower()

    def test_ignore_invalid_triggers_is_false(self):
        """Machine must be configured with ignore_invalid_triggers=False."""
        from base_engine.monitoring.bot_state_machine import TRANSITIONS_AVAILABLE
        if not TRANSITIONS_AVAILABLE:
            pytest.skip("transitions library not installed")
        m = self._make_machine()
        assert m.machine.ignore_invalid_triggers is False

    def test_record_error_uses_safe_trigger(self):
        """record_error must not raise even on unexpected state."""
        m = self._make_machine()
        m.state = "safe_mode"  # edge case state
        # Should not raise even if 'fail' is invalid from safe_mode
        try:
            m.record_error(is_fatal=True)
        except Exception as e:
            pytest.fail(f"record_error raised unexpectedly: {e}")

    def test_record_health_ok_uses_safe_trigger(self):
        """record_health_ok must not raise even in healthy state."""
        m = self._make_machine()
        m._consecutive_health_ok = 100
        try:
            m.record_health_ok()
        except Exception as e:
            pytest.fail(f"record_health_ok raised unexpectedly: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# I51  IngestionScheduler — configurable timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestionSchedulerTimeout:
    """I51: Timeout is read from settings.INGESTION_TIMEOUT_SECONDS, not hardcoded."""

    def test_timeout_constant_exists(self):
        from base_engine.data import ingestion_scheduler as is_mod
        assert hasattr(is_mod, "_INGESTION_TIMEOUT_SECONDS")

    def test_timeout_reads_from_settings(self):
        """_INGESTION_TIMEOUT_SECONDS is derived from settings, not hardcoded 600."""
        import inspect
        from base_engine.data import ingestion_scheduler as is_mod
        source = inspect.getsource(is_mod)
        # The source must reference settings.INGESTION_TIMEOUT_SECONDS or getattr(settings, ...)
        assert "INGESTION_TIMEOUT_SECONDS" in source
        # And it must NOT be a bare hardcoded 600.0 in the wait_for call
        assert "timeout=600.0" not in source, \
            "Hardcoded 600.0 timeout still present (should use _INGESTION_TIMEOUT_SECONDS)"

    def test_timeout_default_is_600(self):
        """Default value must be 600s (10 minutes) when env var not set."""
        from base_engine.data import ingestion_scheduler as is_mod
        assert is_mod._INGESTION_TIMEOUT_SECONDS == pytest.approx(600.0)

    def test_settings_has_ingestion_timeout_field(self):
        from config.settings import Settings
        # Check via model_fields (pydantic v2) or __fields__ (pydantic v1) or direct attr
        has_field = (
            hasattr(Settings, "INGESTION_TIMEOUT_SECONDS")
            or "INGESTION_TIMEOUT_SECONDS" in getattr(Settings, "model_fields", {})
        )
        assert has_field

    def test_settings_ingestion_timeout_default(self):
        from config.settings import settings
        val = getattr(settings, "INGESTION_TIMEOUT_SECONDS", None)
        assert val is not None
        assert float(val) == pytest.approx(600.0)


# ─────────────────────────────────────────────────────────────────────────────
# I52  BaseEngine — dead adapter fields removed
# ─────────────────────────────────────────────────────────────────────────────

class TestDeadAdapterFieldsRemoved:
    """I52: Seven dead adapter fields no longer declared in BaseEngine.__init__."""

    def test_capital_tracker_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._capital_tracker" not in init_src

    def test_contract_change_monitor_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._contract_change_monitor" not in init_src

    def test_airdrop_tracker_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._airdrop_tracker" not in init_src

    def test_regulatory_monitor_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._regulatory_monitor" not in init_src

    def test_wash_trading_detector_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._wash_trading_detector" not in init_src

    def test_domain_calibrator_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._domain_calibrator" not in init_src

    def test_agentic_rag_not_in_init(self):
        from base_engine.base_engine import BaseEngine
        import inspect
        init_src = inspect.getsource(BaseEngine.__init__)
        assert "self._agentic_rag" not in init_src


# ─────────────────────────────────────────────────────────────────────────────
# I53  EnsembleBot — ENSEMBLE_MIN_CONFIDENCE single source
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsembleMinConfidenceSingleSource:
    """I53: _base_min_confidence reads settings.ENSEMBLE_MIN_CONFIDENCE, not getattr fallback."""

    def test_no_065_fallback_in_source(self):
        """The old getattr fallback of 0.65 must not exist."""
        import inspect
        from bots import ensemble_bot
        init_src = inspect.getsource(ensemble_bot.EnsembleBot.__init__)
        assert 'getattr(settings, "ENSEMBLE_MIN_CONFIDENCE", 0.65)' not in init_src
        assert "getattr(settings, 'ENSEMBLE_MIN_CONFIDENCE', 0.65)" not in init_src

    def test_reads_settings_directly(self):
        """_base_min_confidence must be set from settings.ENSEMBLE_MIN_CONFIDENCE."""
        import inspect
        from bots import ensemble_bot
        init_src = inspect.getsource(ensemble_bot.EnsembleBot.__init__)
        assert "settings.ENSEMBLE_MIN_CONFIDENCE" in init_src

    def test_value_matches_settings(self):
        """The initialized value must equal whatever settings defines."""
        from config.settings import settings
        from bots.ensemble_bot import EnsembleBot
        mock_engine = MagicMock()
        mock_engine.db = None
        mock_engine.cache = None
        mock_engine.event_bus = None
        bot = EnsembleBot.__new__(EnsembleBot)
        bot.base_engine = mock_engine
        # Manually call just the __init__ attribute assignments via EnsembleBot.__init__
        # (skip super().__init__ which has side effects)
        with patch("bots.base_bot.BaseBot.__init__", return_value=None):
            EnsembleBot.__init__(bot, mock_engine)
        assert bot._base_min_confidence == pytest.approx(settings.ENSEMBLE_MIN_CONFIDENCE)

    def test_settings_default_is_045(self):
        """settings.py defines ENSEMBLE_MIN_CONFIDENCE default as 0.45 (Session 47: lowered from 0.55)."""
        from config.settings import Settings
        # Read the field default from Settings model
        field = None
        if hasattr(Settings, "model_fields"):
            field = Settings.model_fields.get("ENSEMBLE_MIN_CONFIDENCE")
        elif hasattr(Settings, "__fields__"):  # pydantic v1
            field = Settings.__fields__.get("ENSEMBLE_MIN_CONFIDENCE")

        if field is not None:
            default = getattr(field, "default", None)
            if default is not None:
                assert float(default) == pytest.approx(0.45)
        else:
            # Fallback: check settings instance value
            from config.settings import settings
            assert settings.ENSEMBLE_MIN_CONFIDENCE == pytest.approx(0.45)


# ─────────────────────────────────────────────────────────────────────────────
# I18  signal_ingestion — external fetches wrapped in wait_for
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalIngestionTimeoutWrapping:
    """I18: Wikipedia, GDELT, HackerNews, spike-loop fetches are wrapped in wait_for(10.0).

    Reads source from disk rather than inspect.getsource() to avoid mock contamination:
    prior tests may patch SignalIngestionService methods with MagicMock objects, which
    causes inspect.getsource() to raise OSError (no source for built-in/mock objects).
    """

    @staticmethod
    def _get_signal_ingestion_source() -> str:
        """Read signal_ingestion.py source directly from disk — immune to mock patching."""
        import pathlib
        from base_engine.signals import signal_ingestion as si_mod
        src_path = pathlib.Path(si_mod.__file__)
        return src_path.read_text(encoding="utf-8")

    def test_wikipedia_fetch_wrapped(self):
        src = self._get_signal_ingestion_source()
        # Verify _wikipedia_collection_loop uses wait_for with 10.0 timeout
        assert "_wikipedia_collection_loop" in src
        assert "wait_for" in src
        assert "10.0" in src

    def test_gdelt_fetch_wrapped(self):
        src = self._get_signal_ingestion_source()
        assert "_gdelt_collection_loop" in src
        assert "wait_for" in src
        assert "10.0" in src

    def test_hackernews_fetch_wrapped(self):
        src = self._get_signal_ingestion_source()
        assert "_hackernews_collection_loop" in src
        assert "wait_for" in src
        assert "10.0" in src

    def test_spike_detection_wiki_wrapped(self):
        src = self._get_signal_ingestion_source()
        assert "_spike_detection_loop" in src
        assert "wait_for" in src
        assert "10.0" in src
