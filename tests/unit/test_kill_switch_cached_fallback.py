"""Option A — scan-loop kill-switch-check cached fallback (master 2026-06-04; ported to
wb/main 2026-06-08).

Tests the new non-blocking, in-memory accessors that let the scan loop reuse a
recent cached kill-switch state on a DB-slow timeout instead of skipping the scan:
  - KillSwitch.cached_engaged() / cache_age_seconds()
  - PortfolioKillSwitch.cached_engaged()
  - MultiLayerKillSwitch.cached_should_trade()

The execution path (order_gateway / execution_engine) is intentionally NOT exercised
here: Option A does not change it — it keeps the authoritative live is_engaged() check.

wb/main port note: every test is parametrized over BOTH the top-level engine tree
(`base_engine.coordination.*` — the engine the live WB process runs now) and the vendored
silo (`bots.weather.engine.base_engine.coordination.*` — live after the weather_main.py
cutover). Both must behave identically.
"""
import importlib
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

_TREES = [
    ("base_engine.coordination.kill_switch",
     "base_engine.coordination.multi_kill_switch"),
    ("bots.weather.engine.base_engine.coordination.kill_switch",
     "bots.weather.engine.base_engine.coordination.multi_kill_switch"),
]


@pytest.fixture(params=_TREES, ids=["top_level", "silo"])
def trees(request):
    ks_mod = importlib.import_module(request.param[0])
    mks_mod = importlib.import_module(request.param[1])
    return ks_mod, mks_mod


def _ks(ks_mod):
    db = MagicMock()
    db.session_factory = None
    return ks_mod.KillSwitch(db=db)


def _mlks(ks_mod, mks_mod):
    db = MagicMock()
    db.session_factory = None
    base = ks_mod.KillSwitch(db=db)
    return mks_mod.MultiLayerKillSwitch(base_kill_switch=base, db=db), base


class TestKillSwitchCachedEngaged:
    def test_none_when_never_checked(self, trees):
        ks_mod, _ = trees
        assert _ks(ks_mod).cached_engaged() is None

    def test_returns_cached_false(self, trees):
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._cache_engaged = False
        ks._cache_until = time.monotonic() + 30
        assert ks.cached_engaged() is False

    def test_returns_cached_true(self, trees):
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._cache_engaged = True
        ks._cache_until = time.monotonic() + 30
        assert ks.cached_engaged() is True

    def test_ignores_ttl_returns_stale_value(self, trees):
        # Cache "expired" (until in the past) but the value is still returned.
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._cache_engaged = False
        ks._cache_until = time.monotonic() - 999
        assert ks.cached_engaged() is False

    def test_killed_flag_overrides_to_engaged(self, trees):
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._killed = True
        ks._cache_engaged = None
        assert ks.cached_engaged() is True

    def test_no_db_io_required(self, trees):
        # session_factory is None; the method must never touch the DB.
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._cache_engaged = False
        assert ks.cached_engaged() is False


class TestKillSwitchCacheAge:
    def test_none_when_never_checked(self, trees):
        ks_mod, _ = trees
        assert _ks(ks_mod).cache_age_seconds() is None

    def test_age_small_when_fresh(self, trees):
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._cache_engaged = False
        ks._cache_until = time.monotonic() + ks_mod.KILL_CACHE_TTL_SECONDS  # just cached now
        age = ks.cache_age_seconds()
        assert age is not None and 0.0 <= age < 1.0

    def test_age_grows_when_stale(self, trees):
        ks_mod, _ = trees
        ks = _ks(ks_mod)
        ks._cache_engaged = True
        # As if cached 100s ago: cache_until = (now - 100) + TTL
        ks._cache_until = time.monotonic() - 100 + ks_mod.KILL_CACHE_TTL_SECONDS
        age = ks.cache_age_seconds()
        assert age is not None and 99.0 < age < 101.0


class TestMultiLayerCachedShouldTrade:
    def test_none_when_no_cache(self, trees):
        mlks, base = _mlks(*trees)
        assert base.cached_engaged() is None
        assert mlks.cached_should_trade("MirrorBot") is None

    def test_true_when_cached_not_engaged(self, trees):
        mlks, base = _mlks(*trees)
        base._cache_engaged = False
        assert mlks.cached_should_trade("MirrorBot") is True

    def test_false_when_cached_engaged(self, trees):
        mlks, base = _mlks(*trees)
        base._cache_engaged = True
        assert mlks.cached_should_trade("MirrorBot") is False

    def test_false_when_bot_killed_regardless_of_cache(self, trees):
        mlks, base = _mlks(*trees)
        base._cache_engaged = False  # portfolio would allow
        mlks.bot._killed_bots["MirrorBot"] = datetime.now(timezone.utc)
        assert mlks.cached_should_trade("MirrorBot") is False
        # A different, non-killed bot still uses the cache.
        assert mlks.cached_should_trade("WeatherBot") is True

    def test_false_when_system_killed(self, trees):
        mlks, base = _mlks(*trees)
        base._cache_engaged = False
        mlks.system._system_killed = True
        assert mlks.cached_should_trade("MirrorBot") is False

    def test_portfolio_cached_engaged_passthrough(self, trees):
        mlks, base = _mlks(*trees)
        base._cache_engaged = True
        assert mlks.portfolio.cached_engaged() is True
        base._cache_engaged = None
        assert mlks.portfolio.cached_engaged() is None

    def test_pure_sync_no_await_needed(self, trees):
        # cached_should_trade is a plain (non-async) method — callable from a
        # timeout handler without awaiting.
        mlks, base = _mlks(*trees)
        base._cache_engaged = False
        assert mlks.cached_should_trade("MirrorBot") is True

    def test_false_when_base_hard_killed(self, trees):
        # Base KillSwitch._killed=True (hard in-process kill) forces cached_engaged()
        # to True even with no cache value, so cached_should_trade must block.
        mlks, base = _mlks(*trees)
        base._killed = True
        assert base.cached_engaged() is True
        assert mlks.cached_should_trade("MirrorBot") is False
