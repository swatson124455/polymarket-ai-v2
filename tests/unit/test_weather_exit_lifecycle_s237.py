"""S237 exit-lifecycle fixes — guardrail tests.

MEDIUM-2: _restore_exits_from_redis must not future-date the restored cooldown when the
          dynamic cooldown re-derived at restore is smaller than the remaining time.
MEDIUM-3: _close_stale_positions must decrement in-memory/daily-counter exposure AND clear
          _position_details (the primary same-side dedup source) for closed positions.

(BLOCKER-1 — exits must use side="SELL" — is guarded in test_weather_bot.py: the four exit
tests now assert side == "SELL".)
"""

import asyncio
import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.weather_bot import WeatherBot


# ── MEDIUM-2: exit-cooldown restore must never future-date the timestamp ──────

class TestExitCooldownRestore:
    @pytest.mark.asyncio
    async def test_restore_does_not_future_date_when_cooldown_lt_remaining(self):
        """Dynamic cooldown re-derived at restore (1h) < remaining time (5h) must clamp
        to 'exited now', not produce a future-dated _recently_exited (the over-block bug)."""
        bot = MagicMock()
        bot._exit_reasons = {}
        bot._recently_exited = {}
        bot._get_exit_cooldown = MagicMock(return_value=3600.0)  # 1h dynamic cooldown now

        now_wall = time.time()
        cache = MagicMock()
        cache.redis = MagicMock()
        cache.redis.keys = AsyncMock(return_value=["weatherbot:exit:mkt_m2"])
        cache.get = AsyncMock(return_value={"expire_at": now_wall + 18000.0, "reason": "RESOLUTION"})
        bot.base_engine = MagicMock()
        bot.base_engine.cache = cache

        await WeatherBot._restore_exits_from_redis(bot)

        assert "mkt_m2" in bot._recently_exited
        # The bug set this to now_mono + 14400 (future). Fixed: clamped to ~now_mono.
        assert bot._recently_exited["mkt_m2"] <= time.monotonic() + 0.5, (
            "restored cooldown must not be future-dated (would over-block re-entry)"
        )

    @pytest.mark.asyncio
    async def test_restore_preserves_remaining_when_cooldown_ge_remaining(self):
        """When the dynamic cooldown (6h) >= remaining (1h), the restore reflects that the
        exit happened ~5h ago (elapsed ≈ cooldown - remaining), so re-entry unblocks soon."""
        bot = MagicMock()
        bot._exit_reasons = {}
        bot._recently_exited = {}
        bot._get_exit_cooldown = MagicMock(return_value=21600.0)  # 6h

        now_wall = time.time()
        cache = MagicMock()
        cache.redis = MagicMock()
        cache.redis.keys = AsyncMock(return_value=["weatherbot:exit:mkt_m2b"])
        cache.get = AsyncMock(return_value={"expire_at": now_wall + 3600.0, "reason": "RESOLUTION"})
        bot.base_engine = MagicMock()
        bot.base_engine.cache = cache

        mono_before = time.monotonic()
        await WeatherBot._restore_exits_from_redis(bot)

        # elapsed ≈ 6h - 1h = 5h ago → timestamp ≈ now - 18000, comfortably in the past.
        exited_at = bot._recently_exited["mkt_m2b"]
        assert exited_at <= mono_before, "exit should be dated in the past"
        assert (mono_before - exited_at) == pytest.approx(18000.0, abs=5.0)


# ── MEDIUM-3: stale-close must clean exposure + dedup state ────────────────────

class TestStaleCloseCleanup:
    @pytest.mark.asyncio
    async def test_stale_close_decrements_exposure_and_clears_dedup(self):
        mid = "mkt_m3"
        group_key = "Springfield:2020-01-01"

        # DB session mock: 4 execute() calls in order —
        #   1) SELECT open positions  -> [(mid, question)]
        #   2) UPDATE date-stale       -> (ignored)
        #   3) UPDATE age-fallback RETURNING -> [] (no extra rows)
        #   4) UPDATE pnl              -> (ignored)
        sel = MagicMock()
        sel.fetchall = MagicMock(return_value=[(mid, "Highest temperature in Springfield?")])
        agefb = MagicMock()
        agefb.fetchall = MagicMock(return_value=[])
        noop = MagicMock()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(side_effect=[sel, noop, agefb, noop])
        session.commit = AsyncMock()
        db = MagicMock()
        db.get_session = MagicMock(return_value=session)

        gw = MagicMock()
        gw._open_position_markets = {"WeatherBot": {mid}}
        gw._position_details = {f"WeatherBot:{mid}": {"side": "YES", "size": 100.0, "price": 0.5}}

        bot = MagicMock()
        bot.base_engine = MagicMock()
        bot.base_engine.db = db
        bot.base_engine.order_gateway = gw
        bot._exposure_lock = asyncio.Lock()
        bot._group_exposure = {group_key: 50.0}
        bot._city_exposure = {"Springfield": 50.0}
        bot._market_group_cache = {mid: (group_key, "Springfield", 50.0)}

        with patch("bots.weather_bot.WeatherMarketMapper._extract_city_and_date",
                   return_value=("Springfield", date(2020, 1, 1))), \
             patch("bots.weather_bot._inc_daily", new=AsyncMock()) as inc_daily:
            await WeatherBot._close_stale_positions(bot)

        # Primary dedup source cleared → re-entry unblocks (MEDIUM-3 core).
        assert f"WeatherBot:{mid}" not in gw._position_details
        assert mid not in gw._open_position_markets["WeatherBot"]
        # Exposure decremented (was leaked: popped cache without decrementing).
        assert bot._group_exposure[group_key] == pytest.approx(0.0)
        assert bot._city_exposure["Springfield"] == pytest.approx(0.0)
        assert mid not in bot._market_group_cache
        # Daily-counter write-through fired with negative deltas.
        assert inc_daily.await_count == 2
        _signs = [c.args[3] for c in inc_daily.await_args_list]
        assert all(s < 0 for s in _signs), "stale-close must decrement (negative) the counters"
