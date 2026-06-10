"""B2 (S241) — ModelRunMonitor must not burn the Open-Meteo quota the scan path needs.

Two behaviors under test (silo monitor — the class WeatherBot imports on both the
current hybrid and post-weather_main.py-cutover):

1. Refresh horizon is WEATHER_MODEL_RUN_REFRESH_DAYS (default 3, was hardcoded 8):
   the full-horizon refresh produced ~544 get_combined_forecast fetches per
   model-run event, 429'ing Open-Meteo and starving on-demand scan fetches
   (weatherbot_analyze_skip reason=no_forecast every cycle, 2026-06-10).
2. 429-aware abort: when any forecast-client model is in an active 429 cooldown
   (_model_429_until), the refresh round aborts at the next batch boundary instead
   of marching through the remaining pairs.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bots.weather.engine.base_engine.weather.model_run_monitor import ModelRunMonitor
from bots.weather.engine.config.settings import settings


def _station(sid: str, unit: str = "C"):
    return SimpleNamespace(station_id=sid, temp_unit=unit, latitude=0.0, longitude=0.0,
                           local_model=None, city_name=sid)


def _monitor(n_stations: int = 4, cooled: dict | None = None):
    fc = SimpleNamespace(
        _model_429_until=dict(cooled or {}),
        _model_run_cache={},
    )
    stations = [_station(f"S{i}") for i in range(n_stations)]
    return ModelRunMonitor(fc, stations, asyncio.Queue()), fc


class TestRefreshHorizonKnob:
    @pytest.mark.asyncio
    async def test_default_horizon_3_days(self, monkeypatch):
        monkeypatch.setattr(settings, "WEATHER_MODEL_RUN_REFRESH_DAYS", 3, raising=False)
        mon, _ = _monitor(n_stations=4)
        calls = []

        async def _fake_refresh(station, td):
            calls.append((station.station_id, td))
            return (1, 0)

        with patch.object(mon, "_refresh_single", side_effect=_fake_refresh):
            await mon._refresh_forecasts(us_only=False)

        # 4 stations x 3 days = 12 pairs (was 4 x 8 = 32 with the hardcoded horizon)
        assert len(calls) == 12

    @pytest.mark.asyncio
    async def test_horizon_8_restores_old_behavior(self, monkeypatch):
        monkeypatch.setattr(settings, "WEATHER_MODEL_RUN_REFRESH_DAYS", 8, raising=False)
        mon, _ = _monitor(n_stations=4)
        calls = []

        async def _fake_refresh(station, td):
            calls.append(1)
            return (1, 0)

        with patch.object(mon, "_refresh_single", side_effect=_fake_refresh):
            await mon._refresh_forecasts(us_only=False)

        assert len(calls) == 32

    @pytest.mark.asyncio
    async def test_us_only_keeps_3_day_hrrr_horizon(self, monkeypatch):
        monkeypatch.setattr(settings, "WEATHER_MODEL_RUN_REFRESH_DAYS", 8, raising=False)
        fc = SimpleNamespace(_model_429_until={}, _model_run_cache={})
        stations = [_station("KLGA", unit="F"), _station("EGLC", unit="C")]
        mon = ModelRunMonitor(fc, stations, asyncio.Queue())
        calls = []

        async def _fake_refresh(station, td):
            calls.append(station.station_id)
            return (1, 0)

        with patch.object(mon, "_refresh_single", side_effect=_fake_refresh):
            await mon._refresh_forecasts(us_only=True)

        # us_only: only the F-unit station, HRRR horizon stays 3 days
        assert len(calls) == 3 and set(calls) == {"KLGA"}


class TestAbortOn429Cooldown:
    @pytest.mark.asyncio
    async def test_active_cooldown_aborts_before_first_batch(self, monkeypatch):
        monkeypatch.setattr(settings, "WEATHER_MODEL_RUN_REFRESH_DAYS", 3, raising=False)
        mon, _ = _monitor(n_stations=4, cooled={"gfs025": time.monotonic() + 3600})
        calls = []

        async def _fake_refresh(station, td):
            calls.append(1)
            return (1, 0)

        with patch.object(mon, "_refresh_single", side_effect=_fake_refresh):
            await mon._refresh_forecasts(us_only=False)

        assert calls == []  # aborted before any fetch

    @pytest.mark.asyncio
    async def test_expired_cooldown_does_not_abort(self, monkeypatch):
        monkeypatch.setattr(settings, "WEATHER_MODEL_RUN_REFRESH_DAYS", 3, raising=False)
        mon, _ = _monitor(n_stations=2, cooled={"gfs025": time.monotonic() - 5})
        calls = []

        async def _fake_refresh(station, td):
            calls.append(1)
            return (1, 0)

        with patch.object(mon, "_refresh_single", side_effect=_fake_refresh):
            await mon._refresh_forecasts(us_only=False)

        assert len(calls) == 6  # 2 stations x 3 days — expired cooldown ignored

    @pytest.mark.asyncio
    async def test_cooldown_set_mid_round_stops_next_batch(self, monkeypatch):
        # 30 stations x 3 days = 90 pairs = 5 batches of 20. Cooldown lands during
        # batch 1 -> batches 2-5 must not run (only the first 20 pairs fetched).
        monkeypatch.setattr(settings, "WEATHER_MODEL_RUN_REFRESH_DAYS", 3, raising=False)
        mon, fc = _monitor(n_stations=30)
        calls = []

        async def _fake_refresh(station, td):
            calls.append(1)
            fc._model_429_until["deterministic"] = time.monotonic() + 300
            return (1, 0)

        with patch.object(mon, "_refresh_single", side_effect=_fake_refresh):
            await mon._refresh_forecasts(us_only=False)

        assert len(calls) == 20
