"""S226 (fallacy-audit V34 follow-ups) — synthetic-ensemble marker + deterministic RNG.

When the real ensemble fetch fails but the deterministic forecast succeeds,
get_combined_forecast fabricates a 31-member Gaussian pseudo-ensemble around
the deterministic high (spread lead-time-scaled since S224 / 410a89b).

Two defects covered here:

1. MARKER: downstream consumers could not tell a fabricated ensemble from a
   real one — ensemble_count=31 looks exactly like a full GEFS fetch.
   CombinedForecast.synthetic_ensemble must be True on the synthetic path and
   False everywhere else (field defaults False so every other construction
   site is unaffected), and "synthetic" must be appended to models_used so the
   provenance persists via weather_forecasts.models_used / calibration
   model_name. NOTE: marker only — no trade gating on it in this change.

2. DETERMINISM: the member RNG was seeded with hash((station_id, target_iso)).
   Python salts str hashes per process (PYTHONHASHSEED), so identical inputs
   produced a DIFFERENT fabricated ensemble after every restart. The seed must
   come from a stable digest of the actual inputs (station, date, det high).
"""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bots.weather.engine.base_engine.weather.forecast_client import (
    CombinedForecast,
    WeatherForecastClient,
)
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY

# Past date (same convention as TestWeatherForecastClient) → lead_time clamps
# to 0 → sigma bucket is constant, so member values depend only on the seed.
DUMMY_DATE = date(2026, 3, 10)
DUMMY_ISO = DUMMY_DATE.isoformat()

DET_DAILY = {"daily": {"time": [DUMMY_ISO], "temperature_2m_max": [65.0]}}
ENS_DAILY = {
    "daily": {
        "time": [DUMMY_ISO],
        "temperature_2m_max_member01": [65.0] * 7,
        "temperature_2m_max_member02": [66.0] * 7,
    }
}


async def _combined_forecast(det=DET_DAILY, ens=None, nbm=None):
    """Fresh client (fresh RNG/caches — simulates a restart as far as Python
    object state goes) with the network fetches mocked. ens=None reproduces
    the ensemble-fetch-failed degraded path that triggers the synthetic
    fallback; passing ENS_DAILY exercises the real-ensemble path."""
    client = WeatherForecastClient()
    station = STATION_REGISTRY["new_york_city"]  # temp_unit="F"
    with patch.object(client, "get_deterministic_forecast", new_callable=AsyncMock,
                      return_value=det), \
         patch.object(client, "get_ensemble_forecast", new_callable=AsyncMock,
                      return_value=ens), \
         patch.object(client, "get_nbm_forecast", new_callable=AsyncMock,
                      return_value=nbm):
        return await client.get_combined_forecast(station, DUMMY_DATE)


class TestSyntheticEnsembleMarker:
    """V34 follow-up (a): the fabricated ensemble must be marked."""

    @pytest.mark.asyncio
    async def test_synthetic_path_sets_marker_true(self):
        fc = await _combined_forecast(ens=None)
        assert fc is not None
        assert len(fc.ensemble_members) == 31  # fabricated members present
        assert fc.synthetic_ensemble is True
        # provenance tag persists through weather_forecasts.models_used
        assert "synthetic" in fc.models_used

    @pytest.mark.asyncio
    async def test_real_ensemble_path_leaves_marker_false(self):
        fc = await _combined_forecast(ens=ENS_DAILY)
        assert fc is not None
        assert fc.synthetic_ensemble is False
        assert "synthetic" not in fc.models_used
        # real path unchanged by this fix
        assert "gfs025_ensemble" in fc.models_used

    def test_marker_defaults_false_at_plain_construction(self):
        # Guards every other construction site (warm-cache restore, tests,
        # the diverged dead top-level tree): omitting the field must yield
        # False, never "unknown"/truthy.
        fc = CombinedForecast(
            ensemble_members=[70.0, 71.0],
            deterministic_high=70.5,
            model_spread=1.0,
            lead_time_hours=24.0,
        )
        assert fc.synthetic_ensemble is False

    @pytest.mark.asyncio
    async def test_warm_cache_restore_recovers_marker_from_models_used(self):
        # warm_cache_from_db rebuilds CombinedForecast from DB rows; the
        # marker itself has no DB column, but the "synthetic" tag in
        # models_used round-trips (weather_forecasts.models_used JSONB) —
        # the restore path must translate the tag back into the marker.
        from datetime import datetime, timedelta, timezone

        d_synth = date.today() + timedelta(days=1)
        d_real = date.today() + timedelta(days=2)
        now_utc = datetime.now(timezone.utc)
        rows = [
            ("KLGA", datetime(d_synth.year, d_synth.month, d_synth.day),
             now_utc, [65.0] * 31, 65.0, 1.5, ["nbm", "synthetic"]),
            ("KLGA", datetime(d_real.year, d_real.month, d_real.day),
             now_utc, [65.0, 66.0], 65.5, 1.5, ["nbm", "gfs025_ensemble"]),
        ]

        class _Result:
            def fetchall(self):
                return rows

        class _Session:
            async def execute(self, *args, **kwargs):
                return _Result()

        class _Ctx:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *args):
                return False

        class _DB:
            def get_session(self):
                return _Ctx()

        client = WeatherForecastClient()
        await client.warm_cache_from_db(_DB())

        synth_key = f"KLGA:{d_synth.isoformat()}"
        real_key = f"KLGA:{d_real.isoformat()}"
        assert synth_key in client._cache and real_key in client._cache
        assert client._cache[synth_key][1].synthetic_ensemble is True
        assert client._cache[real_key][1].synthetic_ensemble is False


class TestSyntheticEnsembleDeterminism:
    """V34 follow-up (b): same inputs → same fabricated members, across
    fresh RNG state and across processes with different hash salts."""

    @pytest.mark.asyncio
    async def test_same_inputs_identical_members_fresh_clients(self):
        fc1 = await _combined_forecast(ens=None)
        fc2 = await _combined_forecast(ens=None)
        assert fc1 is not None and fc2 is not None
        assert fc1.ensemble_members == fc2.ensemble_members

    @pytest.mark.asyncio
    async def test_members_change_when_deterministic_high_changes(self):
        # A new model run (different point high) must redraw, not reuse noise.
        det_warmer = {"daily": {"time": [DUMMY_ISO], "temperature_2m_max": [70.0]}}
        fc1 = await _combined_forecast(det=DET_DAILY, ens=None)
        fc2 = await _combined_forecast(det=det_warmer, ens=None)
        assert fc1.ensemble_members != fc2.ensemble_members

    def test_members_stable_across_python_hash_seeds(self):
        # THE defect test: the old seed hash((station_id, target_iso)) is
        # salted per process, so two interpreters with different
        # PYTHONHASHSEED fabricated different ensembles for identical inputs
        # (restart-varying probabilities on the degraded path). A stable
        # digest seed must produce byte-identical members regardless of salt.
        repo_root = Path(__file__).resolve().parents[2]
        script = (
            "import asyncio\n"
            "from datetime import date\n"
            "from unittest.mock import AsyncMock, patch\n"
            "from bots.weather.engine.base_engine.weather.forecast_client import WeatherForecastClient\n"
            "from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY\n"
            "async def main():\n"
            "    client = WeatherForecastClient()\n"
            "    station = STATION_REGISTRY['new_york_city']\n"
            "    d = date(2026, 3, 10)\n"
            "    det = {'daily': {'time': [d.isoformat()], 'temperature_2m_max': [65.0]}}\n"
            "    with patch.object(client, 'get_deterministic_forecast', new_callable=AsyncMock, return_value=det), \\\n"
            "         patch.object(client, 'get_ensemble_forecast', new_callable=AsyncMock, return_value=None), \\\n"
            "         patch.object(client, 'get_nbm_forecast', new_callable=AsyncMock, return_value=None):\n"
            "        fc = await client.get_combined_forecast(station, d)\n"
            "    print(','.join(f'{m:.9f}' for m in fc.ensemble_members))\n"
            "asyncio.run(main())\n"
        )
        outputs = []
        for salt in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=salt)
            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(repo_root), env=env,
                capture_output=True, text=True, timeout=120,
            )
            assert proc.returncode == 0, proc.stderr
            outputs.append(proc.stdout.strip())
        assert outputs[0], "subprocess produced no members"
        assert outputs[0] == outputs[1], (
            "fabricated ensemble varies with PYTHONHASHSEED — RNG seed is "
            "process-salted (restart-varying), not a stable digest"
        )
