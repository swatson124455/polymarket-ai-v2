"""Unit tests for WeatherBot S114 cold-start mitigation and S115 SAMOS calibration.

Covers:
  - _get_min_edge() spread confidence gate (S132: spread gate removed)
  - _should_halt_severe_weather() severe weather halt logic
  - Probability engine EMOS/SAMOS fallback chain
  - _fit_samos() SAMOS anomaly-space OLS fitting
"""

import math
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from config.settings import settings
from base_engine.weather.probability_engine import WeatherProbabilityEngine
from base_engine.weather.station_registry import WeatherStation


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_us_station(station_id: str = "KLGA") -> WeatherStation:
    return WeatherStation(
        city_name="New York City",
        station_id=station_id,
        ghcnd_id="GHCND:USW00014732",
        latitude=40.7769,
        longitude=-73.8740,
        elevation_m=6.0,
        timezone="America/New_York",
        temp_unit="F",
    )


def _make_intl_station(station_id: str = "VIDP", local_model=None) -> WeatherStation:
    return WeatherStation(
        city_name="Delhi",
        station_id=station_id,
        ghcnd_id="GHCND:INM00042182",
        latitude=28.5665,
        longitude=77.1031,
        elevation_m=216.0,
        timezone="Asia/Kolkata",
        temp_unit="C",
        local_model=local_model,
    )


def _make_bot_stub(**overrides):
    """Create a minimal WeatherBot-like object with the methods under test.

    We import WeatherBot and attach only the instance attributes needed,
    avoiding the full __init__ which requires DB, Redis, etc.
    """
    from bots.weather_bot import WeatherBot

    bot = object.__new__(WeatherBot)
    bot._station_n_resolved = overrides.get("_station_n_resolved", {})
    bot._min_edge = overrides.get("_min_edge", 0.08)
    bot._intl_min_edge = overrides.get("_intl_min_edge", 0.12)
    bot._category_params = overrides.get("_category_params", {})
    bot._spread_history = overrides.get("_spread_history", {})
    bot._severe_weather_events = overrides.get("_severe_weather_events", {})
    bot._bootstrapped_stations = overrides.get("_bootstrapped_stations", set())
    return bot


# S134: TestCalibrationConfidence class removed (Buhlmann ramp deleted in S132)

# ═══════════════════════════════════════════════════════════════════════════
# 2. _get_min_edge() — spread confidence gate
# ═══════════════════════════════════════════════════════════════════════════


class TestGetMinEdge:
    """Tests for WeatherBot._get_min_edge() with S114 spread confidence gate."""

    def test_returns_base_min_edge_no_spread(self):
        """Without model_spread, returns base min_edge."""
        bot = _make_bot_stub(_min_edge=0.08)
        station = _make_us_station()
        assert bot._get_min_edge("temperature", station) == 0.08

    def test_intl_station_without_local_model_uses_intl_floor(self):
        """International station (C) without local_model gets higher floor."""
        bot = _make_bot_stub(_min_edge=0.08, _intl_min_edge=0.12)
        station = _make_intl_station(local_model=None)
        result = bot._get_min_edge("temperature", station)
        assert result == 0.12

    def test_intl_station_with_local_model_uses_base(self):
        """International station WITH local_model trades at data parity."""
        bot = _make_bot_stub(_min_edge=0.08, _intl_min_edge=0.12)
        station = _make_intl_station(local_model="meteofrance_seamless")
        result = bot._get_min_edge("temperature", station)
        assert result == 0.08

    def test_spread_gate_removed_no_scaling(self):
        """S132: Spread confidence gate removed — model_spread should NOT affect min_edge."""
        bot = _make_bot_stub(_min_edge=0.08)
        station = _make_us_station()
        bot._spread_history = {"KLGA": deque([2.0, 2.0, 2.0], maxlen=14)}
        # Wide spread should NOT scale up min_edge anymore
        result = bot._get_min_edge("temperature", station, model_spread=10.0)
        assert result == 0.08
        # Tight spread should NOT scale down min_edge anymore
        result2 = bot._get_min_edge("temperature", station, model_spread=0.5)
        assert result2 == 0.08

    def test_category_params_override_base(self):
        """Category-specific min_edge overrides global base."""
        bot = _make_bot_stub(
            _min_edge=0.08,
            _category_params={"precipitation": {"min_edge": 0.10}},
        )
        station = _make_us_station()
        result = bot._get_min_edge("precipitation", station)
        assert result == 0.10

    def test_no_station_uses_base(self):
        """When station is None, returns base min_edge."""
        bot = _make_bot_stub(_min_edge=0.08)
        result = bot._get_min_edge("temperature")
        assert result == 0.08


# ═══════════════════════════════════════════════════════════════════════════
# 3. SAMOS probability engine — fallback chain and global EMOS
# ═══════════════════════════════════════════════════════════════════════════


class TestProbabilityEngineFallbackChain:
    """Tests for WeatherProbabilityEngine._get_emos_params() fallback chain."""

    def test_local_emos_takes_precedence(self):
        """Local station EMOS is preferred over global."""
        eng = WeatherProbabilityEngine()
        eng.load_emos_calibration({
            "KLGA": {0: (0.5, 0.98, 1.2), 6: (0.3, 0.99, 1.1)},
        })
        eng.load_global_emos((0.1, 1.0, 2.0))
        result = eng._get_emos_params("KLGA", 3.0)  # bucket 0
        assert result == (0.5, 0.98, 1.2)

    def test_falls_back_to_global_emos(self):
        """Station without local EMOS falls back to global."""
        eng = WeatherProbabilityEngine()
        eng.load_emos_calibration({"KORD": {0: (0.5, 0.98, 1.2)}})
        eng.load_global_emos((0.1, 1.0, 2.0))
        result = eng._get_emos_params("KLGA", 3.0)
        assert result == (0.1, 1.0, 2.0)

    def test_falls_back_to_global_for_missing_bucket(self):
        """Station has local EMOS but not for this lead time bucket."""
        eng = WeatherProbabilityEngine()
        eng.load_emos_calibration({
            "KLGA": {0: (0.5, 0.98, 1.2)},  # only bucket 0
        })
        eng.load_global_emos((0.1, 1.0, 2.0))
        result = eng._get_emos_params("KLGA", 15.0)  # bucket 12 — not in local
        assert result == (0.1, 1.0, 2.0)

    def test_falls_back_to_bias_offset_without_global(self):
        """No local, no global → simple bias offset (a=bias, b=1, sigma=None)."""
        eng = WeatherProbabilityEngine()
        eng.load_calibration({"KLGA": {0: 0.5}})
        result = eng._get_emos_params("KLGA", 3.0)  # bucket 0
        assert result == (0.5, 1.0, None)

    def test_falls_back_to_identity_without_anything(self):
        """No local, no global, no bias → identity (0, 1, None)."""
        eng = WeatherProbabilityEngine()
        result = eng._get_emos_params("KLGA", 3.0)
        assert result == (0.0, 1.0, None)

    def test_load_global_emos_stores_params(self):
        """load_global_emos() sets the global fallback correctly."""
        eng = WeatherProbabilityEngine()
        assert eng._global_emos is None
        eng.load_global_emos((1.5, 0.95, 2.3))
        assert eng._global_emos == (1.5, 0.95, 2.3)


class TestFitSamos:
    """Tests for WeatherBot._fit_samos() SAMOS anomaly-space OLS."""

    @staticmethod
    def _fit_samos(pairs):
        from bots.weather_bot import WeatherBot
        return WeatherBot._fit_samos(pairs)

    def test_returns_none_for_insufficient_data(self):
        """< 2 valid pairs → None (fall back to raw EMOS)."""
        assert self._fit_samos([]) is None
        assert self._fit_samos([(50, 52, 55, 5)]) is None

    def test_returns_none_for_invalid_clim_std(self):
        """All clim_std <= 0.5 → all filtered out → None."""
        pairs = [
            (50, 52, 55, 0.1),
            (60, 62, 65, 0.3),
            (70, 72, 75, 0.5),
        ]
        assert self._fit_samos(pairs) is None

    def test_perfect_forecast_returns_identity(self):
        """Perfect forecasts (forecast == actual) → a~0, b~1."""
        pairs = [
            (50, 50, 55, 5.0),
            (60, 60, 55, 5.0),
            (70, 70, 55, 5.0),
            (80, 80, 55, 5.0),
        ]
        a, b, sigma = self._fit_samos(pairs)
        assert abs(a) < 1e-6
        assert abs(b - 1.0) < 1e-6

    def test_systematic_bias_detected(self):
        """Forecast consistently 2 degrees too low → a > 0 in anomaly space."""
        clim_mean, clim_std = 60.0, 5.0
        pairs = [
            (50, 52, clim_mean, clim_std),
            (55, 57, clim_mean, clim_std),
            (60, 62, clim_mean, clim_std),
            (65, 67, clim_mean, clim_std),
            (70, 72, clim_mean, clim_std),
        ]
        a, b, sigma = self._fit_samos(pairs)
        # Actual is consistently 2F higher than forecast
        # In anomaly space: y_anom - x_anom = 2/5 = 0.4 per point
        assert a > 0.3  # positive intercept captures warm bias correction

    def test_denormalization_formula(self):
        """Verify the SAMOS → raw space conversion formula.

        a_raw = clim_mean * (1 - b_samos) + clim_std * a_samos
        b_raw = b_samos
        sigma_raw = clim_std * sigma_samos
        """
        # SAMOS params (anomaly space)
        a_s, b_s, sigma_s = 0.1, 0.95, 0.8
        clim_mean, clim_std = 60.0, 5.0

        a_raw = clim_mean * (1.0 - b_s) + clim_std * a_s
        b_raw = b_s
        sigma_raw = clim_std * sigma_s

        # a_raw = 60*(1-0.95) + 5*0.1 = 60*0.05 + 0.5 = 3.0 + 0.5 = 3.5
        assert abs(a_raw - 3.5) < 1e-9
        assert abs(b_raw - 0.95) < 1e-9
        # sigma_raw = 5 * 0.8 = 4.0
        assert abs(sigma_raw - 4.0) < 1e-9

    def test_sigma_floor_at_0_3(self):
        """SAMOS sigma has a floor at 0.3 anomaly units."""
        # Near-perfect correlation with tiny residuals
        clim_mean, clim_std = 60.0, 10.0
        pairs = [
            (50, 50.001, clim_mean, clim_std),
            (60, 60.001, clim_mean, clim_std),
            (70, 70.001, clim_mean, clim_std),
            (80, 80.001, clim_mean, clim_std),
        ]
        a, b, sigma = self._fit_samos(pairs)
        assert sigma >= 0.3


# ═══════════════════════════════════════════════════════════════════════════
# 4. _should_halt_severe_weather()
# ═══════════════════════════════════════════════════════════════════════════


class TestShouldHaltSevereWeather:
    """Tests for WeatherBot._should_halt_severe_weather()."""

    def test_halts_on_hurricane_warning(self):
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Hurricane Warning"]},
        )
        station = _make_us_station()
        result = bot._should_halt_severe_weather(station)
        assert result == "Hurricane Warning"

    def test_halts_on_tornado_warning(self):
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Tornado Warning"]},
        )
        station = _make_us_station()
        result = bot._should_halt_severe_weather(station)
        assert result == "Tornado Warning"

    def test_halts_on_extreme_wind_warning(self):
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Extreme Wind Warning"]},
        )
        station = _make_us_station()
        result = bot._should_halt_severe_weather(station)
        assert result == "Extreme Wind Warning"

    def test_does_not_halt_on_blizzard_warning(self):
        """Blizzard Warning is severe but not in the halt list."""
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Blizzard Warning"]},
        )
        station = _make_us_station()
        result = bot._should_halt_severe_weather(station)
        assert result is None

    def test_does_not_halt_on_winter_storm_warning(self):
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Winter Storm Warning"]},
        )
        station = _make_us_station()
        assert bot._should_halt_severe_weather(station) is None

    def test_does_not_halt_on_heat_advisory(self):
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Heat Advisory"]},
        )
        station = _make_us_station()
        assert bot._should_halt_severe_weather(station) is None

    def test_skips_international_stations(self):
        """International stations (temp_unit=C) return None (no NWS coverage)."""
        bot = _make_bot_stub(
            _severe_weather_events={"VIDP": ["Hurricane Warning"]},
        )
        station = _make_intl_station()
        result = bot._should_halt_severe_weather(station)
        assert result is None

    def test_no_events_for_station(self):
        """Station with no events in the dict returns None."""
        bot = _make_bot_stub(_severe_weather_events={})
        station = _make_us_station()
        assert bot._should_halt_severe_weather(station) is None

    def test_multiple_events_first_halt_wins(self):
        """If multiple events exist and one is halting, returns the halt event."""
        bot = _make_bot_stub(
            _severe_weather_events={
                "KLGA": ["Flood Watch", "Tornado Warning", "Wind Advisory"],
            },
        )
        station = _make_us_station()
        result = bot._should_halt_severe_weather(station)
        assert result == "Tornado Warning"

    def test_halt_event_match_is_exact(self):
        """Partial matches should not trigger halts (e.g. 'Tornado Watch' != 'Tornado Warning')."""
        bot = _make_bot_stub(
            _severe_weather_events={"KLGA": ["Tornado Watch"]},
        )
        station = _make_us_station()
        assert bot._should_halt_severe_weather(station) is None
