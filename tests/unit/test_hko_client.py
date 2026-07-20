"""S233 tests for HKOClient — the HK Observatory open-data client that supplies
Hong Kong's true resolution-source temperature (HK resolves on the HKO urban HQ,
not the VHHH airport METAR). Covers the pure HQ-reading parse, the resolution-day
running-max ACCUMULATION (rhrread carries only the current hour, so the day's max
is folded across polls via a cache), and the CLMMAXT reconciliation history. Raw
shapes match samples captured live from the HKO API this session."""
from datetime import date
from unittest.mock import AsyncMock

import pytest

from bots.weather.engine.base_engine.weather.hko_client import HKOClient


# ── fakes (no network / no Redis) ─────────────────────────────────────────

class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status
        self.closed = False

    def get(self, url, params=None):
        return _FakeResp(self._status, self._payload)

    async def close(self):
        self.closed = True


class _FakeCache:
    """Duck-typed RedisCache: async get/set backed by a dict, with a `.redis`
    handle (truthy when up) and the shared `raise_on_error` kwarg."""
    def __init__(self, redis_up=True, error=False):
        self.store = {}
        self.redis = object() if redis_up else None   # falsy => Redis down
        self._error = error

    async def get(self, key, raise_on_error=False):
        if self._error and raise_on_error:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ttl=None, raise_on_error=False):
        if self._error and raise_on_error:
            raise RuntimeError("redis down")
        self.store[key] = value


# rhrread sample shape captured live 2026-07-20 (HQ among 26 places).
_RHRREAD = {
    "temperature": {
        "recordTime": "2026-07-21T05:00:00+08:00",
        "data": [
            {"place": "King's Park", "value": 28, "unit": "C"},
            {"place": "Hong Kong Observatory", "value": 29, "unit": "C"},
            {"place": "Wong Chuk Hang", "value": 27, "unit": "C"},
        ],
    }
}


# ── pure parse ────────────────────────────────────────────────────────────

class TestParseHqTemp:
    def test_extracts_hq_not_other_places(self):
        got = HKOClient.parse_hq_temp(_RHRREAD)
        assert got == (29.0, "2026-07-21T05:00:00+08:00")   # HQ, not King's Park (28)

    def test_missing_hq_returns_none(self):
        payload = {"temperature": {"recordTime": "t", "data": [{"place": "King's Park", "value": 28}]}}
        assert HKOClient.parse_hq_temp(payload) is None

    def test_null_hq_value_returns_none(self):
        payload = {"temperature": {"data": [{"place": "Hong Kong Observatory", "value": None}]}}
        assert HKOClient.parse_hq_temp(payload) is None

    def test_empty_payload_returns_none(self):
        assert HKOClient.parse_hq_temp({}) is None
        assert HKOClient.parse_hq_temp(None) is None


# ── current HQ temp (network, faked) ──────────────────────────────────────

class TestGetCurrentHqTemp:
    async def test_ok(self):
        c = HKOClient()
        c._session = _FakeSession(_RHRREAD)
        assert await c.get_current_hq_temp() == (29.0, "2026-07-21T05:00:00+08:00")

    async def test_http_error_returns_none(self):
        c = HKOClient()
        c._session = _FakeSession(_RHRREAD, status=500)
        assert await c.get_current_hq_temp() is None


# ── resolution-day running max ACCUMULATION ───────────────────────────────

class TestRunningDailyMax:
    _TD = date(2026, 7, 21)

    async def test_accumulates_max_across_polls(self):
        c = HKOClient()
        cache = _FakeCache()
        # 29 -> 27 (lower, max holds) -> 31 (new peak)
        c.get_current_hq_temp = AsyncMock(return_value=(29.0, "2026-07-21T05:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=cache) == 29.0
        c.get_current_hq_temp = AsyncMock(return_value=(27.0, "2026-07-21T06:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=cache) == 29.0   # holds
        c.get_current_hq_temp = AsyncMock(return_value=(31.0, "2026-07-21T07:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=cache) == 31.0   # new peak
        assert cache.store[HKOClient._runmax_key(self._TD)] == 31.0

    async def test_fahrenheit_conversion(self):
        c = HKOClient()
        cache = _FakeCache()
        c.get_current_hq_temp = AsyncMock(return_value=(29.0, "2026-07-21T05:00:00+08:00"))
        # 29C -> 84.2F
        assert await c.get_running_daily_max(self._TD, temp_unit="F", cache=cache) == pytest.approx(84.2)

    async def test_reading_from_other_day_not_folded(self):
        c = HKOClient()
        cache = _FakeCache()
        # recordTime is 2026-07-20 (HK) but target is 2026-07-21 -> not our day
        c.get_current_hq_temp = AsyncMock(return_value=(35.0, "2026-07-20T23:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=cache) is None
        assert HKOClient._runmax_key(self._TD) not in cache.store   # nothing stored

    async def test_no_cache_fails_closed(self):
        # A single instantaneous reading is NOT a daily max — with no cache the
        # override must be skipped (None), not fed a bogus value.
        c = HKOClient()
        c.get_current_hq_temp = AsyncMock(return_value=(29.0, "2026-07-21T05:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=None) is None

    async def test_cache_down_fails_closed(self):
        # RedisCache object present but its .redis handle is down → fail CLOSED.
        c = HKOClient()
        c.get_current_hq_temp = AsyncMock(return_value=(29.0, "2026-07-21T05:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=_FakeCache(redis_up=False)) is None

    async def test_cache_error_fails_closed(self):
        # A raised Redis error mid-accumulation → fail CLOSED (never return a
        # bare reading as if it were the day max).
        c = HKOClient()
        c.get_current_hq_temp = AsyncMock(return_value=(29.0, "2026-07-21T05:00:00+08:00"))
        assert await c.get_running_daily_max(self._TD, cache=_FakeCache(error=True)) is None

    async def test_no_reading_returns_stored(self):
        c = HKOClient()
        cache = _FakeCache()
        cache.store[HKOClient._runmax_key(self._TD)] = 30.5
        c.get_current_hq_temp = AsyncMock(return_value=None)
        assert await c.get_running_daily_max(self._TD, cache=cache) == 30.5


# ── CLMMAXT reconciliation history (network, faked) ───────────────────────

class TestDailyMaxHistory:
    async def test_parses_clmmaxt_rows(self):
        payload = {
            "fields": ["Year", "Month", "Day", "Value", "Completeness"],
            "data": [["2026", "6", "29", "31.6", "C"], ["2026", "6", "30", "32.1", "C"]],
        }
        c = HKOClient()
        c._session = _FakeSession(payload)
        got = await c.get_daily_max_history(2026)
        assert got == [(date(2026, 6, 29), 31.6), (date(2026, 6, 30), 32.1)]

    async def test_malformed_rows_skipped(self):
        payload = {"data": [["2026", "6", "30", "32.1", "C"], ["bad", "row"], []]}
        c = HKOClient()
        c._session = _FakeSession(payload)
        assert await c.get_daily_max_history(2026) == [(date(2026, 6, 30), 32.1)]
