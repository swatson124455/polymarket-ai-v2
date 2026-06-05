"""Option A — scan-loop kill-switch-check cached fallback (2026-06-04).

Tests the new non-blocking, in-memory accessors that let the scan loop reuse a
recent cached kill-switch state on a DB-slow timeout instead of skipping the scan:
  - KillSwitch.cached_engaged() / cache_age_seconds()
  - PortfolioKillSwitch.cached_engaged()
  - MultiLayerKillSwitch.cached_should_trade()

The execution path (order_gateway / execution_engine) is intentionally NOT exercised
here: Option A does not change it — it keeps the authoritative live is_engaged() check.
"""
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _ks():
    from base_engine.coordination.kill_switch import KillSwitch
    db = MagicMock()
    db.session_factory = None
    return KillSwitch(db=db)


class TestKillSwitchCachedEngaged:
    def test_none_when_never_checked(self):
        assert _ks().cached_engaged() is None

    def test_returns_cached_false(self):
        ks = _ks()
        ks._cache_engaged = False
        ks._cache_until = time.monotonic() + 30
        assert ks.cached_engaged() is False

    def test_returns_cached_true(self):
        ks = _ks()
        ks._cache_engaged = True
        ks._cache_until = time.monotonic() + 30
        assert ks.cached_engaged() is True

    def test_ignores_ttl_returns_stale_value(self):
        # Cache "expired" (until in the past) but the value is still returned.
        ks = _ks()
        ks._cache_engaged = False
        ks._cache_until = time.monotonic() - 999
        assert ks.cached_engaged() is False

    def test_killed_flag_overrides_to_engaged(self):
        ks = _ks()
        ks._killed = True
        ks._cache_engaged = None
        assert ks.cached_engaged() is True

    def test_no_db_io_required(self):
        # session_factory is None; the method must never touch the DB.
        ks = _ks()
        ks._cache_engaged = False
        assert ks.cached_engaged() is False


class TestKillSwitchCacheAge:
    def test_none_when_never_checked(self):
        assert _ks().cache_age_seconds() is None

    def test_age_small_when_fresh(self):
        from base_engine.coordination.kill_switch import KILL_CACHE_TTL_SECONDS
        ks = _ks()
        ks._cache_engaged = False
        ks._cache_until = time.monotonic() + KILL_CACHE_TTL_SECONDS  # just cached now
        age = ks.cache_age_seconds()
        assert age is not None and 0.0 <= age < 1.0

    def test_age_grows_when_stale(self):
        from base_engine.coordination.kill_switch import KILL_CACHE_TTL_SECONDS
        ks = _ks()
        ks._cache_engaged = True
        # As if cached 100s ago: cache_until = (now - 100) + TTL
        ks._cache_until = time.monotonic() - 100 + KILL_CACHE_TTL_SECONDS
        age = ks.cache_age_seconds()
        assert age is not None and 99.0 < age < 101.0


def _mlks():
    from base_engine.coordination.kill_switch import KillSwitch
    from base_engine.coordination.multi_kill_switch import MultiLayerKillSwitch
    db = MagicMock()
    db.session_factory = None
    base = KillSwitch(db=db)
    return MultiLayerKillSwitch(base_kill_switch=base, db=db), base


class TestMultiLayerCachedShouldTrade:
    def test_none_when_no_cache(self):
        mlks, base = _mlks()
        assert base.cached_engaged() is None
        assert mlks.cached_should_trade("MirrorBot") is None

    def test_true_when_cached_not_engaged(self):
        mlks, base = _mlks()
        base._cache_engaged = False
        assert mlks.cached_should_trade("MirrorBot") is True

    def test_false_when_cached_engaged(self):
        mlks, base = _mlks()
        base._cache_engaged = True
        assert mlks.cached_should_trade("MirrorBot") is False

    def test_false_when_bot_killed_regardless_of_cache(self):
        mlks, base = _mlks()
        base._cache_engaged = False  # portfolio would allow
        mlks.bot._killed_bots["MirrorBot"] = datetime.now(timezone.utc)
        assert mlks.cached_should_trade("MirrorBot") is False
        # A different, non-killed bot still uses the cache.
        assert mlks.cached_should_trade("WeatherBot") is True

    def test_false_when_system_killed(self):
        mlks, base = _mlks()
        base._cache_engaged = False
        mlks.system._system_killed = True
        assert mlks.cached_should_trade("MirrorBot") is False

    def test_portfolio_cached_engaged_passthrough(self):
        mlks, base = _mlks()
        base._cache_engaged = True
        assert mlks.portfolio.cached_engaged() is True
        base._cache_engaged = None
        assert mlks.portfolio.cached_engaged() is None

    def test_pure_sync_no_await_needed(self):
        # cached_should_trade is a plain (non-async) method — callable from a
        # timeout handler without awaiting.
        mlks, base = _mlks()
        base._cache_engaged = False
        assert mlks.cached_should_trade("MirrorBot") is True

    def test_false_when_base_hard_killed(self):
        # Base KillSwitch._killed=True (hard in-process kill) forces cached_engaged()
        # to True even with no cache value, so cached_should_trade must block.
        mlks, base = _mlks()
        base._killed = True
        assert base.cached_engaged() is True
        assert mlks.cached_should_trade("MirrorBot") is False
