"""S234 — ERA5 cold-start bootstrap must bind target_date as a DATE, not a str.

DEFECT: `weather_calibration.target_date` is a DATE column; asyncpg rejects a
str bind ("invalid input for query argument $2: '2026-05-07' (expected a
datetime.date or datetime.datetime instance, got 'str')").
`ForecastClient.fetch_historical_bias` returns the date as an ISO STRING by
contract (`Tuple[float, float, str, float]`), and `_maybe_bootstrap_cold_station`
passed it straight through — so EVERY bootstrap row raised DataError, was
swallowed by the per-row `except`, and logged `weatherbot_bootstrap_row_failed`.
Live effect: `bootstrap_gfs` rows froze at 314 (all 2026-05-31..06-12, none
since); newly added/renamed stations never got their ERA5 historical seed.

Same class as the S227 `gt_cutoff` fix (92740f3) at a call site it missed.
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeSession:
    """Captures the bind params handed to session.execute()."""

    def __init__(self):
        self.calls = []
        self.commit = AsyncMock()

    async def execute(self, _sql, params=None):
        self.calls.append(params)
        return MagicMock()


class _SessionCM:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


def _bare_bot(session):
    """Bypass WeatherBot.__init__ — the bootstrap path only needs these few
    attributes, and a real init pulls in the whole engine."""
    from bots.weather_bot import WeatherBot

    bot = object.__new__(WeatherBot)
    bot._bootstrapped_stations = set()
    bot._station_n_resolved = {}

    # 3 pairs, target_date as the ISO STRING the producer actually returns
    bot._forecast_client = MagicMock()
    bot._forecast_client.fetch_historical_bias = AsyncMock(
        return_value=[(70.0, 72.0, "2026-05-07", 24.0)] * 12
    )

    db = MagicMock()
    db.get_session = MagicMock(return_value=_SessionCM(session))
    bot.base_engine = MagicMock()
    bot.base_engine.db = db

    bot._weather_cal_has_source_col = AsyncMock(return_value=True)
    return bot


def _station():
    st = MagicMock()
    st.station_id = "FACT"
    st.city_name = "Cape Town"
    st.latitude = -33.96
    st.longitude = 18.60
    st.temp_unit = "C"
    return st


@pytest.mark.asyncio
async def test_bootstrap_binds_target_date_as_date_not_str():
    session = _FakeSession()
    bot = _bare_bot(session)

    await bot._maybe_bootstrap_cold_station(_station())

    assert session.calls, "bootstrap inserted no rows — path did not run"
    for params in session.calls:
        td = params["td"]
        # THE DEFECT: this was the raw ISO string, which asyncpg rejects.
        assert isinstance(td, date), (
            f"target_date bound as {type(td).__name__} ({td!r}) — asyncpg needs a "
            f"datetime.date; every bootstrap row will DataError."
        )
        assert td == date(2026, 5, 7)


@pytest.mark.asyncio
async def test_bootstrap_passes_through_a_real_date_untouched():
    """isinstance guard: if the producer ever returns a date, don't re-parse."""
    session = _FakeSession()
    bot = _bare_bot(session)
    bot._forecast_client.fetch_historical_bias = AsyncMock(
        return_value=[(70.0, 72.0, date(2026, 5, 7), 24.0)] * 12
    )

    await bot._maybe_bootstrap_cold_station(_station())

    assert session.calls
    for params in session.calls:
        assert params["td"] == date(2026, 5, 7)


@pytest.mark.asyncio
async def test_bootstrap_rows_actually_commit():
    """Regression guard: the rows must reach commit, not be swallowed by the
    per-row except that hid this defect for weeks."""
    session = _FakeSession()
    bot = _bare_bot(session)

    await bot._maybe_bootstrap_cold_station(_station())

    assert len(session.calls) == 12
    session.commit.assert_awaited_once()
