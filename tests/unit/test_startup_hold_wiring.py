"""S194: Pin the startup-hold flag wiring across base_engine + 3 bots.

Pre-S194: mark_positions_seeded / mark_exposure_restored / mark_reconciliation_passed
existed as setters on BaseEngine but were never called from any code path. Every
restart of every bot hit the 120s watchdog timeout (base_engine.py:1298) and
forced ready-to-trade in degraded mode. These tests pin the wiring so future
regressions are loud.
"""
import inspect

import pytest


def _get_source(obj) -> str:
    """Return the source code for a module/method/class as a single string."""
    return inspect.getsource(obj)


class TestBaseEngineStartupHoldWiring:
    """base_engine.start() must mark positions_seeded + reconciliation_passed."""

    def test_start_calls_mark_positions_seeded_after_paper_trading_seed(self):
        """After paper_trading.seed_positions_from_db(), start() must call mark_positions_seeded()."""
        from base_engine import base_engine as be_mod

        src = _get_source(be_mod.BaseEngine.start)
        # The seed_positions_from_db call is inside start(); the mark must follow.
        seed_idx = src.find("paper_trading.seed_positions_from_db")
        mark_idx = src.find("self.mark_positions_seeded()")
        assert seed_idx >= 0, "paper_trading.seed_positions_from_db() must remain in start()"
        assert mark_idx >= 0, "S194: start() must call self.mark_positions_seeded()"
        assert mark_idx > seed_idx, \
            "mark_positions_seeded() must be called AFTER paper_trading.seed_positions_from_db()"

    def test_start_calls_mark_reconciliation_passed(self):
        """start() must call mark_reconciliation_passed() — both branches.

        Pre-S194 the reconciler init was wrapped in try/except with no flag-set
        on either success or failure → flag never fired → 120s timeout.
        Post-S194: mark_reconciliation_passed() fires on success, on failure
        (so the 120s watchdog doesn't punish a non-critical init), and when no
        reconciler is configured at all.
        """
        from base_engine import base_engine as be_mod

        src = _get_source(be_mod.BaseEngine.start)
        # Should appear at least 3 times: success path, except path, else (no reconciler) path
        assert src.count("self.mark_reconciliation_passed()") >= 3, \
            "S194: mark_reconciliation_passed() must fire on success, exception, AND missing-reconciler paths"


class TestWeatherBotExposureRestoreWiring:
    """WeatherBot._restore_exposure_from_db must call base_engine.mark_exposure_restored()."""

    def test_restore_exposure_calls_mark_exposure_restored(self):
        from bots import weather_bot as wb_mod

        src = _get_source(wb_mod.WeatherBot._restore_exposure_from_db)
        assert "self.base_engine.mark_exposure_restored()" in src, \
            "S194: WB _restore_exposure_from_db must call base_engine.mark_exposure_restored()"
        # Must fire on both success and exception paths (per S194 wiring choice)
        assert src.count("self.base_engine.mark_exposure_restored()") >= 2, \
            "S194: must fire on both success path and exception path (watchdog fallback safety)"


class TestEsportsBotExposureRestoreWiring:
    """EsportsBot._restore_exposure_from_db must call base_engine.mark_exposure_restored()."""

    def test_restore_exposure_calls_mark_exposure_restored(self):
        from bots import esports_bot as eb_mod

        src = _get_source(eb_mod.EsportsBot._restore_exposure_from_db)
        assert "self.base_engine.mark_exposure_restored()" in src, \
            "S194: EB _restore_exposure_from_db must call base_engine.mark_exposure_restored()"
        # Must fire on both no-DB-fallback path and main path
        assert src.count("self.base_engine.mark_exposure_restored()") >= 2, \
            "S194: must fire on both no-DB-fallback and main success paths"


class TestMirrorBotStateRestoreWiring:
    """MirrorBot._restore_state_on_startup must call base_engine.mark_exposure_restored()."""

    def test_restore_state_calls_mark_exposure_restored(self):
        from bots import mirror_bot as mb_mod

        src = _get_source(mb_mod.MirrorBot._restore_state_on_startup)
        assert "self.base_engine.mark_exposure_restored()" in src, \
            "S194: MB _restore_state_on_startup must call base_engine.mark_exposure_restored()"


class TestWatchdogStillExistsAsFallback:
    """The 120s degraded-mode watchdog must remain as a safety fallback.

    S194 wires the happy paths but does NOT remove the watchdog — if any wiring
    breaks in the future or a new bot is added without wiring, the watchdog
    still ensures the bot eventually leaves startup-hold (vs hanging forever).
    """

    def test_watchdog_method_intact(self):
        from base_engine import base_engine as be_mod

        # Method must still exist
        assert hasattr(be_mod.BaseEngine, "_periodic_startup_hold_watchdog"), \
            "S194: watchdog removed inadvertently — must remain as fallback"
        src = _get_source(be_mod.BaseEngine._periodic_startup_hold_watchdog)
        assert "self.mark_ready_to_trade()" in src, \
            "Watchdog must still force-ready after timeout as last-resort fallback"
        assert "entering degraded mode" in src, \
            "Watchdog must still log degraded-mode entry for observability"

    def test_setters_still_invoke_check_all_ready(self):
        """All 3 setters chain to _check_all_ready() so any combination wiring works."""
        from base_engine import base_engine as be_mod

        for setter_name in ("mark_positions_seeded", "mark_exposure_restored", "mark_reconciliation_passed"):
            src = _get_source(getattr(be_mod.BaseEngine, setter_name))
            assert "self._check_all_ready()" in src, \
                f"{setter_name} must invoke _check_all_ready() to detect when all 3 are set"
