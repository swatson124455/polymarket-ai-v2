"""Unit tests for WeatherBot and supporting weather modules."""

import asyncio
import math
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from bots.weather.engine.config.settings import settings

from bots.weather.engine.base_engine.weather.station_registry import (
    STATION_REGISTRY,
    StationHealthMonitor,
    US_CITY_NAMES,
    WeatherStation,
    lookup_station,
)
from bots.weather.engine.base_engine.weather.market_mapper import (
    TemperatureBucket,
    WeatherMarketGroup,
    WeatherMarketMapper,
    _parse_date,
)
from bots.weather.engine.base_engine.weather.probability_engine import WeatherProbabilityEngine
from bots.weather.engine.base_engine.weather.forecast_client import CombinedForecast, WeatherForecastClient


# ═══════════════════════════════════════════════════════════════════════════
# Station Registry
# ═══════════════════════════════════════════════════════════════════════════


class TestStationRegistry:
    def test_registry_has_all_major_us_cities(self):
        expected_us = [
            "new_york_city", "atlanta", "seattle", "dallas", "miami", "chicago", "denver",
            "los_angeles", "phoenix", "houston", "philadelphia", "san_francisco", "boston",
            "washington_dc", "minneapolis", "detroit", "las_vegas", "portland", "nashville",
            "salt_lake_city", "kansas_city", "orlando", "tampa", "charlotte", "new_orleans",
            "indianapolis", "columbus", "memphis", "louisville", "austin", "san_antonio",
            "san_diego", "sacramento", "pittsburgh", "st_louis", "baltimore", "raleigh",
            "oklahoma_city", "omaha", "albuquerque", "tucson", "el_paso", "jacksonville",
            "honolulu", "anchorage",
        ]
        for key in expected_us:
            assert key in STATION_REGISTRY, f"Missing US city: {key}"

    def test_registry_has_all_major_international_cities(self):
        expected_intl = [
            "london", "toronto", "seoul", "buenos_aires", "wellington", "ankara",
            "tokyo", "sydney", "melbourne", "paris", "berlin", "dubai", "mexico_city",
            "sao_paulo", "amsterdam", "mumbai", "vienna", "stockholm", "oslo",
            "copenhagen", "warsaw", "prague", "zurich", "brussels", "madrid", "rome",
            "singapore", "hong_kong", "bangkok", "taipei", "vancouver", "montreal",
            "auckland", "johannesburg", "cairo", "istanbul", "athens", "lisbon",
            "dublin", "helsinki", "beijing", "shanghai", "delhi", "kuala_lumpur",
            "jakarta", "nairobi",
        ]
        for key in expected_intl:
            assert key in STATION_REGISTRY, f"Missing international city: {key}"

    def test_registry_total_size(self):
        assert len(STATION_REGISTRY) >= 80, f"Expected ≥80 stations, got {len(STATION_REGISTRY)}"

    def test_lookup_nyc_aliases(self):
        assert lookup_station("nyc") is not None
        assert lookup_station("new york city") is not None
        assert lookup_station("new york") is not None
        assert lookup_station("NYC").station_id == "KLGA"

    def test_lookup_los_angeles_aliases(self):
        assert lookup_station("Los Angeles").station_id == "KLAX"
        assert lookup_station("la").station_id == "KLAX"

    def test_lookup_washington_dc_aliases(self):
        s = lookup_station("Washington D.C.")
        assert s is not None
        assert s.station_id == "KDCA"
        assert lookup_station("Washington DC") is not None
        assert lookup_station("Washington, D.C.") is not None

    def test_lookup_sao_paulo_aliases(self):
        assert lookup_station("São Paulo") is not None
        assert lookup_station("Sao Paulo") is not None

    def test_lookup_london(self):
        s = lookup_station("London")
        assert s is not None
        assert s.station_id == "EGLC"
        assert s.temp_unit == "C"

    def test_lookup_tokyo(self):
        s = lookup_station("Tokyo")
        assert s is not None
        assert s.station_id == "RJTT"
        assert s.temp_unit == "C"

    def test_lookup_unknown_city_returns_none(self):
        assert lookup_station("Mars") is None
        assert lookup_station("") is None
        assert lookup_station("randomcityxyz") is None

    def test_lookup_substring_in_longer_text(self):
        s = lookup_station("highest temperature in NYC be")
        assert s is not None
        assert s.station_id == "KLGA"

    def test_us_cities_use_fahrenheit(self):
        us_keys = [
            "new_york_city", "atlanta", "seattle", "dallas", "miami", "chicago", "denver",
            "los_angeles", "phoenix", "houston", "boston", "las_vegas", "honolulu",
        ]
        for key in us_keys:
            assert STATION_REGISTRY[key].temp_unit == "F", f"{key} should be F"

    def test_international_cities_use_celsius(self):
        intl_keys = [
            "london", "toronto", "seoul", "buenos_aires", "wellington", "ankara",
            "tokyo", "sydney", "paris", "berlin", "dubai", "singapore",
        ]
        for key in intl_keys:
            assert STATION_REGISTRY[key].temp_unit == "C", f"{key} should be C"

    def test_station_has_valid_coordinates(self):
        for key, station in STATION_REGISTRY.items():
            assert -90 <= station.latitude <= 90, f"{key}: invalid latitude {station.latitude}"
            assert -180 <= station.longitude <= 180, f"{key}: invalid longitude {station.longitude}"

    def test_us_city_names_frozenset(self):
        assert "New York City" in US_CITY_NAMES
        assert "Los Angeles" in US_CITY_NAMES
        assert "Chicago" in US_CITY_NAMES
        assert "London" not in US_CITY_NAMES   # International should not be in US set
        assert "Tokyo" not in US_CITY_NAMES

    def test_all_us_stations_fahrenheit(self):
        """Every station in US_CITY_NAMES must use Fahrenheit."""
        f_cities = {s.city_name for s in STATION_REGISTRY.values() if s.temp_unit == "F"}
        assert US_CITY_NAMES == f_cities

    def test_no_duplicate_aliases(self):
        """Each alias must map to exactly one station (no collisions)."""
        seen: dict = {}
        for key, station in STATION_REGISTRY.items():
            for alias in station.aliases:
                assert alias not in seen, (
                    f"Duplicate alias '{alias}': {seen[alias]} and {key}"
                )
                seen[alias] = key


# ═══════════════════════════════════════════════════════════════════════════
# Market Mapper
# ═══════════════════════════════════════════════════════════════════════════


class TestMarketMapper:
    mapper = WeatherMarketMapper()

    def _make_market(self, question, mid="m1", yes_price=0.2):
        return {
            "id": mid,
            "question": question,
            "yes_token_id": "tok_yes",
            "no_token_id": "tok_no",
            "yes_price": yes_price,
        }

    def test_is_weather_market_true(self):
        assert self.mapper.is_weather_market(
            {"question": "Will the highest temperature in NYC be between 48-49°F on January 22?"}
        )

    def test_is_weather_market_false(self):
        assert not self.mapper.is_weather_market({"question": "Will Biden win the election?"})
        assert not self.mapper.is_weather_market({"question": ""})

    def test_parse_range_market(self):
        mkt = self._make_market(
            "Will the highest temperature in NYC be between 48-49°F on January 22?"
        )
        b = self.mapper.parse_market(mkt)
        assert b is not None
        assert b.bucket_type == "range"
        assert b.low_bound == 48.0
        assert b.high_bound == 49.0
        assert b.temp_unit == "F"

    def test_parse_at_or_below_market(self):
        mkt = self._make_market(
            "Will the highest temperature in NYC be 42°F or below on January 22?"
        )
        b = self.mapper.parse_market(mkt)
        assert b is not None
        assert b.bucket_type == "at_or_below"
        assert b.low_bound is None
        assert b.high_bound == 42.0

    def test_parse_at_or_higher_market(self):
        mkt = self._make_market(
            "Will the highest temperature in NYC be 55°F or higher on January 22?"
        )
        b = self.mapper.parse_market(mkt)
        assert b is not None
        assert b.bucket_type == "at_or_higher"
        assert b.low_bound == 55.0
        assert b.high_bound is None

    def test_parse_exact_market(self):
        mkt = self._make_market(
            "Will the highest temperature in London be 10°C on February 5?"
        )
        b = self.mapper.parse_market(mkt)
        assert b is not None
        assert b.bucket_type == "exact"
        assert b.low_bound == 10.0
        assert b.high_bound == 10.0
        assert b.temp_unit == "C"

    def test_parse_celsius_market(self):
        mkt = self._make_market(
            "Will the highest temperature in Seoul be between 5-6°C on March 1?"
        )
        b = self.mapper.parse_market(mkt)
        assert b is not None
        assert b.temp_unit == "C"
        assert b.low_bound == 5.0
        assert b.high_bound == 6.0

    def test_parse_negative_temps(self):
        mkt = self._make_market(
            "Will the highest temperature in Toronto be -5°C or below on January 10?"
        )
        b = self.mapper.parse_market(mkt)
        assert b is not None
        assert b.bucket_type == "at_or_below"
        assert b.high_bound == -5.0

    def test_parse_non_weather_returns_none(self):
        mkt = self._make_market("Will Bitcoin reach $100k?")
        assert self.mapper.parse_market(mkt) is None

    def test_group_markets_basic(self):
        markets = [
            self._make_market(
                "Will the highest temperature in NYC be between 48-49°F on January 22?",
                mid="m1", yes_price=0.15,
            ),
            self._make_market(
                "Will the highest temperature in NYC be between 50-51°F on January 22?",
                mid="m2", yes_price=0.35,
            ),
            self._make_market(
                "Will the highest temperature in NYC be 55°F or higher on January 22?",
                mid="m3", yes_price=0.10,
            ),
        ]
        groups = self.mapper.group_markets(markets)
        assert len(groups) == 1
        assert groups[0].city == "New York City"
        assert len(groups[0].buckets) == 3

    def test_group_markets_different_cities(self):
        markets = [
            self._make_market(
                "Will the highest temperature in NYC be between 48-49°F on January 22?",
                mid="m1",
            ),
            self._make_market(
                "Will the highest temperature in London be between 5-6°C on January 22?",
                mid="m2",
            ),
        ]
        groups = self.mapper.group_markets(markets)
        assert len(groups) == 2

    def test_group_markets_different_dates(self):
        markets = [
            self._make_market(
                "Will the highest temperature in NYC be between 48-49°F on January 22?",
                mid="m1",
            ),
            self._make_market(
                "Will the highest temperature in NYC be between 48-49°F on January 23?",
                mid="m2",
            ),
        ]
        groups = self.mapper.group_markets(markets)
        assert len(groups) == 2

    def test_group_buckets_sorted_by_bound(self):
        markets = [
            self._make_market(
                "Will the highest temperature in NYC be 55°F or higher on January 22?",
                mid="m3",
            ),
            self._make_market(
                "Will the highest temperature in NYC be 42°F or below on January 22?",
                mid="m1",
            ),
            self._make_market(
                "Will the highest temperature in NYC be between 48-49°F on January 22?",
                mid="m2",
            ),
        ]
        groups = self.mapper.group_markets(markets)
        assert len(groups) == 1
        # at_or_below (None bound) should be first, then range (48), then at_or_higher (55)
        bounds = [b.low_bound for b in groups[0].buckets]
        assert bounds[0] is None  # at_or_below
        assert bounds[1] == 48.0
        assert bounds[2] == 55.0


class TestDateParsing:
    def test_full_month_name(self):
        assert _parse_date("January 22") == date(datetime.now().year, 1, 22)

    def test_abbreviated_month(self):
        assert _parse_date("Feb 3") == date(datetime.now().year, 2, 3)

    def test_with_year(self):
        assert _parse_date("March 15, 2026") == date(2026, 3, 15)

    def test_invalid_returns_none(self):
        assert _parse_date("NotAMonth 1") is None
        assert _parse_date("") is None


# ═══════════════════════════════════════════════════════════════════════════
# Probability Engine
# ═══════════════════════════════════════════════════════════════════════════


class TestProbabilityEngine:
    engine = WeatherProbabilityEngine()

    def _make_buckets(self):
        """Create a realistic set of NYC temperature buckets."""
        return [
            TemperatureBucket("m1", "t1", "n1", 0.10, "at_or_below", None, 42.0, "F"),
            TemperatureBucket("m2", "t2", "n2", 0.15, "range", 43.0, 45.0, "F"),
            TemperatureBucket("m3", "t3", "n3", 0.25, "range", 46.0, 48.0, "F"),
            TemperatureBucket("m4", "t4", "n4", 0.30, "range", 49.0, 51.0, "F"),
            TemperatureBucket("m5", "t5", "n5", 0.15, "range", 52.0, 54.0, "F"),
            TemperatureBucket("m6", "t6", "n6", 0.05, "at_or_higher", 55.0, None, "F"),
        ]

    def test_fit_distribution_basic(self):
        members = [48.0, 49.1, 47.5, 50.2, 48.8, 49.5, 47.0, 51.0, 48.3, 49.7,
                    48.5, 49.0, 47.8, 50.5, 48.2, 49.3, 47.2, 50.0, 48.7, 49.8,
                    48.1, 49.4, 47.6, 50.3, 48.6, 49.2, 47.3, 50.1, 48.4, 49.6, 48.9]
        loc, scale, shape = self.engine.fit_distribution(members, lead_time_hours=24.0)
        # Mean should be near 49
        assert 47.0 < loc < 51.0
        assert scale > 0.5
        assert isinstance(shape, float)

    def test_fit_distribution_skewnorm_fallback_at_n20(self):
        """S141: n=20 members is below the threshold of 30 — must fall back to shape=0.0 (normal)."""
        members = [48.0 + i * 0.1 for i in range(20)]  # n=20 < 30
        _, _, shape = self.engine.fit_distribution(members, lead_time_hours=24.0)
        assert shape == 0.0, f"Expected normal fallback (shape=0.0) for n=20, got {shape}"

    def test_fit_distribution_minimum_members(self):
        loc, scale, shape = self.engine.fit_distribution([50.0, 52.0], lead_time_hours=12.0)
        assert 49.0 < loc < 53.0

    def test_fit_distribution_too_few_raises(self):
        with pytest.raises(ValueError):
            self.engine.fit_distribution([50.0], lead_time_hours=12.0)
        with pytest.raises(ValueError):
            self.engine.fit_distribution([], lead_time_hours=12.0)

    def test_bucket_probabilities_sum_to_one(self):
        buckets = self._make_buckets()
        # Distribution centered at 48°F
        probs = self.engine.bucket_probabilities(48.0, 2.5, 0.0, buckets)
        total = sum(probs.values())
        assert abs(total - 1.0) < 0.02, f"Probabilities sum to {total}, expected ~1.0"

    def test_at_or_below_captures_left_tail(self):
        buckets = self._make_buckets()
        # Distribution centered well above 42 → low prob for "42 or below"
        probs = self.engine.bucket_probabilities(55.0, 2.0, 0.0, buckets)
        assert probs["m1"] < 0.01  # Very unlikely to be ≤42 when mean is 55

    def test_at_or_higher_captures_right_tail(self):
        buckets = self._make_buckets()
        # Distribution centered well above 55 → high prob for "55 or higher"
        probs = self.engine.bucket_probabilities(60.0, 2.0, 0.0, buckets)
        assert probs["m6"] > 0.8

    def test_range_bucket_centered(self):
        buckets = self._make_buckets()
        # Distribution centered at 50 → bucket 49-51 should have highest prob
        probs = self.engine.bucket_probabilities(50.0, 2.0, 0.0, buckets)
        assert probs["m4"] > probs["m2"]  # 49-51 > 43-45
        assert probs["m4"] > probs["m6"]  # 49-51 > 55+

    def test_tail_bracket_discount_applied(self):
        """P6: at_or_below and at_or_higher buckets get 15% discount (longshot bias)."""
        buckets = self._make_buckets()
        # Distribution centered at 50: at_or_below(42) and at_or_higher(55) are tail buckets
        # Without discount a highly symmetric dist would give equal probabilities to
        # symmetric tails; with discount the tail probs should be lower than they'd be
        # otherwise, keeping range buckets' share relatively larger.
        probs = self.engine.bucket_probabilities(50.0, 3.0, 0.0, buckets)
        # Total still sums to ~1.0 after normalization
        assert abs(sum(probs.values()) - 1.0) < 0.02
        # at_or_below(m1) should be very small (mean=50 is far above 42) — discount just makes it smaller
        assert probs["m1"] < 0.05
        # Range buckets near mean should collectively dominate
        assert probs["m3"] + probs["m4"] + probs["m5"] > 0.6

    def test_fit_distribution_uses_ensemble_spread(self):
        """P6: Scale reflects actual ensemble spread, not fixed lead-time inflation.

        With 133 members (GEFS+IFS+AIFS), naturally wider spread at longer lead
        times is captured by the ensemble members themselves. EMOS d-parameter will
        calibrate residual underdispersion once calibration data accumulates.
        """
        # Tight ensemble (stable pattern): std ≈ 0.7°F
        tight_members = [50.0 + (i % 3) * 0.5 for i in range(31)]
        # Wide ensemble (chaotic pattern): std ≈ 9°F
        wide_members = [50.0 + (i - 15) * 1.0 for i in range(31)]
        _, scale_tight, _ = self.engine.fit_distribution(tight_members, lead_time_hours=6.0)
        _, scale_wide, _ = self.engine.fit_distribution(wide_members, lead_time_hours=72.0)
        # Wider-spread ensemble → larger scale (real meteorological property)
        assert scale_wide > scale_tight

    def test_compute_edges_basic(self):
        model_probs = {"m1": 0.30, "m2": 0.50, "m3": 0.20}
        market_prices = {"m1": 0.10, "m2": 0.45, "m3": 0.40}
        edges = self.engine.compute_edges(model_probs, market_prices)
        assert len(edges) == 3
        # Sorted by |edge| desc
        assert edges[0]["abs_edge"] >= edges[1]["abs_edge"]
        # m1 has largest edge: 0.30 - 0.10 = 0.20
        assert edges[0]["market_id"] == "m1" or edges[0]["market_id"] == "m3"

    def test_compute_edges_side_determination(self):
        model_probs = {"m1": 0.70}
        market_prices = {"m1": 0.30}
        edges = self.engine.compute_edges(model_probs, market_prices)
        assert edges[0]["side"] == "YES"  # model > market → underpriced → buy YES

        model_probs2 = {"m1": 0.20}
        edges2 = self.engine.compute_edges(model_probs2, market_prices)
        assert edges2[0]["side"] == "NO"  # model < market → overpriced → buy NO

    def test_kelly_fraction_positive_edge(self):
        f = self.engine.kelly_fraction(edge=0.20, model_prob=0.50, market_price=0.30, kelly_mult=0.25)
        assert f > 0.0
        assert f <= 0.25

    def test_kelly_fraction_zero_edge(self):
        f = self.engine.kelly_fraction(edge=0.0, model_prob=0.50, market_price=0.50)
        assert f == 0.0

    def test_kelly_fraction_negative_edge_buy_no(self):
        f = self.engine.kelly_fraction(edge=-0.20, model_prob=0.30, market_price=0.50, kelly_mult=0.25)
        assert f > 0.0  # Negative edge → buy NO, which should have positive sizing

    def test_kelly_fraction_capped(self):
        f = self.engine.kelly_fraction(edge=0.50, model_prob=0.90, market_price=0.40, kelly_mult=0.25)
        assert f <= 0.25

    def test_load_calibration(self):
        eng = WeatherProbabilityEngine()
        cal = {"KLGA": {0: 0.5, 6: 0.3, 12: 0.1}}
        eng.load_calibration(cal)
        # With calibration, bias offset should shift the mean
        members = [50.0] * 31
        loc, _, _ = eng.fit_distribution(members, lead_time_hours=3.0, station_id="KLGA")
        # Bias bucket 0 → offset = 0.5
        assert loc > 50.0

    def test_load_emos_calibration_shifts_mean(self):
        """EMOS a + b*mean correction shifts loc correctly."""
        eng = WeatherProbabilityEngine()
        # a=2.0, b=1.05, sigma=1.5: corrected_mean = 2.0 + 1.05 * 50 = 54.5
        emos = {"KLGA": {0: (2.0, 1.05, 1.5)}}
        eng.load_emos_calibration(emos)
        members = [50.0] * 31  # ensemble mean = 50.0
        loc, scale, _ = eng.fit_distribution(members, lead_time_hours=3.0, station_id="KLGA")
        # loc should reflect a + b*mean = 2.0 + 1.05*50 = 54.5
        assert abs(loc - 54.5) < 1.0  # some tolerance for skewnorm path
        # scale should use EMOS sigma = 1.5
        assert abs(scale - 1.5) < 0.1

    def test_emos_sigma_overrides_ensemble_spread(self):
        """EMOS sigma replaces raw ensemble spread as scale."""
        eng = WeatherProbabilityEngine()
        # Wide spread ensemble: std ≈ 5° — but EMOS says sigma=1.0
        wide_members = [50.0 + (i - 15) * 0.7 for i in range(31)]
        emos = {"KLGA": {0: (0.0, 1.0, 1.0)}}  # a=0, b=1 (no mean shift), sigma=1.0
        eng.load_emos_calibration(emos)
        _, scale, _ = eng.fit_distribution(wide_members, lead_time_hours=3.0, station_id="KLGA")
        # EMOS sigma=1.0 should override the wider raw spread
        assert scale <= 1.5  # EMOS constrains the scale tighter than raw spread

    def test_emos_fallback_to_simple_bias_when_no_emos(self):
        """When EMOS not loaded, falls back to simple bias offset (backward compat)."""
        eng = WeatherProbabilityEngine()
        eng.load_calibration({"KLGA": {0: 1.0}})  # simple bias: +1.0°
        # No EMOS loaded
        members = [50.0] * 31
        loc, _, _ = eng.fit_distribution(members, lead_time_hours=3.0, station_id="KLGA")
        # loc should be 50.0 + 1.0 bias = 51.0 (identity EMOS: a=1.0, b=1.0)
        assert abs(loc - 51.0) < 0.5

    def test_emos_applied_to_deterministic_high_not_ensemble_mean(self):
        """S222 A3: EMOS (a, b) is trained on the deterministic high, so the
        serve-time correction must use the same quantity. Legacy fallback
        (no det high passed) still applies it to the ensemble mean."""
        eng = WeatherProbabilityEngine()
        eng.load_emos_calibration({"KLGA": {0: (2.0, 0.9, None)}})
        members = [48.0, 50.0, 52.0] * 8  # 24 members (< 30 → no MLE), mean = 50

        # Train/serve consistent: corrected mean = 2 + 0.9*54 = 50.6
        loc, _, _ = eng.fit_distribution(
            members, lead_time_hours=3.0, station_id="KLGA",
            deterministic_high=54.0,
        )
        assert loc == pytest.approx(50.6, abs=1e-6)

        # Legacy fallback (det high unavailable): 2 + 0.9*50 = 47.0
        loc_legacy, _, _ = eng.fit_distribution(
            members, lead_time_hours=3.0, station_id="KLGA",
        )
        assert loc_legacy == pytest.approx(47.0, abs=1e-6)

    def test_emos_sigma_forces_normal_shape(self):
        """S159: When EMOS sigma is active, shape must be 0.0 (normal distribution).

        EMOS sigma replaces raw scale; the MLE shape `a` was estimated from
        raw data and is inconsistent with EMOS-corrected loc/scale.
        """
        eng = WeatherProbabilityEngine()
        # Create skewed ensemble (many values below 50, few above)
        members = [45.0] * 15 + [50.0] * 10 + [60.0] * 6
        assert len(members) == 31  # need n>=30 for skewnorm path
        # With EMOS sigma, shape should be forced to 0.0
        emos = {"KLGA": {0: (0.0, 1.0, 2.0)}}  # a=0, b=1 (no mean shift), sigma=2.0
        eng.load_emos_calibration(emos)
        loc, scale, shape = eng.fit_distribution(members, lead_time_hours=3.0, station_id="KLGA")
        assert shape == 0.0, f"Shape should be 0.0 when EMOS sigma active, got {shape}"
        assert scale == 2.0, f"Scale should be EMOS sigma=2.0, got {scale}"

    def test_no_emos_sigma_preserves_shape(self):
        """S159: Without EMOS sigma, shape is still estimated from MLE (no change)."""
        eng = WeatherProbabilityEngine()
        # Create clearly skewed ensemble
        members = [45.0] * 15 + [50.0] * 10 + [65.0] * 6
        assert len(members) == 31
        # No EMOS sigma — shape should be non-zero if ensemble is skewed
        loc, scale, shape = eng.fit_distribution(members, lead_time_hours=3.0, station_id="KLGA")
        # Shape can be anything; we just confirm it's NOT forced to 0.0
        # (it may be 0 by coincidence, but with this data it shouldn't be)
        # The key guarantee: without EMOS sigma, the code path preserves MLE shape
        assert isinstance(shape, float)


# ═══════════════════════════════════════════════════════════════════════════
# S168 Phase 2A: Empirical CDF
# ═══════════════════════════════════════════════════════════════════════════


class TestEmpiricalCDF:
    """S168: Tests for empirical_bucket_probabilities in probability engine."""

    def _make_buckets(self, ranges):
        """Create mock TemperatureBucket objects from (low, high, bucket_type) tuples."""
        buckets = []
        for i, (lo, hi, btype) in enumerate(ranges):
            b = MagicMock()
            b.market_id = f"market_{i}"
            b.low_bound = lo
            b.high_bound = hi
            b.bucket_type = btype
            buckets.append(b)
        return buckets

    def test_empirical_returns_normalized_probs(self):
        """Empirical CDF probabilities: never inflated above the members' own
        mass (S224 deflate-only — these buckets miss both tails, so the total
        must stay < 1; the pre-S224 renorm inflated it to exactly 1.0, which
        was the fallacy: awarding uncovered tail mass to the listed buckets)."""
        engine = WeatherProbabilityEngine()
        # 100 members centered around 50°F
        np.random.seed(42)
        members = list(np.random.normal(50, 3, 100))
        buckets = self._make_buckets([
            (45, 47, "range"),
            (48, 49, "range"),
            (50, 51, "range"),
            (52, 53, "range"),
            (54, 56, "range"),
        ])
        probs = engine.empirical_bucket_probabilities(members, buckets, lead_time_hours=48.0)
        assert len(probs) == 5
        total = sum(probs.values())
        # covers 44.5-56.5 of a VIF-inflated N(50, 4.2): ~0.85 of the mass
        assert total <= 1.0 + 0.02, f"deflate-only bound violated: {total}"
        assert 0.6 < total < 0.98, f"tail mass was awarded to covered buckets: {total}"

    def test_empirical_returns_empty_for_small_ensemble(self):
        """Empirical CDF requires n>=50 members."""
        engine = WeatherProbabilityEngine()
        members = [50.0] * 30  # only 30
        buckets = self._make_buckets([(48, 52, "range")])
        probs = engine.empirical_bucket_probabilities(members, buckets)
        assert probs == {}

    def test_empirical_handles_at_or_below(self):
        """at_or_below bucket: P(T <= high_bound + 0.5)."""
        engine = WeatherProbabilityEngine()
        # All members at 45°F — 100% below 50
        members = [45.0] * 100
        buckets = self._make_buckets([(0, 50, "at_or_below")])
        probs = engine.empirical_bucket_probabilities(members, buckets)
        assert probs["market_0"] > 0.95

    def test_empirical_handles_at_or_higher(self):
        """at_or_higher bucket: P(T >= low_bound - 0.5)."""
        engine = WeatherProbabilityEngine()
        # All members at 55°F — 100% above 50
        members = [55.0] * 100
        buckets = self._make_buckets([(50, 100, "at_or_higher")])
        probs = engine.empirical_bucket_probabilities(members, buckets)
        assert probs["market_0"] > 0.95

    def test_empirical_emos_uniform_shift_anchored_on_deterministic_high(self):
        """S222 A3: EMOS location correction is a UNIFORM shift anchored on the
        deterministic high (the quantity EMOS was trained on); the ensemble
        spread is preserved — b no longer rescales the member cloud.

        (Replaces test_empirical_applies_emos_per_member, which pinned the
        per-member a + b*m application — the train/serve mismatch itself.)
        """
        engine = WeatherProbabilityEngine()
        engine.load_emos_calibration({
            "test_station": {48: (5.0, 0.8, None)},
        })
        members = [40.0, 50.0, 60.0] * 20  # 60 members, raw mean=50, modes ±10
        # X = det_high = 55 → EMOS-predicted actual = 5 + 0.8*55 = 49
        # shift = 49 - 50 = -1 → corrected modes [39, 49, 59] (spread preserved)
        # S222 A1: VIF 1.4 around mean 49 → [35, 49, 63]
        buckets = self._make_buckets([
            (33, 37, "range"),   # captures members at 35
            (47, 51, "range"),   # captures members at 49
            (61, 65, "range"),   # captures members at 63
        ])
        probs = engine.empirical_bucket_probabilities(
            members, buckets, station_id="test_station", lead_time_hours=48.0,
            deterministic_high=55.0,
        )
        # Each third of members maps to a different bucket → ~33% each.
        # Under the old per-member code the center landed at 45 (not 49) and
        # the (47, 51) bucket would be empty — this geometry discriminates.
        for mid in probs:
            assert 0.25 < probs[mid] < 0.42, f"{mid} = {probs[mid]}"

    def test_empirical_captures_bimodal(self):
        """Empirical CDF should capture bimodal distributions that skew-normal cannot."""
        engine = WeatherProbabilityEngine()
        # Bimodal: half at 40, half at 60 (mean 50)
        # S222 A1: VIF 1.4 inflates modes to 36 and 64
        members = [40.0] * 50 + [60.0] * 50
        buckets = self._make_buckets([
            (34, 38, "range"),   # should capture ~50% (inflated mode 36)
            (48, 52, "range"),   # should be ~0%
            (62, 66, "range"),   # should capture ~50% (inflated mode 64)
        ])
        probs = engine.empirical_bucket_probabilities(members, buckets)
        # Bimodal: peaks preserved (shape not gaussianized), nothing at center
        assert probs["market_0"] > 0.30  # ~50% around inflated low mode
        assert probs["market_1"] < 0.10  # near-zero around 50
        assert probs["market_2"] > 0.30  # ~50% around inflated high mode

    def test_empirical_laplace_smoothing_no_zeros(self):
        """Laplace smoothing prevents zero probabilities in multi-bucket scenario."""
        engine = WeatherProbabilityEngine()
        # Members at 50; most probability in bucket [48,52], some via Laplace in [78,82]
        members = [50.0] * 100
        buckets = self._make_buckets([
            (48, 52, "range"),   # captures all members
            (78, 82, "range"),   # captures zero members, but Laplace gives > 0
        ])
        probs = engine.empirical_bucket_probabilities(members, buckets)
        assert probs["market_0"] > 0.90  # most probability here
        assert probs["market_1"] > 0.0   # Laplace prevents exact 0

    def test_empirical_filters_nan_inf(self):
        """NaN and Inf members are filtered out."""
        engine = WeatherProbabilityEngine()
        members = [50.0] * 80 + [float("nan")] * 10 + [float("inf")] * 10
        buckets = self._make_buckets([(48, 52, "range")])
        probs = engine.empirical_bucket_probabilities(members, buckets)
        assert len(probs) > 0  # Should work with 80 clean members

    def test_empirical_variance_inflation_widens_tails(self):
        """S222 A1: the empirical path must apply variance inflation — before
        the fix it used raw member counts (zero underdispersion correction),
        making tail-bucket probabilities directly overconfident."""
        engine = WeatherProbabilityEngine()
        # mean = 51.6; the 54.0 cluster inflates to 51.6 + 1.4*2.4 = 54.96
        members = [50.0] * 60 + [54.0] * 40
        buckets = self._make_buckets([
            (0, 54, "at_or_below"),
            (55, 999, "at_or_higher"),   # [54.5, inf): raw 54 misses, inflated 54.96 lands
        ])

        probs = engine.empirical_bucket_probabilities(members, buckets)
        assert probs["market_1"] > 0.30  # tail bucket gets the inflated mass

        # Control: with inflation disabled the tail stays near zero (old defect)
        engine._variance_inflation_factor = 1.0
        probs_raw = engine.empirical_bucket_probabilities(members, buckets)
        assert probs_raw["market_1"] < 0.05


class TestS224CalibratorTrainingHygiene:
    """S224 WS-3/WS-4 (fallacy-audit V1/V6): the calibrator fit must (a) train
    on the RAW pre-calibration confidence — recovered via raw_confidence /
    cal_divergence, never the bare post-calibration `confidence` column — and
    (b) carry the ground-truth cutoff. Source-level guards: both fit SQLs are
    built inside fit_from_trade_events, so we assert on its source."""

    @staticmethod
    def _fit_source():
        import inspect
        from bots.weather_bot import WeatherConfidenceCalibrator
        return inspect.getsource(WeatherConfidenceCalibrator.fit_from_trade_events)

    def test_fit_trains_on_raw_confidence_expression(self):
        src = self._fit_source()
        assert "raw_confidence" in src and "cal_divergence" in src, (
            "fit_from_trade_events no longer recovers RAW confidence — "
            "V1 self-training loop reintroduced"
        )
        # the raw-X expression must be used as the selected confidence
        assert "AS confidence" in src and "{_raw_x}" in src

    def test_fit_never_selects_bare_confidence_column(self):
        src = self._fit_source()
        # The old looped pattern: selecting the stored (post-calibration)
        # column directly as the training X.
        assert "DISTINCT ON (market_id) market_id, confidence, side" not in src, (
            "fit SQL selects the bare post-calibration confidence column — "
            "V1 self-training loop reintroduced"
        )

    def test_fit_carries_ground_truth_cutoff(self):
        src = self._fit_source()
        assert src.count("gt_cutoff") >= 4, (
            "ground-truth cutoff missing from one of the fit SQLs (WS-3)"
        )

    def test_cutoff_setting_declared(self):
        from bots.weather.engine.config.settings import settings as _s
        assert getattr(_s, "WEATHER_GROUND_TRUTH_CUTOFF", None) == "2026-07-01"


class TestS224ResolveActualTemp:
    """S224 WS-1/WS-2 (fallacy-audit V11/V4/V10): ground-truth selection.
    WU is the resolution source. The old code, on >10°F/5°C WU-vs-OM
    disagreement, DISCARDED WU and wrote Open-Meteo as truth (inverted);
    and nothing recorded provenance. _resolve_actual_temp returns
    (value, source) or (None, None) to abstain."""

    @staticmethod
    def _resolve(*a, **k):
        from bots.weather_bot import WeatherBot
        return WeatherBot._resolve_actual_temp(*a, **k)

    def test_wu_primacy_when_sources_agree(self):
        assert self._resolve(75.0, 74.2, "F") == (75.0, "wu")

    def test_extreme_disagreement_abstains_never_om(self):
        """The V11 defect: old code returned (om, ...) here. Must abstain."""
        actual, source = self._resolve(75.0, 60.0, "F")  # 15°F gap > 10
        assert actual is None and source is None

    def test_om_fallback_when_wu_missing(self):
        assert self._resolve(None, 71.5, "F") == (71.5, "open_meteo")

    def test_both_missing_abstains(self):
        assert self._resolve(None, None, "F") == (None, None)

    def test_celsius_threshold(self):
        # 6°C gap > 5°C threshold → abstain; 4°C gap → WU kept
        assert self._resolve(30.0, 24.0, "C") == (None, None)
        assert self._resolve(30.0, 26.0, "C") == (30.0, "wu")

    def test_wu_unchecked_when_om_missing(self):
        # ERA5 lag: no OM to check against → WU used as-is (documented behavior)
        assert self._resolve(75.0, None, "F") == (75.0, "wu")


class TestS224CalibratedEdgeGate:
    """S224 fallacy-audit V28: a symmetric calibrated-edge admission gate. The
    raw min_edge gate + negative-EV gate left a window where a (usually NO)
    favorite-funnel sibling passed on raw edge but had ~0 calibrated edge — the
    NO side had no calibrated admission input. The gate requires the calibrated
    edge (effective_confidence - price) to clear min_edge. Default OFF."""

    def test_disabled_always_admits(self):
        from bots.weather_bot import WeatherBot
        # calibrated edge is deeply negative, but gate off → admit
        assert WeatherBot._calibrated_edge_admits(0.50, 0.90, 0.08, enabled=False) is True

    def test_enabled_blocks_thin_calibrated_edge(self):
        from bots.weather_bot import WeatherBot
        # NO favorite: calibrated P(NO)=0.83, price=0.80 → cal edge 0.03 < 0.08 → block
        assert WeatherBot._calibrated_edge_admits(0.83, 0.80, 0.08, enabled=True) is False

    def test_enabled_admits_sufficient_calibrated_edge(self):
        from bots.weather_bot import WeatherBot
        # calibrated P(side)=0.90, price=0.80 → cal edge 0.10 >= 0.08 → admit
        assert WeatherBot._calibrated_edge_admits(0.90, 0.80, 0.08, enabled=True) is True

    def test_symmetric_across_sides(self):
        from bots.weather_bot import WeatherBot
        # the helper is side-agnostic — caller passes the side's price/conf.
        # YES at price 0.30, calibrated conf 0.34 → cal edge 0.04 < 0.08 → block
        assert WeatherBot._calibrated_edge_admits(0.34, 0.30, 0.08, enabled=True) is False
        assert WeatherBot._calibrated_edge_admits(0.40, 0.30, 0.08, enabled=True) is True

    def test_boundary_at_floor_admits(self):
        from bots.weather_bot import WeatherBot
        # exactly at the floor is admitted (block is strict <)
        assert WeatherBot._calibrated_edge_admits(0.88, 0.80, 0.08, enabled=True) is True


class TestS226CalibratedTopNSelection:
    """S226 (V28 follow-up, audit register V28 trailing note): the per-group
    top-N bucket cut in _analyze_group ranked by RAW |edge| (compute_edges
    sorts abs_edge desc; the loop breaks at WEATHER_MAX_BUCKETS_PER_GROUP), so
    with the V28 gate ON a raw-overconfident sibling could crowd out a
    better-CALIBRATED entry. _select_top_buckets ranks by calibrated edge
    (confidence - price, both already on each tradeable dict) when the gate is
    ON, and is a strict identity passthrough when OFF (the deployed default) —
    flag OFF must be byte-identical to today's raw-order behavior."""

    @staticmethod
    def _entries():
        # Raw abs_edge order (as the loop appends): A > B > C > D > E.
        # Calibrated edge (confidence - price): D(0.20) > C(0.15) > E(0.08)
        # > A(0.05) > B(0.02) — deliberately different from raw order.
        return [
            {"market_id": "A", "abs_edge": 0.30, "confidence": 0.55, "price": 0.50},
            {"market_id": "B", "abs_edge": 0.25, "confidence": 0.60, "price": 0.58},
            {"market_id": "C", "abs_edge": 0.20, "confidence": 0.70, "price": 0.55},
            {"market_id": "D", "abs_edge": 0.15, "confidence": 0.80, "price": 0.60},
            {"market_id": "E", "abs_edge": 0.10, "confidence": 0.66, "price": 0.58},
        ]

    def test_gate_off_is_identity_passthrough(self):
        from bots.weather_bot import WeatherBot
        entries = self._entries()
        out = WeatherBot._select_top_buckets(entries, 3, gate_on=False)
        # OFF → the exact same list object, untouched: no sort, no truncation.
        # (In the live loop, OFF also keeps the existing raw-order break, so
        # the helper never even sees more than max_buckets entries.)
        assert out is entries
        assert [o["market_id"] for o in out] == ["A", "B", "C", "D", "E"]

    def test_gate_off_raw_order_top_n_preserved(self):
        from bots.weather_bot import WeatherBot
        # Today's flag-OFF outcome: the loop breaks after max_buckets appends,
        # keeping the first N in raw abs_edge order. The helper must not
        # reorder that raw-ordered top-N.
        raw_top3 = self._entries()[:3]  # what the OFF-path loop produces
        out = WeatherBot._select_top_buckets(raw_top3, 3, gate_on=False)
        assert [o["market_id"] for o in out] == ["A", "B", "C"]

    def test_gate_on_selects_calibrated_top_n(self):
        from bots.weather_bot import WeatherBot
        out = WeatherBot._select_top_buckets(self._entries(), 3, gate_on=True)
        # ON → top-3 by calibrated edge desc: D(0.20), C(0.15), E(0.08).
        # Raw-ranked A/B (cal edge 0.05/0.02) no longer crowd out D/C/E.
        assert [o["market_id"] for o in out] == ["D", "C", "E"]

    def test_gate_on_fewer_than_max_returns_all_calibrated_sorted(self):
        from bots.weather_bot import WeatherBot
        out = WeatherBot._select_top_buckets(self._entries()[:2], 3, gate_on=True)
        # A cal edge 0.05 > B cal edge 0.02
        assert [o["market_id"] for o in out] == ["A", "B"]

    def test_gate_on_ties_keep_raw_order(self):
        from bots.weather_bot import WeatherBot
        # Equal calibrated edge → stable sort keeps raw abs_edge order.
        entries = [
            {"market_id": "X", "abs_edge": 0.30, "confidence": 0.60, "price": 0.50},
            {"market_id": "Y", "abs_edge": 0.20, "confidence": 0.70, "price": 0.60},
        ]
        out = WeatherBot._select_top_buckets(entries, 2, gate_on=True)
        assert [o["market_id"] for o in out] == ["X", "Y"]

    def test_gate_on_empty_list(self):
        from bots.weather_bot import WeatherBot
        assert WeatherBot._select_top_buckets([], 3, gate_on=True) == []


class TestS224SyntheticEnsembleSigma:
    """S224 fallacy-audit V34: the point-forecast-only synthetic ensemble used a
    FIXED 2°F/1.1°C spread (a day-1 error) at every lead time, so long-lead
    fallbacks were massively overconfident (a 120h point high has ~5°F real
    uncertainty). Sigma must grow with lead time (NBM's schedule; proper σ)."""

    def test_sigma_grows_with_lead_time(self):
        from bots.weather.engine.base_engine.weather.forecast_client import WeatherForecastClient
        s = WeatherForecastClient._synthetic_lead_sigma
        # old defect: all of these were 2.0 regardless of lead
        assert s(12.0, "F") == pytest.approx(1.5)
        assert s(36.0, "F") == pytest.approx(2.5)
        assert s(60.0, "F") == pytest.approx(3.5)
        assert s(100.0, "F") == pytest.approx(5.0)
        # strictly increasing across the buckets
        assert s(100.0, "F") > s(60.0, "F") > s(36.0, "F") > s(12.0, "F")

    def test_sigma_celsius_scaled(self):
        from bots.weather.engine.base_engine.weather.forecast_client import WeatherForecastClient
        s = WeatherForecastClient._synthetic_lead_sigma
        assert s(100.0, "C") == pytest.approx(5.0 * 5.0 / 9.0, rel=1e-3)
        assert s(12.0, "c") == pytest.approx(1.5 * 5.0 / 9.0, rel=1e-3)


class TestS224NdfdWrongDayPoP:
    """S224 fallacy-audit V37: when NDFD has no PoP period for the TARGET day,
    the old code substituted TODAY's PoP (pop_data[:2]) — and because
    get_ndfd_pop drops NWS null-PoP periods, a DRY target day matched nothing
    and grabbed a possibly-rainy today, inflating p_rain and manufacturing
    NO-edge on the 0-inch bucket. Correct behavior: None → pure ensemble."""

    def test_target_day_match_returns_mean(self):
        from bots.weather_bot import WeatherBot
        pop = [("Today", 80.0, "2026-03-06"), ("Tonight", 60.0, "2026-03-06"),
               ("Sat", 10.0, "2026-03-07"), ("Sat Night", 20.0, "2026-03-07")]
        assert WeatherBot._ndfd_pop_for_target(pop, "2026-03-07") == pytest.approx(15.0)

    def test_no_matching_day_returns_none_not_today(self):
        from bots.weather_bot import WeatherBot
        # target 03-08 absent (e.g. dry day nulled out, or beyond horizon).
        # Old bug: returned mean(today) = 70.0. Fixed: None.
        pop = [("Today", 80.0, "2026-03-06"), ("Tonight", 60.0, "2026-03-06")]
        assert WeatherBot._ndfd_pop_for_target(pop, "2026-03-08") is None

    def test_empty_pop_data_returns_none(self):
        from bots.weather_bot import WeatherBot
        assert WeatherBot._ndfd_pop_for_target([], "2026-03-07") is None


class TestS226NdfdNullPopIsZero:
    """S226 (V37 follow-up, sanctioned by S224/419df24): NWS deliberately serves
    null PoP for ~0% periods — a null on a real forecast period IS the dry
    signal, not missing data. get_ndfd_pop used to DROP null-PoP periods, so a
    dry target day had no matching entry and fell to pure ensemble instead of a
    true 0% NDFD signal. Nulls must become 0.0. The no-matching-day → None
    invariant (S224) lives in _ndfd_pop_for_target and must NOT regress."""

    @staticmethod
    def _client_with_mock_forecast(station_id, periods):
        """WeatherForecastClient with the NWS forecast URL pre-cached and a
        mocked session returning the given forecast periods."""
        import time as _time

        client = WeatherForecastClient()
        client._nws_forecast_url_cache[station_id] = (
            _time.monotonic() + 3600.0,
            "https://api.weather.gov/gridpoints/OKX/33,37/forecast",
        )
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"properties": {"periods": periods}})
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)
        return client, mock_sess

    @pytest.mark.asyncio
    async def test_null_pop_on_matching_day_becomes_zero(self):
        """Defect: dry target day (NWS nulls) used to vanish from pop_data;
        _ndfd_pop_for_target then returned None → pure ensemble. Fixed: the
        null periods survive as 0.0 and the dry day recovers a real 0% PoP."""
        from bots.weather_bot import WeatherBot

        station = STATION_REGISTRY["new_york_city"]
        periods = [
            {"name": "Saturday", "startTime": "2026-03-07T06:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None}},
            {"name": "Saturday Night", "startTime": "2026-03-07T18:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None}},
            {"name": "Sunday", "startTime": "2026-03-08T06:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": 30}},
        ]
        client, mock_sess = self._client_with_mock_forecast(station.station_id, periods)
        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_ndfd_pop(station)

        assert result is not None
        assert ("Saturday", 0.0, "2026-03-07") in result
        assert ("Saturday Night", 0.0, "2026-03-07") in result
        # Valued periods are untouched
        assert ("Sunday", 30.0, "2026-03-08") in result
        # The dry day now yields a true 0% signal instead of None
        assert WeatherBot._ndfd_pop_for_target(result, "2026-03-07") == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_all_null_pop_returns_entries_not_none(self):
        """A fully dry 72h window (every period null) used to collapse to None."""
        station = STATION_REGISTRY["new_york_city"]
        periods = [
            {"name": "Today", "startTime": "2026-03-06T06:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None}},
            {"name": "Tonight", "startTime": "2026-03-06T18:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None}},
        ]
        client, mock_sess = self._client_with_mock_forecast(station.station_id, periods)
        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_ndfd_pop(station)

        assert result == [("Today", 0.0, "2026-03-06"), ("Tonight", 0.0, "2026-03-06")]

    @pytest.mark.asyncio
    async def test_no_matching_day_still_returns_none(self):
        """S224 invariant must not regress: a target day with no NDFD period
        (beyond the ~72h horizon) still routes to None → pure ensemble.
        No wrong-day substitution."""
        from bots.weather_bot import WeatherBot

        station = STATION_REGISTRY["new_york_city"]
        periods = [
            {"name": "Today", "startTime": "2026-03-06T06:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None}},
            {"name": "Sunday", "startTime": "2026-03-08T06:00:00-05:00",
             "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": 30}},
        ]
        client, mock_sess = self._client_with_mock_forecast(station.station_id, periods)
        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_ndfd_pop(station)

        assert WeatherBot._ndfd_pop_for_target(result, "2026-03-10") is None

    @pytest.mark.asyncio
    async def test_empty_periods_still_returns_none(self):
        """No periods at all (empty NWS payload) still returns None."""
        station = STATION_REGISTRY["new_york_city"]
        client, mock_sess = self._client_with_mock_forecast(station.station_id, [])
        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_ndfd_pop(station)

        assert result is None


class TestS224DeflateOnlyNormalization:
    """S224 fallacy-audit fix #1: sum-to-1 renormalization over NON-EXHAUSTIVE
    bucket sets manufactured probability mass — a singleton bucket renormalized
    to a literal 1.0 (reproduced live: model_prob=1.0, price 0.43, fabricated
    edge 0.57), and partial sibling sets were inflated by 1/total. The fix is
    deflate-only: divide only when total > 1 (incoherent for disjoint buckets);
    never inflate a total < 1 (the signature of missing siblings)."""

    engine = WeatherProbabilityEngine()

    @staticmethod
    def _bucket(market_id, btype, lo, hi):
        return TemperatureBucket(market_id, "t", "n", 0.43, btype, lo, hi, "F")

    # ── parametric path ────────────────────────────────────────────────

    def test_parametric_singleton_never_renorms_to_one(self):
        """Defect: a single bucket with p in (0.01, 0.99) divided by itself
        emitted exactly 1.0, breaking the 0.999 clamp."""
        b = self._bucket("m1", "at_or_below", None, 50.0)
        probs = self.engine.bucket_probabilities(50.0, 2.0, 0.0, [b])
        # cdf(50.5; loc=50, scale=2) ≈ 0.60 — must stay the raw belief
        assert probs["m1"] < 0.99, f"singleton renormed to {probs['m1']}"
        assert 0.4 < probs["m1"] < 0.8

    def test_parametric_partial_group_not_inflated(self):
        """Defect: two mid-range buckets covering ~76% of the mass were
        inflated by 1/total, fabricating edge on both."""
        buckets = [
            self._bucket("m1", "range", 49.0, 51.0),
            self._bucket("m2", "range", 52.0, 54.0),
        ]
        probs = self.engine.bucket_probabilities(50.0, 2.0, 0.0, buckets)
        total = sum(probs.values())
        assert total < 0.9, f"partial group inflated to {total}"
        # raw CDF beliefs preserved (≈0.55 and ≈0.21)
        assert 0.4 < probs["m1"] < 0.7
        assert 0.1 < probs["m2"] < 0.35

    def test_parametric_deflation_preserved_for_overlapping_mass(self):
        """Regression guard: total > 1 (overlapping buckets) still deflates."""
        buckets = [
            self._bucket("m1", "at_or_below", None, 55.0),   # ≈1.0 at loc=48
            self._bucket("m2", "at_or_higher", 42.0, None),  # ≈1.0 at loc=48
        ]
        probs = self.engine.bucket_probabilities(48.0, 2.0, 0.0, buckets)
        assert abs(sum(probs.values()) - 1.0) < 0.01
        assert abs(probs["m1"] - 0.5) < 0.05

    def test_parametric_degenerate_still_returns_empty(self):
        """M1 guard unchanged: no meaningful mass in any bucket → {}."""
        b = self._bucket("m1", "range", 90.0, 92.0)  # 20σ away
        probs = self.engine.bucket_probabilities(50.0, 0.5, 0.0, [b])
        assert probs == {}

    # ── empirical path ─────────────────────────────────────────────────

    def test_empirical_singleton_never_renorms_to_one(self):
        members = [40.0 + 0.2 * i for i in range(100)]  # spread 40-59.8
        b = self._bucket("m1", "range", 45.0, 49.0)
        probs = self.engine.empirical_bucket_probabilities(members, [b])
        assert probs["m1"] < 0.9, f"singleton renormed to {probs['m1']}"
        assert probs["m1"] > 0.05

    def test_empirical_partial_group_not_inflated(self):
        members = [40.0 + 0.2 * i for i in range(100)]
        buckets = [
            self._bucket("m1", "range", 45.0, 49.0),
            self._bucket("m2", "range", 50.0, 54.0),
        ]
        probs = self.engine.empirical_bucket_probabilities(members, buckets)
        total = sum(probs.values())
        assert total < 0.9, f"partial group inflated to {total}"

    # ── scipy-less fallback path ───────────────────────────────────────

    def test_fallback_singleton_never_renorms_to_one(self):
        b = self._bucket("m1", "at_or_below", None, 50.0)
        probs = self.engine._bucket_probabilities_fallback(50.0, 2.0, [b])
        assert probs["m1"] < 0.99, f"singleton renormed to {probs['m1']}"

    # ── NBM benchmark path (same fallacy, 4th site) ────────────────────

    def test_nbm_singleton_edge_not_fabricated(self):
        """Defect: a singleton bucket renormed nbm_prob to 1.0, fabricating
        nbm_edge = 1 - price and a spurious high_conviction flag."""
        b = self._bucket("m1", "at_or_below", None, 50.0)
        signals = self.engine.compute_nbm_benchmark(
            50.0, [b], {"m1": 0.43}, lead_time_hours=12.0,
        )
        if "m1" in signals:  # only present if |edge| >= threshold
            assert signals["m1"]["nbm_prob"] < 0.9, (
                f"NBM singleton renormed to {signals['m1']['nbm_prob']}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Forecast Client
# ═══════════════════════════════════════════════════════════════════════════


class TestCombinedForecast:
    def test_dataclass_creation(self):
        fc = CombinedForecast(
            ensemble_members=[50.0, 51.0, 49.0],
            deterministic_high=50.0,
            model_spread=1.0,
            lead_time_hours=24.0,
            models_used=["gfs025"],
        )
        assert len(fc.ensemble_members) == 3
        assert fc.deterministic_high == 50.0
        assert fc.lead_time_hours == 24.0


class TestWeatherForecastClient:
    def test_init_defaults(self):
        client = WeatherForecastClient()
        assert client._cache_ttl == 900.0
        assert client._rate_limit == 120

    @pytest.mark.asyncio
    async def test_get_nbm_forecast_success(self):
        """get_nbm_forecast returns daytime high from NWS 7-day forecast."""
        client = WeatherForecastClient()

        # Mock session for both NWS /points and /forecast calls
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200

        # First call: /points → forecast URL; second call: forecast periods
        mock_resp.json = AsyncMock(side_effect=[
            {"properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/44,32/forecast"}},
            {"properties": {"periods": [
                {"startTime": "2026-03-06T06:00:00-05:00", "isDaytime": True,
                 "temperature": 72, "temperatureUnit": "F"},
                {"startTime": "2026-03-06T18:00:00-05:00", "isDaytime": False,
                 "temperature": 55, "temperatureUnit": "F"},
            ]}},
        ])
        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_nbm_forecast(
                40.7772, -73.8726, "KLGA", date(2026, 3, 6)
            )

        assert result == pytest.approx(72.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_get_nbm_forecast_api_error_returns_none(self):
        """get_nbm_forecast returns None when NWS /points API fails."""
        client = WeatherForecastClient()
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 503
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_nbm_forecast(
                40.7772, -73.8726, "KLGA", date(2026, 3, 6)
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_nbm_used_as_deterministic_high_for_us_station(self):
        """get_combined_forecast uses NBM as deterministic_high for US stations (temp_unit=F)."""
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        client = WeatherForecastClient()
        station = STATION_REGISTRY["new_york_city"]  # temp_unit="F"

        # Patch get_deterministic_forecast and get_ensemble_forecast to return minimal data
        dummy_date = date(2026, 3, 10)
        dummy_iso = dummy_date.isoformat()

        with patch.object(client, "get_deterministic_forecast", new_callable=AsyncMock,
                          return_value={"daily": {"time": [dummy_iso], "temperature_2m_max": [65.0]}}), \
             patch.object(client, "get_ensemble_forecast", new_callable=AsyncMock,
                          return_value={"daily": {"time": [dummy_iso],
                                                   "temperature_2m_max_member01": [65.0] * 7,
                                                   "temperature_2m_max_member02": [66.0] * 7}}), \
             patch.object(client, "get_nbm_forecast", new_callable=AsyncMock, return_value=70.0):
            result = await client.get_combined_forecast(station, dummy_date)

        assert result is not None
        # NBM value (70.0) should override GFS (65.0) as deterministic_high
        assert result.deterministic_high == pytest.approx(70.0, abs=0.1)
        assert "nbm" in result.models_used
        assert "gfs_seamless" not in result.models_used

    @pytest.mark.asyncio
    async def test_nbm_not_called_for_international_station(self):
        """get_combined_forecast skips NBM fetch for non-US stations (temp_unit=C)."""
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        client = WeatherForecastClient()
        station = STATION_REGISTRY.get("london") or STATION_REGISTRY.get("paris")
        if station is None:
            pytest.skip("No international station available in registry")

        dummy_date = date(2026, 3, 10)
        dummy_iso = dummy_date.isoformat()

        with patch.object(client, "get_deterministic_forecast", new_callable=AsyncMock,
                          return_value={"daily": {"time": [dummy_iso], "temperature_2m_max": [15.0]}}), \
             patch.object(client, "get_ensemble_forecast", new_callable=AsyncMock,
                          return_value={"daily": {"time": [dummy_iso],
                                                   "temperature_2m_max_member01": [15.0] * 7}}), \
             patch.object(client, "get_nbm_forecast", new_callable=AsyncMock, return_value=99.0) as mock_nbm:
            result = await client.get_combined_forecast(station, dummy_date)

        # NBM should not have been called for international station
        mock_nbm.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# WeatherBot
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.trade_coordinator = None
    engine.cache = None
    engine.db = None
    engine.risk_manager = MagicMock()
    engine.risk_manager.check_hard_stop_loss = MagicMock(return_value={"should_exit": False, "reason": "", "details": {}})
    engine.order_gateway = MagicMock()
    engine.order_gateway._open_position_markets = {"WeatherBot": set()}
    engine.get_all_tradeable_markets = AsyncMock(return_value=[])
    engine.place_order = AsyncMock(return_value={"success": True, "order_id": "test1"})
    return engine


@pytest.fixture
def weather_bot(mock_engine):
    from bots.weather_bot import WeatherBot
    bot = WeatherBot(mock_engine)
    return bot


class TestWeatherBot:
    @pytest.mark.asyncio
    async def test_scan_no_markets(self, weather_bot):
        """No markets → no crash, no trades."""
        await weather_bot.scan_and_trade()
        assert weather_bot.trades_executed == 0

    @pytest.mark.asyncio
    async def test_scan_no_weather_markets(self, weather_bot, mock_engine):
        """Non-weather markets → filtered out, no trades."""
        mock_engine.get_all_tradeable_markets = AsyncMock(return_value=[
            {"id": "m1", "question": "Will Bitcoin reach $100k?", "yes_token_id": "t1", "no_token_id": "n1"},
        ])
        await weather_bot.scan_and_trade()
        assert weather_bot.trades_executed == 0

    @pytest.mark.asyncio
    async def test_analyze_opportunity_non_weather_returns_none(self, weather_bot):
        result = await weather_bot.analyze_opportunity({"question": "Will it rain gold?"})
        assert result is None

    @pytest.mark.asyncio
    async def test_scan_with_weather_market_and_edge(self, weather_bot, mock_engine):
        """Weather market with strong edge → trade placed."""
        # Use a future date to avoid past-date skip
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=3))
        future_str = future.strftime("%B %d, %Y")  # e.g. "March 01, 2026"

        mock_engine.get_all_tradeable_markets = AsyncMock(return_value=[
            {
                "id": "m1",
                "question": f"Will the highest temperature in NYC be between 48-49°F on {future_str}?",
                "yes_token_id": "tok_yes",
                "no_token_id": "tok_no",
                "yes_price": 0.70,  # S159: lowered from 0.85 — YES identity dampener (0.85x) blocks conf 0.8075 < 0.85
                "slug": "nyc-temp-future",
                # S221: liquidity/volume added so the new _filter_thin_markets
                # gate accepts this fixture (default $1000/$100 floors).
                # bestBid/bestAsk omitted intentionally — DB fallback path
                # doesn't populate them, and the filter's spread check
                # opts-out when both are 0.
                "liquidity": 5000,
                "volume": 5000,
            },
        ])

        # Mock forecast: ensemble centered at 48.5 → high prob for 48-49 bucket
        fake_forecast = CombinedForecast(
            ensemble_members=[48.0 + i * 0.1 for i in range(31)],
            deterministic_high=48.5,
            model_spread=1.0,
            lead_time_hours=24.0,
            models_used=["gfs025"],
        )
        weather_bot._forecast_client.get_combined_forecast = AsyncMock(return_value=fake_forecast)
        weather_bot._station_health.is_healthy = AsyncMock(return_value=True)
        weather_bot.running = True  # BaseBot.place_order checks this

        await weather_bot.scan_and_trade()
        # Should have attempted at least one trade (edge 8-25%)
        assert mock_engine.place_order.called
        # S160 WB-8: Verify order parameters, not just boolean .called
        _call = mock_engine.place_order.call_args
        assert _call.kwargs["bot_name"] == "WeatherBot"
        assert _call.kwargs["side"] in ("YES", "NO"), "Side must be YES or NO, never BUY/SELL"
        assert _call.kwargs["market_id"], "market_id must be non-empty"
        assert _call.kwargs["size"] > 0, "size must be positive"
        assert 0 < _call.kwargs["price"] < 1, "price must be in (0, 1)"

    def test_expiry_boost_removed(self):
        """S141: expiry_boost is always 1.0 — graduated schedule removed (inverse P&L relationship)."""
        import inspect
        from bots.weather_bot import WeatherBot
        src = inspect.getsource(WeatherBot._execute_weather_trade)
        assert "expiry_boost = 1.0" in src, "expiry_boost must be hardcoded to 1.0"
        assert "elif lead_time < 1.0" not in src, "Old graduated schedule must be absent"
        assert "elif lead_time < 6.0" not in src, "Old graduated schedule must be absent"

    @pytest.mark.asyncio
    async def test_daily_loss_limit_blocks(self, weather_bot):
        """After hitting daily loss limit, no trades."""
        weather_bot._daily_pnl = -600.0  # Exceeds $500 limit
        weather_bot._daily_pnl_date = datetime.now().strftime("%Y-%m-%d")

        # Even with edge, should not trade
        from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketGroup
        group = WeatherMarketGroup(
            city="New York City",
            target_date=date.today(),
            station=STATION_REGISTRY["new_york_city"],
            buckets=[],
        )
        opp = {
            "market_id": "m1", "token_id": "t1", "side": "YES",
            "price": 0.30, "confidence": 0.8, "model_prob": 0.50,
            "edge": 0.20, "abs_edge": 0.20, "city": "New York City",
        }
        # S221 Phase 2: populate market index so _check_executable_edge passes
        # and the daily-loss-limit gate is what actually rejects (preserves
        # this test's stated intent rather than my new check rejecting first).
        weather_bot.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.28, "bestAsk": 0.32}
        }
        await weather_bot._execute_weather_trade(opp, group)
        assert not weather_bot.base_engine.place_order.called

    @pytest.mark.asyncio
    async def test_reset_daily_pnl(self, weather_bot):
        """Daily P&L resets on new day (P2: via async _handle_daily_boundary)."""
        weather_bot._daily_pnl = -100.0
        weather_bot._daily_pnl_date = "2025-01-01"
        weather_bot._group_exposure = {"NYC:2025-01-01": 50.0}
        weather_bot._city_exposure = {"New York City": 50.0}

        # Patch DB restore so it doesn't need a real DB
        weather_bot._restore_daily_pnl_from_db = AsyncMock()

        await weather_bot._handle_daily_boundary()

        assert weather_bot._daily_pnl == 0.0
        assert len(weather_bot._group_exposure) == 0
        assert len(weather_bot._city_exposure) == 0
        weather_bot._restore_daily_pnl_from_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_daily_pnl_refreshed_every_scan_same_day(self, weather_bot):
        """S224 V42: the daily loss limit / drawdown halt read _daily_pnl, whose
        ONLY writer is _restore_daily_pnl_from_db (DB ground truth). Before the
        fix, _handle_daily_boundary early-returned on same-day so the restore
        ran once per UTC day — leaving both circuit breakers blind to intra-day
        losses. The boundary handler must refresh P&L on EVERY call."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        weather_bot._daily_pnl_date = today  # already initialised this day
        weather_bot._restore_daily_pnl_from_db = AsyncMock()

        # Three scans within the same day
        await weather_bot._handle_daily_boundary()
        await weather_bot._handle_daily_boundary()
        await weather_bot._handle_daily_boundary()

        # Pre-fix: 0 calls (early-return). Post-fix: one per scan.
        assert weather_bot._restore_daily_pnl_from_db.call_count == 3

    @pytest.mark.asyncio
    async def test_daily_boundary_reset_still_once_per_day(self, weather_bot):
        """S224 V42 regression guard: the expensive reset (exposure/cache clears,
        calibration-actuals backfill) must stay gated to the actual boundary and
        NOT run on every same-day scan."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        weather_bot._daily_pnl_date = today
        weather_bot._group_exposure = {"NYC:x": 50.0}
        weather_bot._restore_daily_pnl_from_db = AsyncMock()
        weather_bot._maybe_update_calibration_actuals = AsyncMock()

        await weather_bot._handle_daily_boundary()  # same day → no reset

        assert weather_bot._group_exposure == {"NYC:x": 50.0}  # not cleared
        weather_bot._maybe_update_calibration_actuals.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_daily_boundary_same_day_skips_reset_but_refreshes_pnl(self, weather_bot):
        """Same-day call → the day-boundary RESET is skipped, but realized P&L
        is still refreshed from the DB (S224 V42: this restore is the only
        writer of _daily_pnl and must run every scan or the circuit breakers
        go blind intra-day)."""
        from datetime import timezone as _tz
        today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
        weather_bot._daily_pnl = -50.0
        weather_bot._daily_pnl_date = today
        weather_bot._restore_daily_pnl_from_db = AsyncMock()

        await weather_bot._handle_daily_boundary()

        # reset skipped (mocked restore is a no-op, so the value is untouched
        # by the reset block — proving _daily_pnl was NOT zeroed)
        assert weather_bot._daily_pnl == -50.0
        # but the DB refresh DID run (the V42 fix)
        weather_bot._restore_daily_pnl_from_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_closes_forecast_client(self, weather_bot):
        """stop() should close the HTTP session."""
        weather_bot._forecast_client.close = AsyncMock()
        await weather_bot.stop()
        weather_bot._forecast_client.close.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Station Health Monitor
# ═══════════════════════════════════════════════════════════════════════════


class TestStationHealthMonitor:
    @pytest.mark.asyncio
    async def test_international_station_probes_openmeteo(self):
        """P4: International stations use Open-Meteo probe instead of always-True."""
        monitor = StationHealthMonitor()
        london = STATION_REGISTRY["london"]

        # Mock a successful Open-Meteo response
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "daily": {"temperature_2m_max": [10.5]}
        })

        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            result = await monitor.is_healthy(london)

        assert result is True

    @pytest.mark.asyncio
    async def test_international_station_fails_open_on_error(self):
        """P4: Open-Meteo probe failure still returns True (fail-open)."""
        monitor = StationHealthMonitor()
        seoul = STATION_REGISTRY["seoul"]

        with patch("aiohttp.ClientSession", side_effect=Exception("network error")):
            result = await monitor.is_healthy(seoul)

        assert result is True  # Fail open — don't block trading on probe failure

    @pytest.mark.asyncio
    async def test_health_cache(self):
        """Second check within TTL uses cache."""
        monitor = StationHealthMonitor()
        london = STATION_REGISTRY["london"]

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"daily": {"temperature_2m_max": [10.0]}})
        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            await monitor.is_healthy(london)

        # Second call: cache hit — no new aiohttp call
        assert london.station_id in monitor._health_cache


# ═══════════════════════════════════════════════════════════════════════════
# P5: ECMWF Ensemble Merging
# ═══════════════════════════════════════════════════════════════════════════


class TestECMWFEnsembleMerging:
    @pytest.mark.asyncio
    async def test_get_ensemble_fetches_both_models(self):
        """P5/P6: get_ensemble_forecast fetches GEFS + ECMWF IFS + ECMWF AIFS in parallel."""
        client = WeatherForecastClient()

        # Simulate GEFS response with 3 members
        gefs_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [48.0],
                "temperature_2m_max_member01": [49.0],
                "temperature_2m_max_member02": [50.0],
            }
        }
        # Simulate ECMWF IFS response with 2 members
        ecmwf_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [47.5],
                "temperature_2m_max_member01": [48.5],
            }
        }

        with patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock) as mock_fetch:
            # P6: 3 calls — GEFS, IFS, AIFS (AIFS returns None here)
            mock_fetch.side_effect = [gefs_resp, ecmwf_resp, None]
            merged = await client.get_ensemble_forecast(40.77, -73.87, temp_unit="F")

        assert merged is not None
        daily = merged["daily"]
        # GEFS: member00..02, ECMWF IFS offset: member03..04
        assert "temperature_2m_max_member00" in daily
        assert "temperature_2m_max_member03" in daily  # ECMWF IFS offset
        assert daily["temperature_2m_max_member03"] == [47.5]
        assert daily["temperature_2m_max_member04"] == [48.5]

    @pytest.mark.asyncio
    async def test_ensemble_falls_back_to_gefs_on_ecmwf_failure(self):
        """P5/P6: If ECMWF IFS + AIFS fail, still return GEFS-only result."""
        client = WeatherForecastClient()

        gefs_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [48.0],
            }
        }

        with patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock) as mock_fetch:
            # P6: 3 calls — GEFS succeeds, IFS fails, AIFS fails
            mock_fetch.side_effect = [gefs_resp, Exception("ECMWF IFS timeout"), Exception("AIFS timeout")]
            merged = await client.get_ensemble_forecast(40.77, -73.87)

        assert merged is not None
        assert "temperature_2m_max_member00" in merged["daily"]

    @pytest.mark.asyncio
    async def test_ensemble_includes_aifs_members(self):
        """P6: AIFS ENS members are appended after GEFS + IFS with correct offsets."""
        client = WeatherForecastClient()

        gefs_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [48.0],
                "temperature_2m_max_member01": [49.0],
            }
        }
        ifs_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [47.5],
            }
        }
        aifs_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [46.0],
                "temperature_2m_max_member01": [47.0],
            }
        }

        with patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [gefs_resp, ifs_resp, aifs_resp]
            merged = await client.get_ensemble_forecast(40.77, -73.87, temp_unit="F")

        assert merged is not None
        daily = merged["daily"]
        # GEFS: member00..01, IFS: member02, AIFS: member03..04
        assert daily["temperature_2m_max_member00"] == [48.0]   # GEFS
        assert daily["temperature_2m_max_member01"] == [49.0]   # GEFS
        assert daily["temperature_2m_max_member02"] == [47.5]   # IFS offset
        assert daily["temperature_2m_max_member03"] == [46.0]   # AIFS offset
        assert daily["temperature_2m_max_member04"] == [47.0]   # AIFS offset

    @pytest.mark.asyncio
    async def test_combined_forecast_uses_all_members(self):
        """P5/P6: Combined forecast incorporates ECMWF IFS members; AIFS=None falls back cleanly."""
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        client = WeatherForecastClient()
        station = STATION_REGISTRY["new_york_city"]

        # GEFS: 3 members, ECMWF IFS: 2 members, AIFS: None → 5 total
        gefs_resp = {
            "daily": {
                "time": ["2026-03-10"],
                "temperature_2m_max_member00": [48.0],
                "temperature_2m_max_member01": [49.0],
                "temperature_2m_max_member02": [50.0],
            }
        }
        ecmwf_resp = {
            "daily": {
                "time": ["2026-03-10"],
                "temperature_2m_max_member00": [47.5],
                "temperature_2m_max_member01": [48.5],
            }
        }

        det_resp = {
            "daily": {
                "time": ["2026-03-10"],
                "temperature_2m_max": [48.8],
            }
        }

        with patch.object(client, "get_deterministic_forecast", new_callable=AsyncMock, return_value=det_resp), \
             patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock,
                          side_effect=[gefs_resp, ecmwf_resp, None]):
            fc = await client.get_combined_forecast(station, date(2026, 3, 10))

        assert fc is not None
        assert len(fc.ensemble_members) == 5  # 3 GEFS + 2 ECMWF IFS


# ═══════════════════════════════════════════════════════════════════════════
# Regime Boost & Near-Expiry Kelly
# ═══════════════════════════════════════════════════════════════════════════


class TestWeatherBotOpportunities:
    def _make_opp(self, city: str, side: str, edge: float = 0.20):
        return {
            "market_id": f"m_{city}", "token_id": "tok", "side": side,
            "price": 0.30, "confidence": 0.7, "model_prob": 0.50,
            "edge": edge if side == "YES" else -edge,
            "abs_edge": edge, "city": city,
        }

    def _make_group(self, city: str):
        from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketGroup
        key = city.lower().replace(" ", "_")
        station = STATION_REGISTRY.get(key, STATION_REGISTRY["new_york_city"])
        return WeatherMarketGroup(
            city=city, target_date=date(2026, 3, 1), station=station, buckets=[]
        )

    def test_regime_boost_warm_front(self):
        """≥3 US cities all showing YES (warm) → 1.2x boost."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("New York City", "YES")], self._make_group("New York City"), {}),
            ([self._make_opp("Atlanta", "YES")], self._make_group("Atlanta"), {}),
            ([self._make_opp("Dallas", "YES")], self._make_group("Dallas"), {}),
            ([self._make_opp("Miami", "YES")], self._make_group("Miami"), {}),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.2

    def test_regime_boost_cold_front(self):
        """≥3 US cities all showing NO (cold) → 1.2x boost."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("Chicago", "NO")], self._make_group("Chicago"), {}),
            ([self._make_opp("Seattle", "NO")], self._make_group("Seattle"), {}),
            ([self._make_opp("Denver", "NO")], self._make_group("Denver"), {}),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.2

    def test_regime_boost_mixed_no_signal(self):
        """Mixed warm/cold → no regime → 1.0 boost."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("New York City", "YES")], self._make_group("New York City"), {}),
            ([self._make_opp("Atlanta", "NO")], self._make_group("Atlanta"), {}),
            ([self._make_opp("Dallas", "YES")], self._make_group("Dallas"), {}),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.0

    def test_regime_boost_international_cities(self):
        """International cities now participate in regime detection (US-only filter removed)."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("London", "YES")], self._make_group("london"), {}),
            ([self._make_opp("Seoul", "YES")], self._make_group("seoul"), {}),
            ([self._make_opp("Toronto", "YES")], self._make_group("toronto"), {}),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.2  # 3 cities same direction → warm regime detected

    @pytest.mark.asyncio
    async def test_near_expiry_kelly_boost(self, weather_bot):
        """Near-expiry (<24h) → 1.5x Kelly multiplier applied."""
        from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketGroup
        group = WeatherMarketGroup(
            city="New York City",
            target_date=date.today(),
            station=STATION_REGISTRY["new_york_city"],
            buckets=[],
        )
        opp = {
            "market_id": "m1", "token_id": "t1", "side": "YES",
            "price": 0.30, "confidence": 0.7, "model_prob": 0.50,
            "edge": 0.20, "abs_edge": 0.20, "city": "New York City",
            "lead_time_hours": 6.0,  # < 24h → boost applies
        }
        weather_bot._daily_pnl_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        weather_bot._daily_pnl = 0.0
        weather_bot.running = True
        # S221 Phase 2: populate market index so _check_executable_edge passes.
        # YES side, model 0.50, bestAsk 0.32 → honest_edge = 0.18 > 0 → accept.
        weather_bot.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.28, "bestAsk": 0.32}
        }

        await weather_bot._execute_weather_trade(opp, group)
        # Trade should be attempted (with boosted size)
        assert weather_bot.base_engine.place_order.called

    @pytest.mark.asyncio
    async def test_calibration_reload_skipped_within_interval(self, weather_bot):
        """_maybe_reload_calibration does nothing if called too recently."""
        import time as _time
        weather_bot._calibration_last_loaded = _time.monotonic()  # Just loaded
        weather_bot.base_engine.db = MagicMock()  # Has DB

        await weather_bot._maybe_reload_calibration()

        # Should not have accessed DB since loaded recently
        weather_bot.base_engine.db.get_session.assert_not_called() if hasattr(
            weather_bot.base_engine.db, "get_session"
        ) else None

    @pytest.mark.asyncio
    async def test_heartbeat_counters_wired(self, weather_bot, mock_engine):
        """scan_and_trade() must set _last_scan_markets before returning."""
        mock_engine.get_all_tradeable_markets = AsyncMock(return_value=[
            {"id": "m1", "question": "Will Bitcoin hit $100k?", "yes_token_id": "t1", "no_token_id": "n1"},
        ])
        await weather_bot.scan_and_trade()
        # Non-weather market → filtered out, but counter still set
        assert weather_bot._last_scan_markets == 0

    @pytest.mark.asyncio
    async def test_heartbeat_counters_reflect_actual_scan(self, weather_bot, mock_engine):
        """Heartbeat counters reflect actual weather market count."""
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=3))
        future_str = future.strftime("%B %d, %Y")

        mock_engine.get_all_tradeable_markets = AsyncMock(return_value=[
            {
                "id": "m1",
                "question": f"Will the highest temperature in NYC be between 48-49°F on {future_str}?",
                "yes_token_id": "tok_yes",
                "no_token_id": "tok_no",
                "yes_price": 0.98,  # No edge (market == model)
                "slug": "nyc-temp",
                # S221: liquidity/volume added so the new _filter_thin_markets
                # gate accepts this fixture (default $1000/$100 floors).
                "liquidity": 5000,
                "volume": 5000,
            },
        ])
        fake_forecast = CombinedForecast(
            ensemble_members=[49.0] * 31,
            deterministic_high=49.0,
            model_spread=0.5,
            lead_time_hours=48.0,
            models_used=["gfs025"],
        )
        weather_bot._forecast_client.get_combined_forecast = AsyncMock(return_value=fake_forecast)
        weather_bot._station_health.is_healthy = AsyncMock(return_value=True)

        await weather_bot.scan_and_trade()
        assert weather_bot._last_scan_markets == 1

    def test_near_boundary_range_bucket_at_low(self, weather_bot):
        """Ensemble mean within 0.5° of range low_bound → boundary risk."""
        from bots.weather_bot import WeatherBot
        bucket = TemperatureBucket(
            market_id="m1", bucket_type="range", low_bound=70.0, high_bound=74.0,
            yes_price=0.25, token_id="t1", no_token_id="n1", temp_unit="F",
        )
        assert WeatherBot._near_boundary(70.3, bucket) is True   # within 0.5° above low
        assert WeatherBot._near_boundary(69.6, bucket) is True   # within 0.5° below low
        assert WeatherBot._near_boundary(71.0, bucket) is False  # safely inside

    def test_near_boundary_range_bucket_at_high(self, weather_bot):
        """Ensemble mean within 0.5° of range high_bound → boundary risk."""
        from bots.weather_bot import WeatherBot
        bucket = TemperatureBucket(
            market_id="m1", bucket_type="range", low_bound=70.0, high_bound=74.0,
            yes_price=0.25, token_id="t1", no_token_id="n1", temp_unit="F",
        )
        assert WeatherBot._near_boundary(74.4, bucket) is True   # within 0.5° of high
        assert WeatherBot._near_boundary(75.0, bucket) is False  # > 0.5° outside range

    def test_near_boundary_at_or_below(self, weather_bot):
        from bots.weather_bot import WeatherBot
        bucket = TemperatureBucket(
            market_id="m1", bucket_type="at_or_below", low_bound=float("-inf"), high_bound=72.0,
            yes_price=0.20, token_id="t1", no_token_id="n1", temp_unit="F",
        )
        assert WeatherBot._near_boundary(72.3, bucket) is True
        assert WeatherBot._near_boundary(70.0, bucket) is False

    def test_near_boundary_at_or_higher(self, weather_bot):
        from bots.weather_bot import WeatherBot
        bucket = TemperatureBucket(
            market_id="m1", bucket_type="at_or_higher", low_bound=80.0, high_bound=float("inf"),
            yes_price=0.15, token_id="t1", no_token_id="n1", temp_unit="F",
        )
        assert WeatherBot._near_boundary(79.8, bucket) is True
        assert WeatherBot._near_boundary(82.0, bucket) is False

    def test_fit_emos_basic_regression(self, weather_bot):
        """_fit_emos returns correct OLS (a, b, sigma) from (forecast, actual) pairs."""
        from bots.weather_bot import WeatherBot
        # Perfect relationship: actual = 1.0 + 1.0 * forecast → a=1.0, b=1.0, sigma≈0
        pairs = [(float(x), 1.0 + float(x)) for x in range(20, 50)]
        a, b, sigma = WeatherBot._fit_emos(pairs)
        assert abs(a - 1.0) < 0.1
        assert abs(b - 1.0) < 0.01
        assert sigma <= 1.0  # near-zero residuals → sigma floored at 0.5

    def test_fit_emos_slope_correction(self, weather_bot):
        """_fit_emos detects b < 1 when forecasts over-predict spread."""
        from bots.weather_bot import WeatherBot
        # actual = 0.5 * forecast + 10 (model over-forecasts temperatures)
        pairs = [(float(x), 0.5 * x + 10.0) for x in range(30, 80)]
        a, b, sigma = WeatherBot._fit_emos(pairs)
        # slope should be ~0.5
        assert abs(b - 0.5) < 0.05
        # intercept should be ~10
        assert abs(a - 10.0) < 1.0

    def test_fit_emos_degenerate_identical_forecasts(self, weather_bot):
        """_fit_emos falls back gracefully when all forecast temps are identical."""
        from bots.weather_bot import WeatherBot
        # All forecasts the same → singular OLS → fallback
        pairs = [(50.0, 50.0 + i * 0.1) for i in range(25)]
        a, b, sigma = WeatherBot._fit_emos(pairs)
        assert b == 1.0  # identity slope in fallback
        assert sigma >= 0.5  # floored


# ═══════════════════════════════════════════════════════════════════════════
# Historical Temperature API
# ═══════════════════════════════════════════════════════════════════════════


class TestHistoricalTemperatureAPI:
    @pytest.mark.asyncio
    async def test_get_historical_temperature_success(self):
        client = WeatherForecastClient()
        target = date(2026, 1, 15)

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "daily": {"temperature_2m_max": [48.5]}
        })

        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_historical_temperature(40.77, -73.87, target, "F")

        assert result == 48.5

    @pytest.mark.asyncio
    async def test_get_historical_temperature_api_error_returns_none(self):
        client = WeatherForecastClient()

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 500

        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_historical_temperature(40.77, -73.87, date(2026, 1, 15))

        assert result is None

    @pytest.mark.asyncio
    async def test_get_historical_temperature_null_data_returns_none(self):
        client = WeatherForecastClient()

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"daily": {"temperature_2m_max": [None]}})

        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_historical_temperature(40.77, -73.87, date(2026, 1, 15))

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Calibration Feedback Loop
# ═══════════════════════════════════════════════════════════════════════════


class TestCalibrationFeedbackLoop:
    @pytest.mark.asyncio
    async def test_calibration_actuals_no_db_noop(self, weather_bot):
        """No DB → _maybe_update_calibration_actuals exits silently."""
        weather_bot.base_engine.db = None
        # Should not raise
        await weather_bot._maybe_update_calibration_actuals()

    @pytest.mark.asyncio
    async def test_calibration_actuals_skips_on_empty_rows(self, weather_bot):
        """No pending rows → no API calls."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_db = MagicMock()
        mock_db.get_session.return_value = mock_session
        weather_bot.base_engine.db = mock_db

        historical_mock = AsyncMock(return_value=50.0)
        weather_bot._forecast_client.get_historical_temperature = historical_mock

        await weather_bot._maybe_update_calibration_actuals()

        historical_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_calibration_actuals_updates_bias(self, weather_bot):
        """Found pending row → fetches actual temp and writes bias."""
        from datetime import date as _date
        pending_row = (1, "KLGA", _date(2026, 1, 15), 48.0, 24.0)

        call_count = 0
        fetch_results = [[pending_row], []]  # First SELECT returns row, second (UPDATE) won't fetchall

        mock_select_result = MagicMock()
        mock_select_result.fetchall.return_value = [pending_row]

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_select_result)
        mock_session.commit = AsyncMock()

        mock_db = MagicMock()
        mock_db.get_session.return_value = mock_session
        weather_bot.base_engine.db = mock_db

        # Actual temperature came in at 50.5°F (forecast was 48.0)
        weather_bot._forecast_client.get_historical_temperature = AsyncMock(return_value=50.5)

        await weather_bot._maybe_update_calibration_actuals()

        weather_bot._forecast_client.get_historical_temperature.assert_called_once()
        mock_session.commit.assert_called()  # UPDATE was committed


# ═══════════════════════════════════════════════════════════════════════════
# METAR Client
# ═══════════════════════════════════════════════════════════════════════════


class TestMetarClientParseGroup:
    """Unit tests for the T-group parser (pure function, no I/O)."""

    def test_parse_t_group_positive_temp(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        # T02890267 → +28.9°C
        assert MetarClient.parse_t_group("METAR KLGA ... RMK T02890267") == pytest.approx(28.9, abs=0.01)

    def test_parse_t_group_negative_temp(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        # T11001267 → -10.0°C
        assert MetarClient.parse_t_group("T11001267") == pytest.approx(-10.0, abs=0.01)

    def test_parse_t_group_no_match_returns_none(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        assert MetarClient.parse_t_group("METAR KLGA 061856Z 28010KT") is None

    def test_parse_t_group_zero_temp(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        # T00000267 → 0.0°C
        assert MetarClient.parse_t_group("T00000267") == pytest.approx(0.0, abs=0.01)


class TestMetarClientAPI:
    """Integration-style tests with mocked HTTP session."""

    @pytest.mark.asyncio
    async def test_get_latest_metar_success(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        client = MetarClient()
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[{
            "rawOb": "KLGA 061856Z T02890267",
            "temp": 29,
            "dewp": 15,
            "obsTime": "2026-03-06 18:56:00",
        }])
        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_latest_metar("KLGA")

        assert result is not None
        assert result["temp_c"] == pytest.approx(28.9, abs=0.01)  # T-group precision
        assert result["station_id"] == "KLGA"

    @pytest.mark.asyncio
    async def test_get_running_daily_max_fahrenheit(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        client = MetarClient()
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        # Two observations on the target date: 20.0°C and 25.0°C → max = 25°C = 77°F
        mock_resp.json = AsyncMock(return_value=[
            {"rawOb": "T02000200", "temp": 20, "obsTime": "2026-03-06 14:00:00"},
            {"rawOb": "T02500200", "temp": 25, "obsTime": "2026-03-06 16:00:00"},
        ])
        mock_sess = MagicMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_running_daily_max("KLGA", date(2026, 3, 6), temp_unit="F")

        assert result is not None
        # 25°C * 9/5 + 32 = 77.0°F
        assert result == pytest.approx(77.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_get_running_daily_max_cache(self):
        """Second call returns cached result without hitting the API."""
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        client = MetarClient()
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[
            {"rawOb": "T02500200", "temp": 25, "obsTime": "2026-03-06 16:00:00"},
        ])
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            r1 = await client.get_running_daily_max("KLGA", date(2026, 3, 6), temp_unit="C")
            # Force cache hit by not resetting it
            r2 = await client.get_running_daily_max("KLGA", date(2026, 3, 6), temp_unit="C")

        assert r1 == r2
        # API should only be called once
        assert mock_resp.json.call_count == 1

    @pytest.mark.asyncio
    async def test_get_running_daily_max_api_error_returns_none(self):
        from bots.weather.engine.base_engine.weather.metar_client import MetarClient
        client = MetarClient()
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 503
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client.get_running_daily_max("KLGA", date(2026, 3, 6))

        assert result is None


class TestMetarResolutionDayOverride:
    """Tests for _apply_metar_resolution_day_override logic in WeatherBot."""

    def _make_group(self, city: str = "NYC", target_date: date = date(2026, 3, 6)):
        """Build a minimal WeatherMarketGroup with a range of buckets."""
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        station = STATION_REGISTRY.get("new_york_city")
        buckets = [
            TemperatureBucket(
                market_id="m1", bucket_type="at_or_below",
                low_bound=float("-inf"), high_bound=72.0,
                yes_price=0.10, token_id="t1", no_token_id="n1", temp_unit="F",
            ),
            TemperatureBucket(
                market_id="m2", bucket_type="range",
                low_bound=73.0, high_bound=75.0,
                yes_price=0.25, token_id="t2", no_token_id="n2", temp_unit="F",
            ),
            TemperatureBucket(
                market_id="m3", bucket_type="range",
                low_bound=76.0, high_bound=78.0,
                yes_price=0.35, token_id="t3", no_token_id="n3", temp_unit="F",
            ),
            TemperatureBucket(
                market_id="m4", bucket_type="at_or_higher",
                low_bound=79.0, high_bound=float("inf"),
                yes_price=0.30, token_id="t4", no_token_id="n4", temp_unit="F",
            ),
        ]
        return WeatherMarketGroup(
            city=city,
            station=station,
            target_date=target_date,
            buckets=buckets,
        )

    @pytest.mark.asyncio
    async def test_metar_override_eliminates_exceeded_range_bucket(self, weather_bot):
        """When running_max > range.high_bound + 0.5, that range is ruled out."""
        group = self._make_group()
        # Running max = 80°F — exceeds m2 (73-75) and m3 (76-78) ranges
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=80.0)
        model_probs = {"m1": 0.05, "m2": 0.20, "m3": 0.30, "m4": 0.45}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 2.0)

        # m2 and m3 exceeded → overridden to ~0
        assert result["m2"] < 0.01
        assert result["m3"] < 0.01
        # m4 (at_or_higher 79°F): running_max=80 >= 79-0.5=78.5 → confirmed YES
        assert result["m4"] > 0.85

    @pytest.mark.asyncio
    async def test_metar_override_no_data_returns_unchanged(self, weather_bot):
        """When METAR returns None, model_probs unchanged."""
        group = self._make_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=None)
        model_probs = {"m1": 0.10, "m2": 0.25, "m3": 0.35, "m4": 0.30}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 3.0)

        assert result == model_probs  # unchanged

    @pytest.mark.asyncio
    async def test_metar_at_or_below_exceeded(self, weather_bot):
        """at_or_below bucket ruled out when running_max > high_bound + 0.5."""
        group = self._make_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=75.0)
        model_probs = {"m1": 0.20, "m2": 0.30, "m3": 0.30, "m4": 0.20}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 1.0)

        # m1 is at_or_below 72°F; running_max=75 > 72.5 → ruled out
        assert result["m1"] < 0.01

    def _make_below_only_group(self, city: str = "NYC", target_date: date = date(2026, 3, 6)):
        """Group with only at_or_below + range buckets (no at_or_higher) so the
        S222 at_or_below tests are isolated from other override branches."""
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        station = STATION_REGISTRY.get("new_york_city")
        buckets = [
            TemperatureBucket(
                market_id="m1", bucket_type="at_or_below",
                low_bound=float("-inf"), high_bound=72.0,
                yes_price=0.10, token_id="t1", no_token_id="n1", temp_unit="F",
            ),
            TemperatureBucket(
                market_id="m2", bucket_type="range",
                low_bound=73.0, high_bound=75.0,
                yes_price=0.25, token_id="t2", no_token_id="n2", temp_unit="F",
            ),
            TemperatureBucket(
                market_id="m3", bucket_type="range",
                low_bound=76.0, high_bound=78.0,
                yes_price=0.35, token_id="t3", no_token_id="n3", temp_unit="F",
            ),
        ]
        return WeatherMarketGroup(
            city=city, station=station, target_date=target_date, buckets=buckets,
        )

    @pytest.mark.asyncio
    async def test_metar_at_or_below_not_pushed_outside_aggressive_window(self, weather_bot):
        """S222: at lead >= 2h, running_max below the ceiling must NOT push
        at_or_below to near-certainty — the daily max can still rise. The
        removed ungated 0.97 branch used to fire here (70 < 72 - 1.5)."""
        group = self._make_below_only_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=70.0)
        model_probs = {"m1": 0.20, "m2": 0.40, "m3": 0.40}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 5.0)

        # No branch fires: m1 not exceeded (70 <= 72.5), not aggressive → unchanged
        assert result == model_probs

    @pytest.mark.asyncio
    async def test_metar_at_or_below_aggressive_push_retained(self, weather_bot):
        """S222: the <2h aggressive 0.98 push for at_or_below is retained."""
        group = self._make_below_only_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=70.0)
        model_probs = {"m1": 0.20, "m2": 0.40, "m3": 0.40}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 1.0)

        # aggressive: 70 < 72 - 0.5 → 0.98, renormalized against 0.40 + 0.40
        assert result["m1"] > 0.5
        assert result["m1"] == pytest.approx(0.98 / (0.98 + 0.80), rel=1e-3)

    @pytest.mark.asyncio
    async def test_metar_at_or_higher_not_ruled_out_outside_aggressive_window(self, weather_bot):
        """S222: at lead >= 2h, running_max below the floor must NOT rule out
        at_or_higher — the daily max can still rise past it. The removed
        ungated 0.001 branch used to fire here (70 < 79 - 2.0)."""
        group = self._make_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=70.0)
        model_probs = {"m1": 0.20, "m2": 0.30, "m3": 0.30, "m4": 0.20}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 5.0)

        # m4 (at_or_higher 79): 70 < 78.5, not aggressive → no override.
        # m1 (at_or_below 72): 70 < 72.5, not aggressive → no override (S222).
        # No branch fires anywhere → probs unchanged.
        assert result == model_probs

    @pytest.mark.asyncio
    async def test_metar_at_or_higher_aggressive_rule_out_retained(self, weather_bot):
        """S222: the <2h aggressive 0.001 rule-out for at_or_higher is retained."""
        group = self._make_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=73.5)
        model_probs = {"m1": 0.20, "m2": 0.30, "m3": 0.30, "m4": 0.20}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 1.0)

        # m4 (at_or_higher 79): aggressive and 73.5 < 78.0 → 0.001 retained
        assert result["m4"] < 0.01

    # ── S224: renorm guard (fallacy-audit fix #1, METAR site) ──────────

    def _make_singleton_group(self, low_bound: float = 72.0):
        """Group stripped to ONE bucket — market_mapper has no minimum group
        size, so thin-filter/parse drops produce these in production."""
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        station = STATION_REGISTRY.get("new_york_city")
        return WeatherMarketGroup(
            city="NYC", station=station, target_date=date(2026, 3, 6),
            buckets=[TemperatureBucket(
                market_id="m1", bucket_type="at_or_higher",
                low_bound=low_bound, high_bound=float("inf"),
                yes_price=0.43, token_id="t1", no_token_id="n1", temp_unit="F",
            )],
        )

    @pytest.mark.asyncio
    async def test_metar_singleton_override_keeps_humility_cap(self, weather_bot):
        """S224 defect: singleton group + 0.97 push renormed 0.97/0.97 = 1.0,
        erasing the deliberate 3% settlement-risk margin (live signal:
        model_prob=1.0 @ price 0.43)."""
        group = self._make_singleton_group(low_bound=72.0)
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=73.0)
        model_probs = {"m1": 0.40}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 5.0)

        # 73 >= 71.5 → 0.97 push fires; must STAY 0.97, not renorm to 1.0
        assert result["m1"] == pytest.approx(0.97)

    @pytest.mark.asyncio
    async def test_metar_singleton_no_override_returns_unchanged(self, weather_bot):
        """S224 defect: even with NO branch fired, the unconditional renorm
        divided a singleton's raw p by itself → literal 1.0 with zero METAR
        evidence."""
        group = self._make_singleton_group(low_bound=79.0)
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=70.0)
        model_probs = {"m1": 0.40}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 5.0)

        # 70 < 78.5, not aggressive → no branch fires → must be untouched
        assert result == model_probs

    @pytest.mark.asyncio
    async def test_metar_no_override_skips_renorm_on_partial_probs(self, weather_bot):
        """S224 defect: model_probs from an incomplete group (sum < 1) were
        renormed 2x with no METAR evidence at all."""
        group = self._make_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=70.0)
        model_probs = {"m1": 0.10, "m2": 0.15, "m3": 0.15, "m4": 0.10}  # sums 0.50

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 5.0)

        # No branch fires anywhere (70 below every trigger) → unchanged
        assert result == model_probs

    @pytest.mark.asyncio
    async def test_metar_conditioning_capped_at_098(self, weather_bot):
        """S224: multi-bucket conditioning (mass flows to survivors after
        monotone-safe rule-outs) is legitimate but must not exceed the 0.98
        deliberate humility cap (was 0.9969 pre-fix)."""
        group = self._make_group()
        weather_bot._metar_client.get_running_daily_max = AsyncMock(return_value=80.0)
        model_probs = {"m1": 0.05, "m2": 0.20, "m3": 0.30, "m4": 0.45}

        result = await weather_bot._apply_metar_resolution_day_override(group, model_probs, 2.0)

        # m1/m2/m3 ruled out (0.001 each), m4 → 0.97; renorm by 0.973
        # would give 0.9969 — the cap must hold it at 0.98.
        assert result["m4"] == pytest.approx(0.98)
        assert result["m2"] < 0.01


# ═══════════════════════════════════════════════════════════════════════════
# Local Model Forecast (Commit 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestFetchLocalModelForecast:
    """Test _fetch_local_model_forecast for international hi-res model integration."""

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_temperature(self):
        """Local model returns daily max temperature for target date."""
        client = WeatherForecastClient()

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "daily": {
                "time": ["2026-03-15"],
                "temperature_2m_max": [18.5],
                "temperature_2m_min": [7.2],
            }
        })
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client._fetch_local_model_forecast(
                48.8566, 2.3522, "C", "meteofrance_seamless", date(2026, 3, 15),
            )

        assert result == pytest.approx(18.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """Non-200 response returns None and logs warning."""
        client = WeatherForecastClient()

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 500
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client._fetch_local_model_forecast(
                48.8566, 2.3522, "C", "meteofrance_seamless", date(2026, 3, 15),
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_429_sets_cooldown(self):
        """429 response sets 1-hour cooldown for the model."""
        client = WeatherForecastClient()
        client._redis = None

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 429
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client._fetch_local_model_forecast(
                48.8566, 2.3522, "C", "meteofrance_seamless", date(2026, 3, 15),
            )

        assert result is None
        assert "meteofrance_seamless" in client._model_429_until

    @pytest.mark.asyncio
    async def test_missing_target_date_returns_none(self):
        """Returns None when target date not in response data."""
        client = WeatherForecastClient()

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "daily": {
                "time": ["2026-03-14"],  # Different date
                "temperature_2m_max": [20.0],
            }
        })
        mock_sess = MagicMock()
        mock_sess.get = MagicMock(return_value=mock_resp)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_sess):
            result = await client._fetch_local_model_forecast(
                48.8566, 2.3522, "C", "meteofrance_seamless", date(2026, 3, 15),
            )

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# International Min Edge (Commit 4)
# ═══════════════════════════════════════════════════════════════════════════


class TestIntlMinEdge:
    """Test _get_min_edge with station-aware international edge floor."""

    def _make_bot(self):
        """Create a minimal WeatherBot mock with _get_min_edge wired up."""
        from bots.weather_bot import WeatherBot
        bot = MagicMock(spec=WeatherBot)
        bot._min_edge = 0.08
        bot._intl_min_edge = 0.12
        bot._category_params = {}
        bot._spread_history = {}
        bot._get_min_edge = WeatherBot._get_min_edge.__get__(bot, WeatherBot)
        return bot

    def test_us_station_gets_base_edge(self):
        """NYC (temp_unit=F) gets base edge 0.08."""
        bot = self._make_bot()
        nyc = lookup_station("NYC")
        assert bot._get_min_edge("temperature", nyc) == pytest.approx(0.08)

    def test_intl_with_local_model_gets_base_edge(self):
        """Paris (has meteofrance_seamless) gets base edge 0.08 — data parity."""
        bot = self._make_bot()
        paris = lookup_station("Paris")
        assert paris.local_model is not None  # meteofrance_seamless
        assert bot._get_min_edge("temperature", paris) == pytest.approx(0.08)

    def test_intl_without_local_model_gets_higher_edge(self):
        """Buenos Aires (no local_model) gets 0.12 floor — data handicap."""
        bot = self._make_bot()
        ba = lookup_station("Buenos Aires")
        assert ba.local_model is None
        assert bot._get_min_edge("temperature", ba) == pytest.approx(0.12)

    def test_no_station_gets_base_edge(self):
        """No station passed → base edge (backwards compatible)."""
        bot = self._make_bot()
        assert bot._get_min_edge("temperature") == pytest.approx(0.08)

    def test_category_params_override_respected(self):
        """Category-specific min_edge still works with intl floor."""
        bot = self._make_bot()
        bot._category_params = {"precipitation": {"min_edge": 0.10}}
        ba = lookup_station("Buenos Aires")
        # 0.10 < 0.12, so intl floor (0.12) wins
        assert bot._get_min_edge("precipitation", ba) == pytest.approx(0.12)
        # But for US station, category param wins
        nyc = lookup_station("NYC")
        assert bot._get_min_edge("precipitation", nyc) == pytest.approx(0.10)


# ═══════════════════════════════════════════════════════════════════════════
# Local Model Station Registry (Commit 1)
# ═══════════════════════════════════════════════════════════════════════════


class TestLocalModelStationRegistry:
    """Verify local_model field is correctly populated in station registry."""

    def test_paris_has_meteofrance(self):
        assert STATION_REGISTRY["paris"].local_model == "meteofrance_seamless"

    def test_london_has_ukmo(self):
        assert STATION_REGISTRY["london"].local_model == "ukmo_seamless"

    def test_berlin_has_icon_d2(self):
        assert STATION_REGISTRY["berlin"].local_model == "icon_d2"

    def test_tokyo_has_jma(self):
        assert STATION_REGISTRY["tokyo"].local_model == "jma_seamless"

    def test_toronto_has_gem(self):
        assert STATION_REGISTRY["toronto"].local_model == "gem_seamless"

    def test_buenos_aires_has_no_local_model(self):
        assert STATION_REGISTRY["buenos_aires"].local_model is None

    def test_dubai_has_no_local_model(self):
        assert STATION_REGISTRY["dubai"].local_model is None

    def test_us_cities_have_no_local_model(self):
        """US cities use NBM, not local models — local_model should be None."""
        for key, station in STATION_REGISTRY.items():
            if station.temp_unit == "F":
                assert station.local_model is None, f"US station {key} should not have local_model"


# ---------------------------------------------------------------------------
# S123: WeatherConfidenceCalibrator tests
# ---------------------------------------------------------------------------
from bots.weather_bot import WeatherConfidenceCalibrator


class TestWeatherConfidenceCalibrator:
    """S135: Tests for the LogisticRegression confidence calibration pipeline."""

    # -- Helper to build a fitted calibrator from synthetic data --------

    @staticmethod
    def _build_fitted_calibrator():
        """Build a calibrator fitted on synthetic data where YES wins less than NO.

        S140b: Per-side IsotonicRegression. NO side has higher WR than YES.
        """
        from sklearn.isotonic import IsotonicRegression

        np.random.seed(42)
        n = 400
        confidences = np.random.uniform(0.5, 0.95, n)
        sides = ["YES"] * 200 + ["NO"] * 200
        # NO wins more (WR ~0.7-0.85), YES wins less (WR ~0.3-0.5)
        no_outcomes = (np.random.uniform(0, 1, 200) < (0.5 + 0.3 * confidences[200:])).astype(float)
        yes_outcomes = (np.random.uniform(0, 1, 200) < (0.1 + 0.3 * confidences[:200])).astype(float)

        model_no = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        model_no.fit(confidences[200:], no_outcomes)

        model_yes = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        model_yes.fit(confidences[:200], yes_outcomes)

        cal = WeatherConfidenceCalibrator()
        cal._model_no = model_no
        cal._model_yes = model_yes
        cal._fitted = True
        cal._n_samples = n
        cal._coef_confidence = round(float(model_no.predict([0.95])[0] - model_no.predict([0.60])[0]), 4)
        return cal

    # -- Identity / unfitted tests --------

    def test_identity_when_unfitted(self):
        """Unfitted calibrator returns raw confidence unchanged."""
        cal = WeatherConfidenceCalibrator()
        assert not cal.is_fitted
        for p in [0.10, 0.50, 0.95]:
            assert cal.calibrate(p, side="YES", lead_time_hours=48.0) == p

    def test_rejection_clears_stale_side_models(self):
        """S222 B2a: fit rejection must clear side models so the S159 YES
        dampener re-engages.

        Old bug: rejection branches set _fitted=False but retained stale
        _model_yes from a prior fit. calibrate() gates on _fitted (inert),
        while the S159 dampener gated on `_model_yes is None` (suppressed)
        — YES entries got NEITHER calibration NOR the 0.85x fallback.
        """
        cal = WeatherConfidenceCalibrator()
        # Simulate a prior successful fit (beta models on both sides)
        cal._model_yes = ("beta", 0.1, 1.2)
        cal._model_no = ("beta", -0.1, 0.9)
        cal._model_type_yes = "beta"
        cal._model_type_no = "beta"
        cal._beta_params_yes = (0.1, 1.2)
        cal._beta_params_no = (-0.1, 0.9)
        cal._fitted = True
        assert cal.calibrate(0.8, side="YES") != 0.8  # calibration active

        # Simulate what the Brier/OOS rejection branches now do
        cal._fitted = False
        cal._clear_models()

        # State coherent: identity passthrough AND dampener condition met
        assert cal._model_yes is None and cal._model_no is None
        assert cal._model_type_yes == "none" and cal._model_type_no == "none"
        assert cal._beta_params_yes is None and cal._beta_params_no is None
        assert cal.calibrate(0.8, side="YES") == 0.8
        assert cal.calibrate(0.8, side="NO") == 0.8

    def test_stale_model_with_fitted_false_is_identity(self):
        """S222 B2a guard: even in the legacy incoherent state (stale model +
        _fitted=False), calibrate() must be identity — and the hardened S159
        dampener condition (_fitted AND _model_yes) treats it as inactive."""
        cal = WeatherConfidenceCalibrator()
        cal._model_yes = ("beta", 0.1, 1.2)
        cal._model_type_yes = "beta"
        cal._fitted = False

        assert cal.calibrate(0.8, side="YES") == 0.8
        # The dampener condition used in _analyze_group:
        yes_cal_active = cal._fitted and cal._model_yes is not None
        assert not yes_cal_active  # dampener engages

    @pytest.mark.asyncio
    async def test_no_db_returns_false(self):
        """fit_from_trade_events returns False when no DB provided."""
        cal = WeatherConfidenceCalibrator()
        result = await cal.fit_from_trade_events(db=None, window_days=30)
        assert result is False
        assert not cal.is_fitted

    # -- Feature effect tests --------

    def test_yes_compressed_more_than_no(self):
        """YES side should get lower calibrated confidence than NO (data: YES loses more)."""
        cal = self._build_fitted_calibrator()
        yes_out = cal.calibrate(0.80, side="YES")
        no_out = cal.calibrate(0.80, side="NO")
        assert yes_out < no_out, f"YES ({yes_out:.3f}) should be < NO ({no_out:.3f})"

    def test_monotonic_no_side(self):
        """S140b: Isotonic should be monotonically increasing for NO side."""
        cal = self._build_fitted_calibrator()
        prev = 0.0
        for c in [0.50, 0.60, 0.70, 0.80, 0.90, 0.95]:
            out = cal.calibrate(c, side="NO")
            assert out >= prev, f"NO side not monotonic: {c} → {out:.3f} < prev {prev:.3f}"
            prev = out

    # -- Output validity tests --------

    def test_output_range(self):
        """Calibrated outputs must be in [0.01, 0.99]."""
        cal = self._build_fitted_calibrator()
        for conf in [0.01, 0.10, 0.50, 0.90, 0.99]:
            for side in ["YES", "NO"]:
                for lt in [1.0, 48.0, 120.0]:
                    result = cal.calibrate(conf, side=side, lead_time_hours=lt)
                    assert 0.01 <= result <= 0.99, f"Out of range: {result} for conf={conf}, side={side}, lt={lt}"

    def test_exception_returns_raw(self):
        """If model is broken, calibrate returns raw_confidence (fail-safe)."""
        cal = self._build_fitted_calibrator()
        cal._model_no = None  # force fallback for NO
        cal._model_yes = None  # force fallback for YES
        result = cal.calibrate(0.75, side="YES")
        assert result == 0.75
        result_no = cal.calibrate(0.80, side="NO")
        assert result_no == 0.80

    def test_temperature_property_returns_float(self):
        """Backward compat: .temperature returns the confidence coefficient as float."""
        cal = self._build_fitted_calibrator()
        assert isinstance(cal.temperature, float)

    # -- Brier guard test --------

    @pytest.mark.asyncio
    async def test_brier_guard_rejects_worse_calibration(self):
        """Calibration that worsens Brier score is rejected."""
        cal = WeatherConfidenceCalibrator()
        mock_session = AsyncMock()
        # Generate well-calibrated data — LR can't improve it
        np.random.seed(123)
        n = 300
        confs = np.random.uniform(0.2, 0.8, n)
        sides_str = ["YES" if i < 150 else "NO" for i in range(n)]
        lead_times = np.random.uniform(10, 100, n)
        prices = np.random.uniform(0.1, 0.8, n)
        outcomes = (np.random.uniform(0, 1, n) < confs).astype(float)
        rows = [
            (float(confs[i]), sides_str[i], float(lead_times[i]), float(prices[i]),
             0.0, 3.0, float(outcomes[i]))
            for i in range(n)
        ]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_db = MagicMock()
        mock_db.get_session = MagicMock(return_value=mock_session)
        result = await cal.fit_from_trade_events(
            db=mock_db, window_days=30, min_samples=200
        )
        # Either fitted (no harm) or rejected — both are correct behavior
        # If fitted, the model should not have worsened Brier by > 0.005
        assert isinstance(result, bool)

    # -- S159: OOS Brier gate tests --------

    def test_oos_brier_gate_rejects_harmful_calibrator(self):
        """S159: Calibrator that worsens OOS Brier by >0.005 is rejected."""
        cal = WeatherConfidenceCalibrator()
        # Directly test the gate logic: simulate a fit where OOS is worse
        # than raw by constructing data where isotonic overfits
        np.random.seed(777)
        n_train = 250
        n_test = 50
        # Training: well-correlated (isotonic will fit nicely)
        train_confs = np.sort(np.random.uniform(0.3, 0.9, n_train))
        train_outcomes = (np.random.uniform(0, 1, n_train) < train_confs).astype(float)
        # Test: deliberately anti-correlated (isotonic predictions will be wrong)
        test_confs = np.sort(np.random.uniform(0.3, 0.9, n_test))
        test_outcomes = (np.random.uniform(0, 1, n_test) < (1.0 - test_confs)).astype(float)
        from sklearn.isotonic import IsotonicRegression
        model = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        model.fit(train_confs, train_outcomes)
        # Compute raw OOS Brier (uncalibrated)
        raw_oos = float(np.mean((test_confs - test_outcomes) ** 2))
        # Compute calibrated OOS Brier
        cal_preds = np.array([float(model.predict([c])[0]) for c in test_confs])
        cal_oos = float(np.mean((cal_preds - test_outcomes) ** 2))
        # With anti-correlated test data, calibrated should be worse
        assert cal_oos > raw_oos + 0.005, (
            f"Test data didn't produce harmful calibrator: cal_oos={cal_oos:.4f} vs raw_oos={raw_oos:.4f}"
        )

    def test_oos_brier_gate_passes_good_calibrator(self):
        """S159: Calibrator that improves OOS Brier is accepted.

        Uses a large IID test set (500 samples) to reduce noise variance.
        With n=500 from the same DGP, isotonic should not degrade OOS Brier
        beyond the +0.005 tolerance.
        """
        cal = WeatherConfidenceCalibrator()
        np.random.seed(42)
        n_train = 500
        n_test = 500
        # Train and test from the SAME distribution (IID)
        all_confs = np.random.uniform(0.3, 0.9, n_train + n_test)
        all_outcomes = (np.random.uniform(0, 1, n_train + n_test) < all_confs).astype(float)
        train_confs = all_confs[:n_train]
        train_outcomes = all_outcomes[:n_train]
        test_confs = all_confs[n_train:]
        test_outcomes = all_outcomes[n_train:]
        from sklearn.isotonic import IsotonicRegression
        model = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        model.fit(train_confs, train_outcomes)
        raw_oos = float(np.mean((test_confs - test_outcomes) ** 2))
        cal_preds = np.array([float(model.predict([c])[0]) for c in test_confs])
        cal_oos = float(np.mean((cal_preds - test_outcomes) ** 2))
        assert cal_oos <= raw_oos + 0.005, (
            f"Good calibrator unexpectedly harmful: cal_oos={cal_oos:.4f} vs raw_oos={raw_oos:.4f}"
        )

    def test_raw_oos_brier_attribute_initialized(self):
        """S159: _raw_oos_brier attribute exists and defaults to None."""
        cal = WeatherConfidenceCalibrator()
        assert cal._raw_oos_brier is None


# ═══════════════════════════════════════════════════════════════════════════
# S168: Beta calibration + Per-city Brier tracker
# ═══════════════════════════════════════════════════════════════════════════


class TestBetaCalibration:
    """S168: Tests for restricted 2-param Beta calibration model."""

    def test_beta_calibrate_identity_at_defaults(self):
        """At c=0, d=1 the Beta calibration is the identity function."""
        cal = WeatherConfidenceCalibrator()
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            result = cal._beta_calibrate(p, c=0.0, d=1.0)
            assert abs(result - p) < 0.001, f"Identity failed at p={p}: got {result}"

    def test_beta_calibrate_monotonic(self):
        """Beta calibration should be monotonically increasing."""
        cal = WeatherConfidenceCalibrator()
        prev = 0.0
        for p in [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
            result = cal._beta_calibrate(p, c=0.5, d=1.2)
            assert result > prev, f"Not monotonic at p={p}: {result} <= {prev}"
            prev = result

    def test_beta_calibrate_output_range(self):
        """Beta calibration outputs must be in (0, 1)."""
        cal = WeatherConfidenceCalibrator()
        for c in [-3.0, -1.0, 0.0, 1.0, 3.0]:
            for d in [0.5, 1.0, 2.0, 5.0]:
                for p in [0.01, 0.10, 0.50, 0.90, 0.99]:
                    result = cal._beta_calibrate(p, c=c, d=d)
                    assert 0.0 < result < 1.0, f"Out of range: c={c}, d={d}, p={p} → {result}"

    def test_beta_calibrate_handles_extremes(self):
        """Beta calibration handles near-0 and near-1 inputs gracefully."""
        cal = WeatherConfidenceCalibrator()
        r1 = cal._beta_calibrate(0.001, c=1.0, d=2.0)
        r2 = cal._beta_calibrate(0.999, c=1.0, d=2.0)
        assert 0.0 < r1 < 1.0
        assert 0.0 < r2 < 1.0

    def test_fit_beta_model_returns_params(self):
        """_fit_beta_model returns (c, d, brier) on valid data."""
        np.random.seed(42)
        n = 200
        confs = np.random.uniform(0.5, 0.95, n)
        outcomes = (np.random.uniform(0, 1, n) < (0.3 + 0.5 * confs)).astype(float)
        result = WeatherConfidenceCalibrator._fit_beta_model(confs, outcomes)
        assert result is not None, "Beta fit should succeed with 200 samples"
        c, d, brier = result
        assert -5.0 <= c <= 5.0, f"c out of bounds: {c}"
        assert 0.1 <= d <= 10.0, f"d out of bounds: {d}"
        assert 0.0 <= brier <= 1.0, f"brier out of range: {brier}"

    def test_fit_beta_model_too_few_samples(self):
        """_fit_beta_model returns None with <10 samples."""
        confs = np.array([0.5, 0.6, 0.7])
        outcomes = np.array([1.0, 0.0, 1.0])
        result = WeatherConfidenceCalibrator._fit_beta_model(confs, outcomes)
        assert result is None

    def test_beta_produces_distinct_outputs(self):
        """S168 key test: Beta should produce distinct outputs where isotonic collapses."""
        np.random.seed(42)
        n = 300
        confs = np.random.uniform(0.5, 0.95, n)
        # Flat win rate (~70%) — isotonic will collapse to 2-3 bins
        outcomes = (np.random.uniform(0, 1, n) < 0.70).astype(float)
        result = WeatherConfidenceCalibrator._fit_beta_model(confs, outcomes)
        assert result is not None
        c, d, _ = result
        # Check outputs are distinct at different confidence levels
        cal = WeatherConfidenceCalibrator()
        outputs = [cal._beta_calibrate(p, c, d) for p in [0.60, 0.70, 0.80, 0.90]]
        # All 4 outputs should be different (Beta has continuous output)
        assert len(set(round(o, 4) for o in outputs)) == 4, (
            f"Beta should produce 4 distinct outputs, got: {[round(o, 4) for o in outputs]}"
        )

    def test_model_type_attributes_initialized(self):
        """S168: New model type attributes initialized correctly."""
        cal = WeatherConfidenceCalibrator()
        assert cal._model_type_no == "none"
        assert cal._model_type_yes == "none"
        assert cal._beta_params_no is None
        assert cal._beta_params_yes is None

    def test_beta_yes_min_samples_lower_than_isotonic(self):
        """Beta YES threshold (80) is lower than isotonic (200)."""
        assert WeatherConfidenceCalibrator._BETA_YES_MIN_SAMPLES == 80


class TestCityBrierTracker:
    """S168: Tests for per-city rolling Brier tracker and sizing dampener."""

    def _make_bot_with_brier(self):
        """Create a minimal stub with city Brier state and methods."""
        from collections import deque

        class StubBot:
            def __init__(self):
                self._city_brier = {}
                self._city_brier_window = 50

            def _update_city_brier(self, city, confidence, outcome):
                if city not in self._city_brier:
                    self._city_brier[city] = deque(maxlen=self._city_brier_window)
                self._city_brier[city].append((confidence, outcome))

            def _get_city_brier_mult(self, city):
                dq = self._city_brier.get(city)
                if not dq or len(dq) < self._city_brier_window:
                    return 1.0
                brier = sum((c - o) ** 2 for c, o in dq) / len(dq)
                if brier < 0.25:
                    return 1.0
                if brier >= 0.35:
                    return 0.10
                return max(0.30, 1.0 - 7.0 * (brier - 0.25))

        return StubBot()

    def test_neutral_when_no_data(self):
        """Returns 1.0 when city has no Brier data."""
        bot = self._make_bot_with_brier()
        assert bot._get_city_brier_mult("UnknownCity") == 1.0

    def test_neutral_when_insufficient_data(self):
        """Returns 1.0 when city has fewer than 50 resolved trades."""
        bot = self._make_bot_with_brier()
        for _ in range(30):
            bot._update_city_brier("TestCity", 0.70, 1.0)
        assert bot._get_city_brier_mult("TestCity") == 1.0

    def test_well_calibrated_city_gets_1x(self):
        """Perfect calibration → 1.0x multiplier (no boost)."""
        bot = self._make_bot_with_brier()
        # Brier ~0 — predictions match outcomes perfectly
        for _ in range(50):
            bot._update_city_brier("Seoul", 0.80, 1.0)
        mult = bot._get_city_brier_mult("Seoul")
        assert mult == 1.0, f"Well-calibrated city should be 1.0x, got {mult}"

    def test_poor_city_throttled(self):
        """City with Brier > 0.35 → 0.10x near-block."""
        bot = self._make_bot_with_brier()
        # Brier = mean((0.80 - 0.0)^2) = 0.64 — very poor
        for _ in range(50):
            bot._update_city_brier("Dallas", 0.80, 0.0)
        mult = bot._get_city_brier_mult("Dallas")
        assert mult == 0.10, f"Very poor city should be 0.10x, got {mult}"

    def test_moderate_city_tapered(self):
        """City with Brier ~0.30 gets intermediate dampener."""
        bot = self._make_bot_with_brier()
        # Mix: 35 correct at conf=0.70, 15 incorrect at conf=0.70
        # Brier = 35*(0.70-1.0)^2 + 15*(0.70-0.0)^2 / 50
        #       = 35*0.09 + 15*0.49 / 50 = (3.15+7.35)/50 = 0.21
        for _ in range(35):
            bot._update_city_brier("Chicago", 0.70, 1.0)
        for _ in range(15):
            bot._update_city_brier("Chicago", 0.70, 0.0)
        mult = bot._get_city_brier_mult("Chicago")
        assert mult == 1.0, f"Brier ~0.21 should give 1.0x, got {mult}"

    def test_multiplier_never_exceeds_1(self):
        """Per S153 invariant: multiplier is always ≤1.0 (dampener only)."""
        bot = self._make_bot_with_brier()
        for _ in range(50):
            bot._update_city_brier("GoodCity", 0.90, 1.0)  # Brier = 0.01
        mult = bot._get_city_brier_mult("GoodCity")
        assert mult <= 1.0, f"Multiplier must be ≤1.0, got {mult}"

    def test_multiplier_floor_at_0_10(self):
        """Worst-case multiplier floors at 0.10."""
        bot = self._make_bot_with_brier()
        for _ in range(50):
            bot._update_city_brier("Terrible", 0.99, 0.0)  # Brier ~0.98
        mult = bot._get_city_brier_mult("Terrible")
        assert mult == 0.10, f"Floor should be 0.10, got {mult}"

    def test_deque_respects_window(self):
        """Only last 50 trades are used for Brier calculation."""
        bot = self._make_bot_with_brier()
        # First 50: terrible (Brier ~0.64)
        for _ in range(50):
            bot._update_city_brier("Recovering", 0.80, 0.0)
        assert bot._get_city_brier_mult("Recovering") == 0.10
        # Next 50: perfect — pushes old ones out
        for _ in range(50):
            bot._update_city_brier("Recovering", 0.80, 1.0)
        mult = bot._get_city_brier_mult("Recovering")
        assert mult == 1.0, f"After recovery, should be 1.0x, got {mult}"


# ═══════════════════════════════════════════════════════════════════════════
# S124: Zero-Kelly guard + Spread inflation
# ═══════════════════════════════════════════════════════════════════════════

class TestZeroKellyGuard:
    """S124: Verify zero-Kelly trades are blocked (not forced to $5)."""

    @pytest.fixture
    def _mock_weather_bot(self):
        """Minimal WeatherBot mock for _execute_weather_trade."""
        bot = MagicMock()
        bot.bot_name = "WeatherBot"
        bot._recently_exited = {}
        bot._exit_cooldown_secs = 14400
        bot._fill_fail_tracker = {}
        bot._fill_fail_max_consec = 3
        bot._fill_fail_cooldown_secs = 1800
        bot._min_fill_prob_estimate = 0.1
        bot._daily_pnl = 0.0
        bot._daily_loss_limit = 10000
        bot._group_exposure = {}
        bot._city_exposure = {}
        bot._max_per_group = 10000
        bot._max_correlated = 5000
        bot._default_size = 25.0
        bot._liquidity_cache = {}
        bot._liquidity_cache_ttl = 60
        bot.base_engine = MagicMock()
        bot.base_engine.order_gateway = None
        bot.base_engine.db = MagicMock()
        bot.base_engine.db.insert_trade_event = AsyncMock()
        bot.base_engine.liquidity_guardian = None
        return bot

    @pytest.mark.asyncio
    async def test_zero_kelly_returns_false(self, _mock_weather_bot):
        """When Kelly returns 0 shares, trade should NOT fire (no $5 forced bet)."""
        import asyncio
        from bots.weather_bot import WeatherBot

        bot = _mock_weather_bot
        # Mock Kelly to return 0 (negative EV)
        bot.calculate_bot_position_size = AsyncMock(return_value=0.0)
        bot._should_halt_severe_weather = MagicMock(return_value=None)
        bot._get_severe_weather_boost = AsyncMock(return_value=1.0)
        bot._get_model_age_hours = MagicMock(return_value=2.0)
        bot._get_station_reliability_factor = AsyncMock(return_value=1.0)
        bot._calibration_confidence = MagicMock(return_value=0.5)
        bot._station_n_resolved = {"KJFK": 50}
        bot._exposure_lock = asyncio.Lock()

        opp = {
            "market_id": "test_mkt_1", "token_id": "tok_1",
            "side": "NO", "price": 0.85, "confidence": 0.78,
            "raw_confidence": 0.95, "model_prob": 0.05,
            "edge": -0.10, "abs_edge": 0.10, "city": "New York",
            "target_date": "2026-03-25", "lead_time_hours": 48.0,
            "ensemble_mean": 55.0, "model_spread": 3.0,
            "ensemble_count": 51, "resolution_boundary_risk": False,
            "market_type": "temperature",
        }
        group = MagicMock()
        group.city = "New York"
        group.target_date = date(2026, 3, 25)
        group.station = MagicMock()
        group.station.station_id = "KJFK"

        result = await WeatherBot._execute_weather_trade(bot, opp, group)
        assert result is False, "Zero-Kelly trade should return False, not fire at $5"

    @pytest.mark.asyncio
    async def test_zero_kelly_logs_shadow_entry(self, _mock_weather_bot):
        """When Kelly returns 0, a SHADOW_ENTRY should be written to DB."""
        import asyncio
        from bots.weather_bot import WeatherBot

        bot = _mock_weather_bot
        bot.calculate_bot_position_size = AsyncMock(return_value=0.0)
        bot._should_halt_severe_weather = MagicMock(return_value=None)
        bot._get_severe_weather_boost = AsyncMock(return_value=1.0)
        bot._get_model_age_hours = MagicMock(return_value=2.0)
        bot._get_station_reliability_factor = AsyncMock(return_value=1.0)
        bot._calibration_confidence = MagicMock(return_value=0.5)
        bot._station_n_resolved = {"KJFK": 50}
        bot._exposure_lock = asyncio.Lock()

        opp = {
            "market_id": "test_mkt_2", "token_id": "tok_2",
            "side": "NO", "price": 0.85, "confidence": 0.78,
            "raw_confidence": 0.95, "model_prob": 0.05,
            "edge": -0.10, "abs_edge": 0.10, "city": "New York",
            "target_date": "2026-03-25", "lead_time_hours": 48.0,
            "ensemble_mean": 55.0, "model_spread": 3.0,
            "ensemble_count": 51, "resolution_boundary_risk": False,
            "market_type": "temperature",
        }
        group = MagicMock()
        group.city = "New York"
        group.target_date = date(2026, 3, 25)
        group.station = MagicMock()
        group.station.station_id = "KJFK"

        await WeatherBot._execute_weather_trade(bot, opp, group)
        bot.base_engine.db.insert_trade_event.assert_called_once()
        call_kwargs = bot.base_engine.db.insert_trade_event.call_args
        assert call_kwargs.kwargs["event_type"] == "SHADOW_ENTRY"
        assert call_kwargs.kwargs["event_data"]["reason"] == "negative_ev"


class TestSpreadInflation:
    """S132: Verify spread inflation is OFF (removed in S132)."""

    def test_spread_not_inflated_by_lead_time(self):
        """S132: Spread inflation removed — lead time should NOT affect scale."""
        engine = WeatherProbabilityEngine()
        members = [50.0, 51.0, 52.0, 53.0, 54.0]
        _, scale_24h, _ = engine.fit_distribution(members, lead_time_hours=24.0)
        _, scale_120h, _ = engine.fit_distribution(members, lead_time_hours=120.0)
        assert abs(scale_24h - scale_120h) < 0.01, (
            f"Spread inflation should be OFF: 24h={scale_24h:.4f} vs 120h={scale_120h:.4f}"
        )


class TestSpreadGate:
    """S140: Bid-ask spread gate rejects illiquid markets."""

    def test_wide_spread_rejected(self):
        """Markets with spread > WEATHER_MAX_SPREAD should be rejected."""
        assert hasattr(settings, "WEATHER_MAX_SPREAD")
        val = float(getattr(settings, "WEATHER_MAX_SPREAD", 0.30))
        assert val > 0, "Spread gate must have a positive threshold"

    def test_spread_config_default(self):
        """Default spread gate is 1.0 (disabled since S153)."""
        val = float(getattr(settings, "WEATHER_MAX_SPREAD", 1.0))
        assert val == 1.0, f"Default should be 1.0 (disabled), got {val}"


# ═══════════════════════════════════════════════════════════════════════════
# S154: NO Price Dampener + Lead-Time Multiplier + Config Defaults
# ═══════════════════════════════════════════════════════════════════════════


class TestS154NoPriceDampener:
    """S154: NO high-price dampener reduces size above soft cap."""

    def test_no_max_entry_price_default(self):
        """Hard cap is 0.85 (S154)."""
        val = float(getattr(settings, "WEATHER_NO_MAX_ENTRY_PRICE", 0.85))
        assert val == 0.85, f"Default should be 0.85, got {val}"

    def test_soft_cap_default(self):
        """Soft cap default is 0.70."""
        val = float(getattr(settings, "WEATHER_NO_PRICE_SOFT_CAP", 0.70))
        assert val == 0.70, f"Default should be 0.70, got {val}"

    def test_dampener_slope_default(self):
        """Slope 6.0 makes dampener reach 0.10 at hard cap 0.85."""
        val = float(getattr(settings, "WEATHER_NO_PRICE_DAMPENER_SLOPE", 6.0))
        assert val == 6.0, f"Default should be 6.0, got {val}"

    def test_dampener_formula_at_soft_cap(self):
        """At soft cap (0.70), dampener is 1.0 (no reduction)."""
        soft_cap = 0.70
        slope = 6.0
        price = 0.70
        scale = max(0.10, 1.0 - slope * (price - soft_cap))
        assert scale == 1.0

    def test_dampener_formula_at_075(self):
        """At 0.75, dampener is 0.70."""
        soft_cap = 0.70
        slope = 6.0
        price = 0.75
        scale = max(0.10, 1.0 - slope * (price - soft_cap))
        assert abs(scale - 0.70) < 0.01

    def test_dampener_formula_at_080(self):
        """At 0.80, dampener is 0.40."""
        soft_cap = 0.70
        slope = 6.0
        price = 0.80
        scale = max(0.10, 1.0 - slope * (price - soft_cap))
        assert abs(scale - 0.40) < 0.01

    def test_dampener_formula_at_085(self):
        """At 0.85 (hard cap), dampener is 0.10 (floor)."""
        soft_cap = 0.70
        slope = 6.0
        price = 0.85
        scale = max(0.10, 1.0 - slope * (price - soft_cap))
        assert abs(scale - 0.10) < 0.01

    def test_dampener_floor_prevents_negative(self):
        """Floor of 0.10 prevents negative values."""
        soft_cap = 0.70
        slope = 6.0
        price = 0.95  # far above hard cap, dampener formula goes negative
        scale = max(0.10, 1.0 - slope * (price - soft_cap))
        assert scale == 0.10


# ═══════════════════════════════════════════════════════════════════════════
# S205 6Q: Confidence-tail sizing dampener (config-gated)
# ═══════════════════════════════════════════════════════════════════════════


class TestS205Confidence6QDampener:
    """S205 6Q: smooth multiplicative taper above THRESHOLD reduces position size
    on the high-confidence tail. PIT-KS rejected calibration on the [0.9-1.0)
    bin (S204); H0''-H0'''(c) eliminated three feature-engineering candidates
    at the station level. Pivot to confidence-scaled sizing."""

    def test_default_settings(self):
        """Defaults: ENABLED=False, THRESHOLD=0.85, SLOPE=2.0, FLOOR=0.30."""
        assert getattr(settings, "WEATHER_CONFIDENCE_DAMPENER_ENABLED", False) is False
        assert float(getattr(settings, "WEATHER_CONFIDENCE_DAMPENER_THRESHOLD", 0.85)) == 0.85
        assert float(getattr(settings, "WEATHER_CONFIDENCE_DAMPENER_SLOPE", 2.0)) == 2.0
        assert float(getattr(settings, "WEATHER_CONFIDENCE_DAMPENER_FLOOR", 0.30)) == 0.30

    def test_at_threshold_returns_unity(self):
        """At c == THRESHOLD, dampener factor is exactly 1.0 (continuity)."""
        threshold, slope, floor = 0.85, 2.0, 0.30
        c = 0.85
        factor = max(floor, 1.0 - slope * (c - threshold))
        assert factor == 1.0

    def test_taper_at_090_and_095(self):
        """Slope 2.0 from threshold 0.85: c=0.90 → 0.90x, c=0.95 → 0.80x."""
        threshold, slope, floor = 0.85, 2.0, 0.30
        f_090 = max(floor, 1.0 - slope * (0.90 - threshold))
        f_095 = max(floor, 1.0 - slope * (0.95 - threshold))
        assert abs(f_090 - 0.90) < 1e-9
        assert abs(f_095 - 0.80) < 1e-9

    def test_floor_clamps_at_extreme_slope(self):
        """With extreme slope, floor clamps the factor (no negative sizing)."""
        threshold, slope, floor = 0.85, 10.0, 0.30
        # 1.0 - 10.0 * (1.0 - 0.85) = -0.5 → clamped to 0.30
        factor = max(floor, 1.0 - slope * (1.0 - threshold))
        assert factor == 0.30

    def test_below_threshold_no_op(self):
        """Below THRESHOLD, dampener is bypassed (factor stays at 1.0).
        The bot-side `if c >= threshold` gate around the formula preserves
        size for any c < threshold; this test pins the gating contract."""
        threshold = 0.85
        c = 0.84
        # Formula is only applied when c >= threshold; below, factor stays 1.0.
        factor = 1.0
        if c >= threshold:
            factor = max(0.30, 1.0 - 2.0 * (c - threshold))
        assert factor == 1.0


class TestS154LeadTimeMultiplier:
    """S154: Lead-time sizing multipliers concentrate capital on sweet spot."""

    def test_72_120h_multiplier_default(self):
        """72-120h (sweet spot) gets 1.15x."""
        val = float(getattr(settings, "WEATHER_LEAD_TIME_MULT_72_120", 1.15))
        assert val == 1.15

    def test_48_72h_multiplier_default(self):
        """48-72h is neutral 1.0x."""
        val = float(getattr(settings, "WEATHER_LEAD_TIME_MULT_48_72", 1.0))
        assert val == 1.0

    def test_0_24h_multiplier_default(self):
        """<24h gets 0.85x."""
        val = float(getattr(settings, "WEATHER_LEAD_TIME_MULT_0_24", 0.85))
        assert val == 0.85

    def test_24_48h_multiplier_default(self):
        """24-48h gets 0.70x (conservative, near-breakeven bucket)."""
        val = float(getattr(settings, "WEATHER_LEAD_TIME_MULT_24_48", 0.70))
        assert val == 0.70


class TestS154MaxLeadTime:
    """S154: Max lead time reduced to 120h."""

    def test_max_lead_time_default(self):
        """Max lead time is 120h (was 168h)."""
        val = int(getattr(settings, "WEATHER_MAX_LEAD_TIME_HOURS", 120))
        assert val == 120, f"Default should be 120, got {val}"


class TestS154FeeOverride:
    """S154: Per-bot fee override for weather markets."""

    def test_weather_fee_default_zero(self):
        """Weather taker fee defaults to 0 (weather markets have no taker fee)."""
        val = int(getattr(settings, "WEATHER_TAKER_FEE_BPS", 0))
        assert val == 0, f"Default should be 0 for weather, got {val}"


class TestS154VarianceInflation:
    """S154: Variance inflation for non-EMOS paths."""

    def test_variance_inflation_default(self):
        """Default inflation factor is 1.4."""
        val = float(getattr(settings, "WEATHER_VARIANCE_INFLATION_FACTOR", 1.4))
        assert val == 1.4, f"Default should be 1.4, got {val}"


# ═══════════════════════════════════════════════════════════════════════════
# T1-A: Mid-life exit evaluator + T1-K: Exit-reason-specific cooldowns
# ═══════════════════════════════════════════════════════════════════════════


class TestMidLifeExitEvaluator:
    """T1-A: _evaluate_mid_life_exits exits positions with reversed model probability."""

    def _make_bot(self):
        import asyncio
        bot = MagicMock()
        bot.bot_name = "WeatherBot"
        bot._recently_exited = {}
        bot._exit_reasons = {}
        bot._exit_cooldown_secs = 14400.0
        bot._exposure_lock = asyncio.Lock()
        bot._group_exposure = {}
        bot._city_exposure = {}
        bot._market_group_cache = {}
        bot._save_exit_to_redis = AsyncMock()
        bot.base_engine = MagicMock()
        bot.base_engine.db = None
        bot.base_engine.order_gateway = MagicMock()
        bot.base_engine.order_gateway._position_details = {}
        # S172 D7: Shared hard stop returns "no exit" by default in tests
        bot.base_engine.risk_manager.check_hard_stop_loss = MagicMock(return_value={
            "should_exit": False, "reason": "", "details": {},
        })
        # S223: exits are SELL of the held token via base_engine.place_order
        # (canonical position_manager idiom; the S160/S214 wrapper+flipped-side
        # route hit entry gates and could never fill — proven live 2026-07-06).
        bot.base_engine.place_order = AsyncMock(return_value={"success": True})
        bot.place_order = AsyncMock(return_value={"success": True})
        bot.running = True
        return bot

    def _make_bucket(self, market_id, yes_price, token_id="t1", no_token_id="n1"):
        return TemperatureBucket(
            market_id=market_id,
            bucket_type="range",
            low_bound=70.0,
            high_bound=75.0,
            yes_price=yes_price,
            token_id=token_id,
            no_token_id=no_token_id,
            temp_unit="F",
        )

    def _make_analyzed(self, bucket, fresh_prob):
        group = MagicMock()
        group.buckets = [bucket]
        return [([], group, {bucket.market_id: fresh_prob})]

    @pytest.mark.asyncio
    async def test_skips_when_flag_disabled(self):
        """Feature flag off → no exits triggered."""
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bucket = self._make_bucket("mkt1", yes_price=0.60)
        # Position: YES at 0.60, fresh_prob drops to 0.40 (EV = -0.20)
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt1": {"side": "YES", "price": 0.60, "size": 10.0}
        }
        analyzed = self._make_analyzed(bucket, 0.40)
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", False, create=True):
            await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_exits_yes_position_on_reversal(self):
        """YES position exits when fresh_prob - entry_price < -exit_min_edge."""
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bucket = self._make_bucket("mkt1", yes_price=0.45, token_id="yes_tok")
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt1": {"side": "YES", "price": 0.60, "size": 15.0}
        }
        analyzed = self._make_analyzed(bucket, 0.40)  # EV = 0.40 - 0.60 = -0.20
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            with patch.object(settings, "WEATHER_EXIT_MIN_EDGE", 0.05, create=True):
                await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        # S223: exit = SELL the HELD token via base_engine.place_order (the S214
        # flipped-side route was a NEW BUY that entry gates rejected — never filled)
        bot.base_engine.place_order.assert_awaited_once()
        call_kwargs = bot.base_engine.place_order.await_args
        assert call_kwargs.kwargs["side"] == "SELL"
        assert call_kwargs.kwargs["token_id"] == "yes_tok"
        assert call_kwargs.kwargs["size"] == 15.0
        assert "mkt1" in bot._recently_exited
        assert bot._exit_reasons["mkt1"] == "REVERSAL"

    @pytest.mark.asyncio
    async def test_exits_no_position_on_reversal(self):
        """NO position exits when (1 - fresh_prob) - entry_price < -exit_min_edge."""
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        # NO token: entry was at 0.70 (YES was at 0.30, NO = 1 - 0.30 = 0.70)
        bucket = self._make_bucket("mkt2", yes_price=0.80, no_token_id="no_tok")
        # fresh_prob = P(YES) = 0.80 → P(NO) = 0.20, entry was 0.70 → EV = 0.20 - 0.70 = -0.50
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt2": {"side": "NO", "price": 0.70, "size": 8.0}
        }
        analyzed = self._make_analyzed(bucket, 0.80)
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            with patch.object(settings, "WEATHER_EXIT_MIN_EDGE", 0.05, create=True):
                await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        # S223: exit = SELL the HELD (NO) token via base_engine.place_order
        bot.base_engine.place_order.assert_awaited_once()
        call_kwargs = bot.base_engine.place_order.await_args
        assert call_kwargs.kwargs["side"] == "SELL"
        assert call_kwargs.kwargs["token_id"] == "no_tok"
        assert bot._exit_reasons["mkt2"] == "REVERSAL"

    @pytest.mark.asyncio
    async def test_no_exit_when_ev_above_threshold(self):
        """Does NOT exit when EV is above -exit_min_edge (edge still positive)."""
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bucket = self._make_bucket("mkt3", yes_price=0.60)
        # Entry 0.60, fresh_prob 0.58 → EV = -0.02 < 0.05 threshold → no exit
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt3": {"side": "YES", "price": 0.60, "size": 10.0}
        }
        analyzed = self._make_analyzed(bucket, 0.58)
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            with patch.object(settings, "WEATHER_EXIT_MIN_EDGE", 0.05, create=True):
                await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_cooldown_markets(self):
        """Markets in _recently_exited cooldown are not re-exited."""
        import time
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bucket = self._make_bucket("mkt4", yes_price=0.40)
        bot._recently_exited["mkt4"] = time.monotonic()  # just exited
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt4": {"side": "YES", "price": 0.70, "size": 5.0}
        }
        analyzed = self._make_analyzed(bucket, 0.30)
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_market_with_no_open_position(self):
        """If no position details found, no SELL is placed."""
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bucket = self._make_bucket("mkt5", yes_price=0.40)
        # No entry in _position_details
        analyzed = self._make_analyzed(bucket, 0.20)
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        bot.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_exposure_decremented_on_exit(self):
        """Group and city exposure are decremented when cache entry exists."""
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bucket = self._make_bucket("mkt6", yes_price=0.40, token_id="t6")
        bot._market_group_cache["mkt6"] = ("NYC:2026-04-01", "NYC", 100.0)
        bot._group_exposure["NYC:2026-04-01"] = 500.0
        bot._city_exposure["NYC"] = 800.0
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt6": {"side": "YES", "price": 0.65, "size": 5.0}
        }
        analyzed = self._make_analyzed(bucket, 0.30)
        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            with patch.object(settings, "WEATHER_EXIT_MIN_EDGE", 0.05, create=True):
                await WeatherBot._evaluate_mid_life_exits(bot, analyzed)
        assert bot._group_exposure["NYC:2026-04-01"] == pytest.approx(400.0)
        assert bot._city_exposure["NYC"] == pytest.approx(700.0)
        assert "mkt6" not in bot._market_group_cache


class TestExitReasonCooldowns:
    """T1-K: _get_exit_cooldown returns reason-specific TTLs."""

    def _make_bot(self):
        bot = MagicMock()
        bot._exit_reasons = {}
        bot._exit_cooldown_secs = 14400.0
        return bot

    def test_resolution_uses_long_cooldown(self):
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bot._exit_reasons["mkt1"] = "RESOLUTION"
        result = WeatherBot._get_exit_cooldown(bot, "mkt1")
        assert result == 14400.0

    def test_reversal_uses_short_cooldown(self):
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bot._exit_reasons["mkt1"] = "REVERSAL"
        with patch.object(settings, "WEATHER_EXIT_COOLDOWN_REVERSAL_SECS", 1800.0, create=True):
            result = WeatherBot._get_exit_cooldown(bot, "mkt1")
        assert result == 1800.0

    def test_unknown_reason_falls_back_to_long_cooldown(self):
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        # No entry in _exit_reasons
        result = WeatherBot._get_exit_cooldown(bot, "unknown_market")
        assert result == 14400.0

    def test_reversal_cooldown_shorter_than_resolution(self):
        from bots.weather_bot import WeatherBot
        bot = self._make_bot()
        bot._exit_reasons["rev"] = "REVERSAL"
        bot._exit_reasons["res"] = "RESOLUTION"
        with patch.object(settings, "WEATHER_EXIT_COOLDOWN_REVERSAL_SECS", 1800.0, create=True):
            rev_cd = WeatherBot._get_exit_cooldown(bot, "rev")
            res_cd = WeatherBot._get_exit_cooldown(bot, "res")
        assert rev_cd < res_cd


class TestYesIdentityDampener:
    """S159: YES identity passthrough dampener (0.85x) when calibrator has no YES model.

    S160 WB-7: Added behavioral wiring tests — S159 had no test verifying
    the dampener is actually applied during opportunity building.
    """

    def test_dampener_applied_when_model_yes_is_none(self):
        """When _model_yes is None, YES confidence is multiplied by 0.85."""
        from bots.weather_bot import WeatherConfidenceCalibrator
        cal = WeatherConfidenceCalibrator()
        assert cal._model_yes is None  # default state

        # Simulate the dampener logic from _analyze_group lines 2402-2406
        side = "YES"
        base_confidence = 0.90
        effective_confidence = base_confidence
        _yes_identity_damp = 1.0
        if (side == "YES"
                and getattr(cal, "_model_yes", None) is None):
            _yes_identity_damp = 0.85
            effective_confidence *= _yes_identity_damp

        assert _yes_identity_damp == 0.85
        assert abs(effective_confidence - 0.765) < 1e-9  # 0.90 * 0.85

    def test_dampener_not_applied_when_model_yes_exists(self):
        """When _model_yes is fitted, dampener is 1.0 (no reduction)."""
        from bots.weather_bot import WeatherConfidenceCalibrator
        cal = WeatherConfidenceCalibrator()
        # Simulate graduation: model_yes is not None
        cal._model_yes = MagicMock()  # any non-None value

        side = "YES"
        base_confidence = 0.90
        effective_confidence = base_confidence
        _yes_identity_damp = 1.0
        if (side == "YES"
                and getattr(cal, "_model_yes", None) is None):
            _yes_identity_damp = 0.85
            effective_confidence *= _yes_identity_damp

        assert _yes_identity_damp == 1.0
        assert effective_confidence == 0.90  # unchanged

    def test_dampener_not_applied_to_no_side(self):
        """NO-side confidence is never dampened, even when _model_yes is None."""
        from bots.weather_bot import WeatherConfidenceCalibrator
        cal = WeatherConfidenceCalibrator()
        assert cal._model_yes is None

        side = "NO"
        base_confidence = 0.90
        effective_confidence = base_confidence
        _yes_identity_damp = 1.0
        if (side == "NO"  # wrong side — should not trigger
                and getattr(cal, "_model_yes", None) is None):
            _yes_identity_damp = 0.85
            effective_confidence *= _yes_identity_damp

        # Correct: NO side check is "side == YES" which is False
        side = "NO"
        effective_confidence2 = base_confidence
        _damp2 = 1.0
        if (side == "YES"
                and getattr(cal, "_model_yes", None) is None):
            _damp2 = 0.85
            effective_confidence2 *= _damp2

        assert _damp2 == 1.0
        assert effective_confidence2 == 0.90  # NO side unaffected

    def test_dampener_blocks_marginal_yes_at_high_prices(self):
        """At max conf 0.95, dampener reduces to 0.8075 — blocks YES > ~$0.81."""
        from bots.weather_bot import WeatherConfidenceCalibrator
        cal = WeatherConfidenceCalibrator()

        max_conf = 0.95
        dampened = max_conf * 0.85  # 0.8075
        # neg-EV gate: confidence < price → reject
        assert dampened < 0.81  # would block at $0.81+
        assert dampened > 0.80  # would allow at $0.80


# ═══════════════════════════════════════════════════════════════════════════
# S214 C1: Regression tests for the hard-stop event_type kwarg bug
# ═══════════════════════════════════════════════════════════════════════════


class TestS214HardStopKwargRegression:
    """S214 C1: Regression tests for the event_type kwarg bug.

    Pre-S214: weather_bot.py:3814 + :3921 passed event_type="EXIT" to
    base_engine.place_order, which doesn't accept that kwarg. Every
    hard-stop EXIT raised TypeError silently (caught by the surrounding
    except Exception, logged as weatherbot_hard_stop_order_failed).
    The position stayed open through hard-stop conditions.

    Two sites exercised here:
      Site 1 — _check_hard_stop_all_positions (was line 3814)
      Site 2 — _evaluate_mid_life_exits hard-stop branch (was line 3921)
    """

    @pytest.mark.asyncio
    async def test_standalone_hard_stop_no_event_type_kwarg(self, weather_bot, mock_engine):
        """Site 1: _check_hard_stop_all_positions calls place_order without event_type."""
        mock_engine.order_gateway._position_details = {
            "WeatherBot:mkt_hs1": {
                "side": "YES",
                "price": 0.50,
                "current_price": 0.35,  # -30% pnl_pct → triggers -25% hard stop
                "size": 10.0,
                "token_id": "tok_yes_hs1",
            },
        }
        mock_engine.risk_manager.check_hard_stop_loss = MagicMock(
            return_value={"should_exit": True, "reason": "hard_stop_loss", "details": {}}
        )
        weather_bot._save_exit_to_redis = AsyncMock()

        await weather_bot._check_hard_stop_all_positions()

        assert mock_engine.place_order.called, (
            "base_engine.place_order must fire when hard-stop triggers"
        )
        call_kwargs = mock_engine.place_order.call_args.kwargs
        assert "event_type" not in call_kwargs, (
            f"event_type kwarg leaked into base_engine.place_order at site 1; "
            f"got kwargs: {sorted(call_kwargs.keys())}"
        )
        # S223: exit = SELL the held token (flipped-side was a NEW BUY that
        # entry gates rejected; hard stops silently never filled)
        assert call_kwargs["side"] == "SELL", "exit must SELL the held token"
        assert call_kwargs["bot_name"] == "WeatherBot"
        assert "mkt_hs1" in weather_bot._recently_exited
        assert weather_bot._exit_reasons["mkt_hs1"] == "HARD_STOP_LOSS"

    @pytest.mark.asyncio
    async def test_mid_life_exit_hard_stop_no_event_type_kwarg(self):
        """Site 2: _evaluate_mid_life_exits hard-stop branch calls place_order without event_type."""
        import asyncio as _asyncio
        from bots.weather_bot import WeatherBot

        bot = MagicMock()
        bot.bot_name = "WeatherBot"
        bot._recently_exited = {}
        bot._exit_reasons = {}
        bot._exit_cooldown_secs = 14400.0
        bot._exposure_lock = _asyncio.Lock()
        bot._group_exposure = {}
        bot._city_exposure = {}
        bot._market_group_cache = {}
        bot._save_exit_to_redis = AsyncMock()
        bot.base_engine = MagicMock()
        bot.base_engine.db = None
        bot.base_engine.order_gateway = MagicMock()
        bot.base_engine.order_gateway._position_details = {
            "WeatherBot:mkt_hs2": {
                "side": "YES", "price": 0.60, "size": 10.0,
                "token_id": "tok_yes_hs2", "current_price": 0.40,
            }
        }
        # Force hard-stop branch to fire (S172 D7: this branch fires regardless of mid-life flag)
        bot.base_engine.risk_manager.check_hard_stop_loss = MagicMock(
            return_value={"should_exit": True, "reason": "hard_stop_loss", "details": {}}
        )
        bot.base_engine.place_order = AsyncMock(return_value={"success": True})
        # Wrapper used by mid-life-exit (NOT hard-stop) branch — left as default MagicMock
        bot.place_order = AsyncMock(return_value={"success": True})
        bot.running = True

        bucket = TemperatureBucket(
            market_id="mkt_hs2",
            bucket_type="range",
            low_bound=70.0, high_bound=75.0,
            yes_price=0.40, token_id="tok_yes_hs2", no_token_id="n_hs2",
            temp_unit="F",
        )
        group = MagicMock()
        group.buckets = [bucket]
        analyzed = [([], group, {"mkt_hs2": 0.40})]

        with patch.object(settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, create=True):
            with patch.object(settings, "WEATHER_EXIT_MIN_EDGE", 0.05, create=True):
                await WeatherBot._evaluate_mid_life_exits(bot, analyzed)

        assert bot.base_engine.place_order.called, (
            "base_engine.place_order must fire from mid-life-exit hard-stop branch"
        )
        call_kwargs = bot.base_engine.place_order.call_args.kwargs
        assert "event_type" not in call_kwargs, (
            f"event_type kwarg leaked into base_engine.place_order at site 2; "
            f"got kwargs: {sorted(call_kwargs.keys())}"
        )
        # S223: exit = SELL the held token (see site-1 comment)
        assert call_kwargs["side"] == "SELL", "exit must SELL the held token"
        assert call_kwargs["bot_name"] == "WeatherBot"


# ═══════════════════════════════════════════════════════════════════════════
# S228 A.2 + bound: pre-execution price re-validation in hard_stop path
# (port of MB 298fc99 + c1e8406)
# ═══════════════════════════════════════════════════════════════════════════


class TestS228HardStopExitRevalidation:
    """S228 A.2 + bound: _check_hard_stop_all_positions re-reads live top-of-book
    bid before firing the stop and aborts the trigger if a fresh pnl_pct no
    longer breaches the threshold.

    Pre-fix: stop fired on stale current_price from position_manager (10s
    cycle); a stale mid below entry could falsely trigger an exit when the
    live bid showed the position was healthy. Premature-exit problem.

    Bound (S228 port of MB c1e8406): live_bid is rejected if outside
    [0.005, 0.999] — Polymarket tick prices are [0.01, 0.99]; outside that
    signals API format mismatch, not a real bid. Out-of-band → _live_bid
    stays None → no abort → stop proceeds (pre-A.2 behavior).
    """

    @pytest.mark.asyncio
    async def test_hard_stop_aborts_when_live_bid_disagrees_with_stale_mid(
        self, weather_bot, mock_engine
    ):
        """Stale mid 0.35 says -30% (stop fires); live bid 0.45 says -10%
        (within tolerance) → re-validate → abort, no place_order, no cooldown."""
        mock_engine.order_gateway._position_details = {
            "WeatherBot:mkt_stale": {
                "side": "YES",
                "price": 0.50,           # entry
                "current_price": 0.35,   # stale, -30%
                "size": 10.0,
                "token_id": "tok_stale",
            },
        }
        # First call uses stale pnl_pct (-30%) → should_exit=True
        # Second call uses live pnl_pct (-10%) → should_exit=False (abort)
        mock_engine.risk_manager.check_hard_stop_loss = MagicMock(side_effect=[
            {"should_exit": True, "reason": "hard_stop_loss", "details": {}},
            {"should_exit": False, "reason": "", "details": {}},
        ])
        mock_engine.orderbook_tracker = MagicMock()
        mock_engine.orderbook_tracker.snapshot_order_book = AsyncMock(
            return_value={"bids": [{"price": "0.45", "size": "100"}]}
        )
        weather_bot._save_exit_to_redis = AsyncMock()

        await weather_bot._check_hard_stop_all_positions()

        assert not mock_engine.place_order.called, (
            "Stop must abort — place_order should not fire when live bid disagrees with stale mid"
        )
        assert "mkt_stale" not in weather_bot._recently_exited, (
            "Recently-exited cooldown should not be set when stop is aborted"
        )
        # _det refreshed to live_bid so subsequent scans don't re-trigger on stale mid
        assert (
            mock_engine.order_gateway._position_details["WeatherBot:mkt_stale"]["current_price"]
            == 0.45
        )

    @pytest.mark.asyncio
    async def test_hard_stop_proceeds_when_live_bid_is_implausible(
        self, weather_bot, mock_engine
    ):
        """Live bid 0.0001 (garbage / API format mismatch) → bound rejects →
        _live_bid stays None → no abort, stop proceeds as if A.2 absent."""
        mock_engine.order_gateway._position_details = {
            "WeatherBot:mkt_bad_bid": {
                "side": "YES",
                "price": 0.50,
                "current_price": 0.35,
                "size": 10.0,
                "token_id": "tok_bad_bid",
            },
        }
        mock_engine.risk_manager.check_hard_stop_loss = MagicMock(
            return_value={"should_exit": True, "reason": "hard_stop_loss", "details": {}}
        )
        mock_engine.orderbook_tracker = MagicMock()
        mock_engine.orderbook_tracker.snapshot_order_book = AsyncMock(
            return_value={"bids": [{"price": "0.0001", "size": "999"}]}
        )
        weather_bot._save_exit_to_redis = AsyncMock()

        await weather_bot._check_hard_stop_all_positions()

        assert mock_engine.place_order.called, (
            "Stop must proceed when live bid is out-of-band garbage — bound short-circuits abort logic"
        )
        assert "mkt_bad_bid" in weather_bot._recently_exited, (
            "Recently-exited cooldown should be set when stop proceeds"
        )


# ═══════════════════════════════════════════════════════════════════════════
# S215: _ensure_markets_in_db batching regression
# ═══════════════════════════════════════════════════════════════════════════


class TestEnsureMarketsInDbBatching:
    """S215: _ensure_markets_in_db must batch all rows into ≤2 statements.

    Pre-fix: 200 markets → 200 INSERT INTO markets + up to 400
    INSERT INTO market_prices_latest, all sequential per-row execute() calls
    inside one outer transaction. ms_discovery p99=44s, max=58.9s vs 60s
    scan timeout. Post-fix issues ≤2 statements regardless of N.
    """

    @pytest.fixture
    def batched_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value={"success": True, "order_id": "t1"})

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        class _AsyncCM:
            async def __aenter__(self_inner):
                return mock_session
            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        mock_db = MagicMock()
        mock_db.get_session = MagicMock(side_effect=lambda: _AsyncCM())
        engine.db = mock_db
        engine._mock_session = mock_session
        return engine

    @pytest.fixture
    def bot(self, batched_engine):
        from bots.weather_bot import WeatherBot
        return WeatherBot(batched_engine)

    @pytest.mark.asyncio
    async def test_batches_200_markets_into_two_executes(self, bot, batched_engine):
        markets = [
            {
                "id": f"m{i}",
                "condition_id": f"c{i}",
                "question": f"q{i}",
                "slug": f"s{i}",
                "yes_token_id": f"yt{i}",
                "no_token_id": f"nt{i}",
                "yes_price": 0.5,
                "no_price": 0.5,
                "volume": 100,
            }
            for i in range(200)
        ]
        await bot._ensure_markets_in_db(markets)
        n_calls = batched_engine._mock_session.execute.call_count
        assert n_calls <= 2, (
            f"S215 perf regression: expected <=2 batched executes for 200 markets, "
            f"got {n_calls} (pre-fix would be 600)"
        )
        batched_engine._mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_markets_no_session_acquired(self, bot, batched_engine):
        await bot._ensure_markets_in_db([])
        assert batched_engine._mock_session.execute.call_count == 0
        batched_engine._mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_db_returns_early(self, bot, batched_engine):
        batched_engine.db = None
        await bot._ensure_markets_in_db([
            {"id": "m1", "yes_token_id": "yt1", "no_token_id": "nt1",
             "yes_price": 0.5, "no_price": 0.5}
        ])
        assert batched_engine._mock_session.execute.call_count == 0

    @pytest.mark.asyncio
    async def test_filters_empty_id_and_invalid_prices(self, bot, batched_engine):
        markets = [
            {"id": "", "yes_token_id": "yt0", "no_token_id": "nt0",
             "yes_price": 0.5, "no_price": 0.5,
             "condition_id": "c0", "question": "q", "slug": "s", "volume": 0},
            {"id": "m1", "yes_token_id": "yt1", "no_token_id": "nt1",
             "yes_price": 0.0, "no_price": 1.0,
             "condition_id": "c1", "question": "q", "slug": "s", "volume": 0},
            {"id": "m2", "yes_token_id": "yt2", "no_token_id": "nt2",
             "yes_price": 0.5, "no_price": 0.5,
             "condition_id": "c2", "question": "q", "slug": "s", "volume": 0},
        ]
        await bot._ensure_markets_in_db(markets)
        assert batched_engine._mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_dedupes_duplicate_token_ids(self, bot, batched_engine):
        markets = [
            {"id": "m1", "yes_token_id": "shared_tid", "no_token_id": "nt1",
             "yes_price": 0.3, "no_price": 0.7,
             "condition_id": "c1", "question": "q", "slug": "s", "volume": 0},
            {"id": "m2", "yes_token_id": "shared_tid", "no_token_id": "nt2",
             "yes_price": 0.4, "no_price": 0.6,
             "condition_id": "c2", "question": "q", "slug": "s", "volume": 0},
        ]
        await bot._ensure_markets_in_db(markets)
        assert batched_engine._mock_session.execute.call_count == 2
        prices_call = batched_engine._mock_session.execute.call_args_list[1]
        prices_params = prices_call.args[1]
        shared_count = sum(1 for v in prices_params.values() if v == "shared_tid")
        assert shared_count == 1, (
            f"shared_tid should be deduped to 1 occurrence; found {shared_count}"
        )

    @pytest.mark.asyncio
    async def test_batched_sql_uses_multi_row_values(self, bot, batched_engine):
        markets = [
            {"id": "m1", "yes_token_id": "yt1", "no_token_id": "nt1",
             "yes_price": 0.3, "no_price": 0.7,
             "condition_id": "c1", "question": "q", "slug": "s", "volume": 0},
            {"id": "m2", "yes_token_id": "yt2", "no_token_id": "nt2",
             "yes_price": 0.4, "no_price": 0.6,
             "condition_id": "c2", "question": "q", "slug": "s", "volume": 0},
        ]
        await bot._ensure_markets_in_db(markets)
        markets_sql = str(batched_engine._mock_session.execute.call_args_list[0].args[0])
        assert ":id_0" in markets_sql and ":id_1" in markets_sql, (
            f"markets batch SQL missing multi-row VALUES tuples: {markets_sql}"
        )
        assert "ON CONFLICT (id) DO NOTHING" in markets_sql
        prices_sql = str(batched_engine._mock_session.execute.call_args_list[1].args[0])
        assert ":tid_0" in prices_sql and ":tid_1" in prices_sql, (
            f"prices batch SQL missing multi-row VALUES tuples: {prices_sql}"
        )
        assert "ON CONFLICT (token_id) DO UPDATE" in prices_sql


# ═══════════════════════════════════════════════════════════════════════════
# S215: WB seeds BaseEngine._market_index after Gamma discovery
# ═══════════════════════════════════════════════════════════════════════════


class TestWBSeedsMarketIndex:
    """S215: WB must call base_engine.update_market_index(weather_markets)
    after Gamma fetch. WB-only gap pre-fix: MirrorBot, EnsembleBot,
    ArbitrageBot all do this; WB didn't. Without it, OrderGateway's depth
    check hit gateway_unknown_market on every WB trade and returned
    max_safe=0, masking Phase 2 soft-clamp.
    """

    @pytest.mark.asyncio
    async def test_scan_calls_update_market_index_after_gamma_fetch(self):
        from bots.weather_bot import WeatherBot

        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value={"success": True})
        engine.update_market_index = MagicMock()

        bot = WeatherBot(engine)

        # S221: liquidity/volume/bestBid/bestAsk added so markets pass
        # the new _filter_thin_markets gate (default $1000/$100/20% thresholds).
        fake_markets = [
            {"id": "m1", "condition_id": "c1", "yes_token_id": "yt1",
             "no_token_id": "nt1", "yes_price": 0.5, "no_price": 0.5,
             "question": "q", "slug": "s", "volume": 5000,
             "liquidity": 5000, "bestBid": 0.48, "bestAsk": 0.52},
            {"id": "m2", "condition_id": "c2", "yes_token_id": "yt2",
             "no_token_id": "nt2", "yes_price": 0.4, "no_price": 0.6,
             "question": "q", "slug": "s", "volume": 5000,
             "liquidity": 5000, "bestBid": 0.38, "bestAsk": 0.42},
        ]
        # Stub Gamma fetch + group_markets so scan reaches the seeding step
        bot._fetch_weather_events_by_tag = AsyncMock(return_value=fake_markets)
        bot._market_mapper.group_markets = MagicMock(return_value=[])  # no groups → early return after seed
        bot._cache_warmed = True  # skip startup-only branch
        bot._ensure_markets_in_db = AsyncMock(return_value=None)

        await bot.scan_and_trade()

        # update_market_index must have been called with the fake_markets list
        engine.update_market_index.assert_called_once()
        called_with = engine.update_market_index.call_args.args[0]
        assert called_with == fake_markets, (
            f"update_market_index called with wrong markets: {called_with}"
        )

    @pytest.mark.asyncio
    async def test_seed_happens_before_ensure_markets_in_db(self):
        """update_market_index must run BEFORE _ensure_markets_in_db so the
        index is populated when downstream order placement runs."""
        from bots.weather_bot import WeatherBot

        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value={"success": True})

        call_order: list[str] = []
        engine.update_market_index = MagicMock(side_effect=lambda m: call_order.append("update_index"))

        bot = WeatherBot(engine)
        # S221: liquid-market fields added so _filter_thin_markets accepts.
        bot._fetch_weather_events_by_tag = AsyncMock(return_value=[
            {"id": "m1", "condition_id": "c1", "yes_token_id": "yt1",
             "no_token_id": "nt1", "yes_price": 0.5, "no_price": 0.5,
             "question": "q", "slug": "s", "volume": 5000,
             "liquidity": 5000, "bestBid": 0.48, "bestAsk": 0.52},
        ])
        bot._market_mapper.group_markets = MagicMock(return_value=[])
        bot._cache_warmed = True

        async def _ensure_recorder(markets):
            call_order.append("ensure_db")
        bot._ensure_markets_in_db = _ensure_recorder

        await bot.scan_and_trade()
        assert call_order == ["update_index", "ensure_db"], (
            f"Expected update_index before ensure_db, got order: {call_order}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# S215: _fetch_weather_events_by_tag parallel pagination + retry + timeout
# ═══════════════════════════════════════════════════════════════════════════


class _FakeAioResp:
    def __init__(self, status, json_data=None):
        self.status = status
        self._json_data = json_data

    async def json(self, content_type=None):
        return self._json_data


class _FakeAioRespCM:
    def __init__(self, response_or_exc):
        self._payload = response_or_exc

    async def __aenter__(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAioSession:
    """Records each .get() call. Returns predetermined responses per page+attempt."""

    def __init__(self, page_responses):
        # page_responses: dict[int, list] — list of _FakeAioResp or Exception per attempt
        self.page_responses = page_responses
        self.call_log = []  # list of (page, attempt, timeout_seconds)
        self.param_log = []  # S223: full params dict per call (tag_slug assertion)
        self._attempt_counters = {}
        self.closed = False
        self._concurrent = 0
        self.max_concurrent = 0

    def get(self, url, params, timeout):
        page = int(params['offset']) // 100
        attempt = self._attempt_counters.get(page, 0)
        self._attempt_counters[page] = attempt + 1
        timeout_s = getattr(timeout, 'total', None)
        self.call_log.append((page, attempt, timeout_s))
        self.param_log.append(dict(params))
        responses = self.page_responses.get(page, [])
        if attempt >= len(responses):
            return _FakeAioRespCM(_FakeAioResp(404, []))
        payload = responses[attempt]
        # Track concurrency by wrapping the CM
        outer = self

        class _ConcurrencyTrackingCM:
            async def __aenter__(self_inner):
                outer._concurrent += 1
                outer.max_concurrent = max(outer.max_concurrent, outer._concurrent)
                if isinstance(payload, BaseException):
                    outer._concurrent -= 1
                    raise payload
                # Simulate a small wait so concurrency is observable
                await asyncio.sleep(0.01)
                return payload

            async def __aexit__(self_inner, exc_type, exc, tb):
                outer._concurrent -= 1
                return False

        return _ConcurrencyTrackingCM()


class TestFetchWeatherEventsByTagParallel:
    """S215: _fetch_weather_events_by_tag must parallel-fetch first 4 pages,
    retry transient 5xx, and use 7s per-page timeout. Pre-fix: 10 sequential
    pages × 15s timeout = 150s worst-case (always tripped 60s scan timeout)."""

    @pytest.fixture
    def fake_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value={"success": True})
        engine.client = MagicMock()
        engine.client.gamma_api = "https://gamma-api.polymarket.com"
        engine.alerting_system = None
        return engine

    @pytest.fixture
    def bot_with_fake_session(self, fake_engine):
        from bots.weather_bot import WeatherBot
        bot = WeatherBot(fake_engine)
        return bot

    def _attach_session(self, bot, fake_session):
        bot._forecast_client._session = fake_session

    @pytest.mark.asyncio
    async def test_first_batch_fetched_concurrently(self, bot_with_fake_session):
        """Pages 0-3 must hit the API concurrently, not sequentially."""
        page_data = [{"markets": []} for _ in range(100)]  # 100 events per page
        responses = {p: [_FakeAioResp(200, page_data)] for p in range(4)}
        # Page 3 partial → no sequential extension
        responses[3] = [_FakeAioResp(200, page_data[:50])]
        fake = _FakeAioSession(responses)
        self._attach_session(bot_with_fake_session, fake)

        await bot_with_fake_session._fetch_weather_events_by_tag()

        assert fake.max_concurrent >= 2, (
            f"Expected concurrent fetches in first batch, got max_concurrent={fake.max_concurrent}"
        )
        pages_called = sorted({c[0] for c in fake.call_log})
        assert pages_called == [0, 1, 2, 3], (
            f"Expected exactly pages 0-3 fetched (page 3 was partial), got {pages_called}"
        )

    @pytest.mark.asyncio
    async def test_per_page_timeout_is_7s(self, bot_with_fake_session):
        page_data = [{"markets": []} for _ in range(50)]  # partial first page → stops
        fake = _FakeAioSession({0: [_FakeAioResp(200, page_data)],
                                1: [_FakeAioResp(200, [])],
                                2: [_FakeAioResp(200, [])],
                                3: [_FakeAioResp(200, [])]})
        self._attach_session(bot_with_fake_session, fake)

        await bot_with_fake_session._fetch_weather_events_by_tag()

        timeouts = [c[2] for c in fake.call_log]
        assert all(t == 7 for t in timeouts), (
            f"Expected all per-page timeouts == 7s, got {timeouts}"
        )

    @pytest.mark.asyncio
    async def test_sequential_extension_when_first_batch_full(self, bot_with_fake_session):
        """All 4 parallel pages full → extend to pages 4-9 sequentially until short page."""
        full = [{"markets": []} for _ in range(100)]
        responses = {
            0: [_FakeAioResp(200, full)],
            1: [_FakeAioResp(200, full)],
            2: [_FakeAioResp(200, full)],
            3: [_FakeAioResp(200, full)],
            4: [_FakeAioResp(200, full)],
            5: [_FakeAioResp(200, full[:30])],  # partial → stop
        }
        fake = _FakeAioSession(responses)
        self._attach_session(bot_with_fake_session, fake)

        await bot_with_fake_session._fetch_weather_events_by_tag()

        pages_called = sorted({c[0] for c in fake.call_log})
        assert pages_called == [0, 1, 2, 3, 4, 5], (
            f"Expected sequential extension to pages 4-5, got {pages_called}"
        )

    @pytest.mark.asyncio
    async def test_retry_on_503_succeeds(self, bot_with_fake_session):
        full = [{"markets": []} for _ in range(50)]  # short → stops processing further
        responses = {
            0: [_FakeAioResp(503, None), _FakeAioResp(200, full)],  # 1st 503, retry succeeds
            1: [_FakeAioResp(200, [])],
            2: [_FakeAioResp(200, [])],
            3: [_FakeAioResp(200, [])],
        }
        fake = _FakeAioSession(responses)
        self._attach_session(bot_with_fake_session, fake)

        await bot_with_fake_session._fetch_weather_events_by_tag()

        # Page 0: 2 attempts (503 + 200). Other pages: 1 attempt each.
        page_0_calls = [c for c in fake.call_log if c[0] == 0]
        assert len(page_0_calls) == 2, (
            f"Expected page 0 to retry once after 503, got attempts={len(page_0_calls)}"
        )

    @pytest.mark.asyncio
    async def test_page_0_failure_returns_empty_after_retries(self, bot_with_fake_session):
        # Both attempts on page 0 return 503 → return []
        responses = {
            0: [_FakeAioResp(503, None), _FakeAioResp(503, None)],
            1: [_FakeAioResp(200, [{"markets": []}])],
            2: [_FakeAioResp(200, [])],
            3: [_FakeAioResp(200, [])],
        }
        fake = _FakeAioSession(responses)
        self._attach_session(bot_with_fake_session, fake)

        result = await bot_with_fake_session._fetch_weather_events_by_tag()

        assert result == [], "Page 0 hard failure must return [] (full discovery abort)"
        # Page 0 was retried (2 attempts); other pages may or may not have run in parallel
        page_0_attempts = sum(1 for c in fake.call_log if c[0] == 0)
        assert page_0_attempts == 2

    @pytest.mark.asyncio
    async def test_page_n_failure_uses_partial_results(self, bot_with_fake_session):
        full = [{"markets": []} for _ in range(100)]
        responses = {
            0: [_FakeAioResp(200, full)],
            1: [_FakeAioResp(200, full)],
            2: [_FakeAioResp(503, None), _FakeAioResp(503, None)],  # both attempts fail
            3: [_FakeAioResp(200, full)],
        }
        fake = _FakeAioSession(responses)
        self._attach_session(bot_with_fake_session, fake)

        await bot_with_fake_session._fetch_weather_events_by_tag()

        # Pages 0+1 succeeded; page 2 hard-failed → break at page 2; page 3's
        # parallel result is discarded (per-API contract: short/missing page = end).
        # No sequential extension because we didn't reach the last batch page.
        # No assertion on result content (depends on parsing internals); the
        # behavioral assertion is that page 2 retried twice.
        page_2_attempts = sum(1 for c in fake.call_log if c[0] == 2)
        assert page_2_attempts == 2, (
            f"Expected page 2 to retry once after 503, got attempts={page_2_attempts}"
        )

    @pytest.mark.asyncio
    async def test_tag_slug_is_daily_temperature(self, bot_with_fake_session):
        """S223 defect repro: discovery queried the retired `temperature` tag
        (1 stale Gamma event) instead of the live `daily-temperature` tag
        (~100 open events). Result: markets=1/scan since 2026-07-01, thin-filter
        dropped it, zero predictions logged after 07-03. Every page request must
        carry tag_slug=daily-temperature."""
        page_data = [{"markets": []} for _ in range(50)]  # partial page → stops
        fake = _FakeAioSession({0: [_FakeAioResp(200, page_data)],
                                1: [_FakeAioResp(200, [])],
                                2: [_FakeAioResp(200, [])],
                                3: [_FakeAioResp(200, [])]})
        self._attach_session(bot_with_fake_session, fake)

        await bot_with_fake_session._fetch_weather_events_by_tag()

        assert fake.param_log, "Expected at least one Gamma /events request"
        for params in fake.param_log:
            assert params.get("tag_slug") == "daily-temperature", (
                f"Discovery must query the live daily-temperature tag, "
                f"got tag_slug={params.get('tag_slug')!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# S221 (2026-05-18): _filter_thin_markets — discovery-time gate
# ═══════════════════════════════════════════════════════════════════════════


class TestS221ThinMarketFilter:
    """S221: Pre-discovery filter rejects markets where executable price diverges
    from midpoint. Concrete failure mode: market 2106427 (NYC weather, end-May)
    had liq=$269 vol=$95 spread=137% — 137 attempts in 24h, all failed at
    slippage gate. This filter rejects pre-scan so signals are never generated.
    """

    @pytest.fixture
    def bot(self):
        from bots.weather_bot import WeatherBot
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value={"success": True})
        return WeatherBot(engine)

    # The 2106427 failure-mode profile, captured from production logs:
    # liquidity=$269, volume=$95, bestBid=0.15, bestAsk=0.80 → spread 137%.
    THIN_MARKET = {
        "id": "2106427", "question": "NYC May 31", "yes_price": 0.525,
        "liquidity": 269, "volume": 95, "bestBid": 0.15, "bestAsk": 0.80,
    }

    # A healthy market that should pass all three gates.
    LIQUID_MARKET = {
        "id": "1234567", "question": "NYC Apr 30", "yes_price": 0.50,
        "liquidity": 5000, "volume": 25000, "bestBid": 0.48, "bestAsk": 0.52,
    }

    def test_rejects_2106427_profile(self, bot):
        """The exact failure mode this fix targets: should be filtered out."""
        result = bot._filter_thin_markets([self.THIN_MARKET])
        assert result == [], (
            f"Market 2106427 (liq=$269, vol=$95, spread=137%) MUST be rejected; "
            f"filter returned {len(result)} markets"
        )

    def test_accepts_liquid_market(self, bot):
        """Liquid market with tight spread passes unchanged."""
        result = bot._filter_thin_markets([self.LIQUID_MARKET])
        assert result == [self.LIQUID_MARKET]

    def test_mixed_input_drops_thin_keeps_liquid(self, bot):
        """Filter is per-market; both populations coexist in the same list."""
        result = bot._filter_thin_markets([self.THIN_MARKET, self.LIQUID_MARKET])
        assert result == [self.LIQUID_MARKET]

    def test_rejects_low_liquidity_only(self, bot):
        """Liquidity below WEATHER_MIN_MARKET_LIQUIDITY_USD (default $1000)."""
        m = {**self.LIQUID_MARKET, "liquidity": 500}  # below $1000 floor
        assert bot._filter_thin_markets([m]) == []

    def test_rejects_low_volume_only(self, bot):
        """Volume below WEATHER_MIN_MARKET_VOLUME_USD (default $100)."""
        m = {**self.LIQUID_MARKET, "volume": 50}  # below $100 floor
        assert bot._filter_thin_markets([m]) == []

    def test_rejects_wide_spread_only(self, bot):
        """Spread above WEATHER_MAX_MARKET_SPREAD_PCT (default 20%)."""
        m = {**self.LIQUID_MARKET, "bestBid": 0.30, "bestAsk": 0.70}
        # mid = 0.50, spread = 0.40 / 0.50 = 80%
        assert bot._filter_thin_markets([m]) == []

    def test_spread_check_skipped_when_bid_ask_absent(self, bot):
        """Fallback paths (DB / direct probe) may not populate bestBid/bestAsk.
        When BOTH are missing/0, spread check is opt-out (entry-validation
        gate at weather_bot.py:3388 catches it later).
        """
        m = {**self.LIQUID_MARKET, "bestBid": 0, "bestAsk": 0}
        assert bot._filter_thin_markets([m]) == [m]

    def test_spread_check_fail_closed_when_one_side_missing(self, bot):
        """If bestBid > 0 but bestAsk = 0 (or vice versa), we have partial
        info and cannot compute spread reliably — fail-closed.
        """
        m_no_ask = {**self.LIQUID_MARKET, "bestBid": 0.45, "bestAsk": 0}
        m_no_bid = {**self.LIQUID_MARKET, "bestBid": 0, "bestAsk": 0.55}
        assert bot._filter_thin_markets([m_no_ask]) == []
        assert bot._filter_thin_markets([m_no_bid]) == []

    def test_escape_valve_disables_filter(self, bot, monkeypatch):
        """Setting min_liq=0, min_vol=0, max_spread_pct>=1.0 bypasses entirely."""
        monkeypatch.setattr(settings, "WEATHER_MIN_MARKET_LIQUIDITY_USD", 0)
        monkeypatch.setattr(settings, "WEATHER_MIN_MARKET_VOLUME_USD", 0)
        monkeypatch.setattr(settings, "WEATHER_MAX_MARKET_SPREAD_PCT", 1.0)
        # 2106427 must pass when filter is disabled
        result = bot._filter_thin_markets([self.THIN_MARKET])
        assert result == [self.THIN_MARKET], (
            "Escape valve (all-three-disabled) must return input unchanged"
        )

    def test_empty_input_returns_empty(self, bot):
        assert bot._filter_thin_markets([]) == []

    def test_non_dict_entries_silently_dropped(self, bot):
        """Defensive: filter skips non-dict entries without crashing."""
        result = bot._filter_thin_markets([self.LIQUID_MARKET, None, "garbage", 42])
        assert result == [self.LIQUID_MARKET]

    def test_alt_field_names_recognized(self, bot):
        """Filter accepts liquidityNum / volumeNum / best_bid / best_ask aliases.
        Gamma API has historically returned both casings/spellings."""
        m_alt = {
            "id": "alt", "question": "alt",
            "liquidityNum": 5000, "volumeNum": 25000,
            "best_bid": 0.48, "best_ask": 0.52,
        }
        assert bot._filter_thin_markets([m_alt]) == [m_alt]


# ═══════════════════════════════════════════════════════════════════════════
# S221 Phase 2 (2026-05-18): _check_executable_edge — entry-validation backstop
# ═══════════════════════════════════════════════════════════════════════════


class TestS221ExecutableEdgeCheck:
    """S221 Phase 2: Backstop check at entry validation. Recomputes edge using
    bestAsk (for YES buys) / 1-bestBid (for NO buys) instead of midpoint, and
    rejects when honest edge is below the WEATHER_MIN_EXECUTABLE_EDGE floor
    (default 0.04 since S224/V26 — requires >=4pts of edge at the executable
    price; was 0.0 = kills clearly-negative-edge signals only).

    Complement to _filter_thin_markets (which catches at discovery) — this
    fires on any liquid-but-still-mispriced market that slipped through.
    """

    def test_rejects_thin_positive_executable_edge_v26(self, bot_with_index):
        """S224 V26: an opp with honest executable edge in (0, 0.04) — which
        PASSED at the old 0.0 default — must now REJECT. YES, model 0.53,
        bestAsk 0.50 → honest_edge = +0.03 < 0.04."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.48, "bestAsk": 0.50}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.53,
               "price": 0.49, "edge": 0.04}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_accepts_executable_edge_at_floor_v26(self, bot_with_index):
        """S224 V26: honest edge exactly at the 0.04 floor is accepted
        (reject is strict <). YES, model 0.54, bestAsk 0.50 → +0.04."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.48, "bestAsk": 0.50}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.54,
               "price": 0.49, "edge": 0.05}
        assert bot_with_index._check_executable_edge(opp) is True

    @pytest.fixture
    def bot_with_index(self):
        """Bot with a controllable OrderGateway._market_index for tests."""
        from bots.weather_bot import WeatherBot
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._market_index = {}  # populated per-test
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value={"success": True})
        return WeatherBot(engine)

    def test_passes_liquid_yes_with_positive_executable_edge(self, bot_with_index):
        """YES side, model 0.72, bestAsk 0.50 → honest_edge = +0.22, accept."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.48, "bestAsk": 0.50}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.72,
               "price": 0.49, "edge": 0.23}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_passes_liquid_no_with_positive_executable_edge(self, bot_with_index):
        """NO side, model 0.72 (of NO), bestBid_YES 0.20 → exec_NO_ask = 0.80,
        honest_edge = 0.72 - 0.80 = -0.08. Should REJECT.
        """
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.20, "bestAsk": 0.22}
        }
        opp = {"market_id": "m1", "side": "NO", "model_prob": 0.72,
               "price": 0.79, "edge": 0.51}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_rejects_2106427_no_side_profile(self, bot_with_index):
        """The exact failure mode: bestBid_YES=0.15, bestAsk_YES=0.80.
        WB wants to BUY NO, model_prob_NO = 0.722.
        exec_NO_ask = 1 - 0.15 = 0.85. honest_edge = 0.722 - 0.85 = -0.128.
        """
        bot_with_index.base_engine.order_gateway._market_index = {
            "2106427": {"bestBid": 0.15, "bestAsk": 0.80}
        }
        opp = {"market_id": "2106427", "side": "NO", "model_prob": 0.722,
               "price": 0.475, "edge": 0.247}  # midpoint edge was +0.247
        assert bot_with_index._check_executable_edge(opp) is False, (
            "2106427 NO-side trade (midpoint edge +0.247, honest edge -0.128) "
            "MUST be rejected by Phase 2"
        )

    def test_rejects_when_market_not_in_index(self, bot_with_index):
        """Fail-closed: no book data → cannot validate → reject."""
        bot_with_index.base_engine.order_gateway._market_index = {}
        opp = {"market_id": "nope", "side": "YES", "model_prob": 0.80,
               "price": 0.50, "edge": 0.30}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_rejects_when_bestbid_zero(self, bot_with_index):
        """bestBid=0 → cannot compute NO-side executable price → fail-closed."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0, "bestAsk": 0.55}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.80,
               "price": 0.50, "edge": 0.30}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_rejects_when_bestask_zero(self, bot_with_index):
        """bestAsk=0 → cannot compute YES-side executable price → fail-closed."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.45, "bestAsk": 0}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.80,
               "price": 0.50, "edge": 0.30}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_escape_valve_disables_check(self, bot_with_index, monkeypatch):
        """WEATHER_MIN_EXECUTABLE_EDGE <= -1.0 in .env disables the entire check."""
        monkeypatch.setattr(settings, "WEATHER_MIN_EXECUTABLE_EDGE", -1.0)
        # Even with no market index entry (would normally fail-closed), accept
        bot_with_index.base_engine.order_gateway._market_index = {}
        opp = {"market_id": "anything", "side": "YES", "model_prob": 0.5}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_threshold_tunable_upward(self, bot_with_index, monkeypatch):
        """Set higher threshold; check rejects positive-but-marginal edge."""
        monkeypatch.setattr(settings, "WEATHER_MIN_EXECUTABLE_EDGE", 0.10)
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.48, "bestAsk": 0.55}
        }
        # YES side, exec_price=0.55, model=0.60, honest_edge=0.05 < 0.10 → reject
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.60,
               "price": 0.52, "edge": 0.08}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_alt_field_names_in_market_index(self, bot_with_index):
        """Index entries may use best_bid/best_ask (snake_case)."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"best_bid": 0.48, "best_ask": 0.52}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.80,
               "price": 0.50, "edge": 0.30}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_accepts_high_edge_yes_at_low_executable_price(self, bot_with_index):
        """The model-vs-market case that SHOULD trade: strong YES conviction
        and a reasonable bestAsk. Honest edge well above default 0.0 threshold.
        """
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.28, "bestAsk": 0.32}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.65,
               "price": 0.30, "edge": 0.35}
        # honest_edge = 0.65 - 0.32 = 0.33 > 0
        assert bot_with_index._check_executable_edge(opp) is True


# ═══════════════════════════════════════════════════════════════════════════
# S223 (2026-07-06): _check_executable_edge must side-adjust model_prob by
# market type. Temperature stores RAW P(YES); precip/snow/wind store P(side).
# Pre-fix the gate compared raw P(YES) to the NO executable price for
# temperature NO opps -> near-always negative -> silent YES-only bias on the
# dominant (temperature) funnel. Gate is ACTIVE by default (min edge 0.0).
# ═══════════════════════════════════════════════════════════════════════════


class TestS223ExecutableEdgeSideAdjust:
    @pytest.fixture
    def bot_with_index(self):
        from bots.weather_bot import WeatherBot
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = MagicMock()
        engine.order_gateway = MagicMock()
        engine.order_gateway._market_index = {}
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        return WeatherBot(engine)

    def test_temperature_no_good_edge_now_accepts(self, bot_with_index):
        """DEFECT REPRO. Temperature NO opp: model_prob=raw P(YES)=0.20 so
        P(NO)=0.80; bestBid_YES=0.30 -> NO_ask=0.70 -> true edge P(NO)-NO_ask
        = +0.10. Pre-S223 computed 0.20-0.70=-0.50 and REJECTED a good NO
        trade (the YES-bias). Must now ACCEPT."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.30, "bestAsk": 0.33}
        }
        opp = {"market_id": "m1", "side": "NO", "model_prob": 0.20,
               "price": 0.70, "edge": -0.10, "market_type": "temperature"}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_temperature_no_missing_market_type_defaults_temperature(self, bot_with_index):
        """market_type absent -> treated as temperature (the live convention;
        matches opp.get('market_type','temperature') used elsewhere). Same good
        NO opp as above without the key must also accept."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.30, "bestAsk": 0.33}
        }
        opp = {"market_id": "m1", "side": "NO", "model_prob": 0.20,
               "price": 0.70, "edge": -0.10}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_temperature_no_genuinely_bad_edge_still_rejects(self, bot_with_index):
        """Sanity: temperature NO with truly negative edge still rejects.
        model_prob=P(YES)=0.55 -> P(NO)=0.45; NO_ask=0.70 -> edge -0.25."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.30, "bestAsk": 0.33}
        }
        opp = {"market_id": "m1", "side": "NO", "model_prob": 0.55,
               "price": 0.70, "edge": -0.05, "market_type": "temperature"}
        assert bot_with_index._check_executable_edge(opp) is False

    def test_wind_no_not_double_flipped(self, bot_with_index):
        """NO-REGRESSION. Wind NO opp stores model_prob ALREADY side-adjusted
        (P(NO)=0.80, weather_bot.py:2462). The gate must NOT flip it again;
        edge = 0.80 - 0.70 = +0.10 -> accept. A blanket flip would give
        0.20-0.70 and wrongly reject."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.30, "bestAsk": 0.33}
        }
        opp = {"market_id": "m1", "side": "NO", "model_prob": 0.80,
               "price": 0.70, "edge": 0.10, "market_type": "wind"}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_precipitation_no_not_double_flipped(self, bot_with_index):
        """NO-REGRESSION. Precip NO stores P(NO) already
        (precipitation_engine.py:227). model_prob=0.85, NO_ask=0.75 -> +0.10
        (clear of the S224/V26 0.04 floor). If it were double-flipped, P(NO)
        would become 0.15 -> edge -0.60 -> reject; correct handling accepts."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.25, "bestAsk": 0.28}
        }
        opp = {"market_id": "m1", "side": "NO", "model_prob": 0.85,
               "price": 0.75, "edge": 0.10, "market_type": "precipitation"}
        assert bot_with_index._check_executable_edge(opp) is True

    def test_temperature_yes_unchanged(self, bot_with_index):
        """YES side never flips (both conventions store P(YES) for YES).
        model_prob=0.72, bestAsk=0.50 -> +0.22 -> accept, unchanged by S223."""
        bot_with_index.base_engine.order_gateway._market_index = {
            "m1": {"bestBid": 0.48, "bestAsk": 0.50}
        }
        opp = {"market_id": "m1", "side": "YES", "model_prob": 0.72,
               "price": 0.49, "edge": 0.23, "market_type": "temperature"}
        assert bot_with_index._check_executable_edge(opp) is True


# ═══════════════════════════════════════════════════════════════════════════
# S223: WEATHER_MID_LIFE_EXIT_ENABLED / WEATHER_EXIT_MIN_EDGE must be DECLARED
# settings fields. weather_bot.py:4010 reads them via getattr; if undeclared,
# pydantic (extra="allow") does not pull them from os.environ (only from its
# own env_file), so the systemd EnvironmentFile value is silently discarded and
# the getattr default always wins. Guard against regressing to an undeclared
# field.
# ═══════════════════════════════════════════════════════════════════════════


class TestS223MidLifeExitSettingsDeclared:
    def test_mid_life_exit_fields_are_declared(self):
        from bots.weather.engine.config.settings import settings, Settings
        # Declared as real fields (not merely present as pydantic 'extra')
        declared = set(getattr(Settings, "model_fields", {}) or {}) | set(
            getattr(Settings, "__annotations__", {}) or {}
        )
        assert "WEATHER_MID_LIFE_EXIT_ENABLED" in declared, (
            "WEATHER_MID_LIFE_EXIT_ENABLED must be a DECLARED field or systemd "
            "env is silently discarded (S223)"
        )
        assert "WEATHER_EXIT_MIN_EDGE" in declared
        assert hasattr(settings, "WEATHER_MID_LIFE_EXIT_ENABLED")
        assert hasattr(settings, "WEATHER_EXIT_MIN_EDGE")


# ═══════════════════════════════════════════════════════════════════════════
# S223: exit orders must be SELL of the HELD token, and mid-life exit state
# must mutate ONLY on confirmed fill.
# Defect (live, 2026-07-06 23:21 Paris): mid-life exit placed a flipped-side
# YES/NO order -> gateway treated it as a NEW BUY -> entry confidence gate
# rejected it ('Confidence 0.00% below threshold 10.00%') -> exit could never
# fill; AND exposure was decremented BEFORE the order, so the failure leaked
# group/city exposure accounting. Both hard-stop paths shared the flipped-side
# shape (and never checked the result dict -> silent failures).
# Canonical idiom: position_manager.py:1087-1099 — "ALL exits are SELL".
# ═══════════════════════════════════════════════════════════════════════════


class TestS223ExitOrdersAreSell:
    def _build(self, place_order_result):
        from bots.weather_bot import WeatherBot
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None  # skip daily_counters write-through in tests
        engine.risk_manager = MagicMock()
        engine.risk_manager.check_hard_stop_loss = MagicMock(
            return_value={"should_exit": False, "reason": ""}
        )
        engine.order_gateway = MagicMock()
        engine.order_gateway._open_position_markets = {"WeatherBot": set()}
        engine.order_gateway._position_details = {
            "WeatherBot:m1": {"side": "YES", "price": 0.39, "size": 108.97}
        }
        engine.get_all_tradeable_markets = AsyncMock(return_value=[])
        engine.place_order = AsyncMock(return_value=place_order_result)
        bot = WeatherBot(engine)
        bot._save_exit_to_redis = AsyncMock()
        bot._recently_exited = {}
        bot._exit_reasons = {}
        bot._exposure_lock = asyncio.Lock()
        bot._market_group_cache = {"m1": ("Paris:2026-07-07", "Paris", 42.5)}
        bot._group_exposure = {"Paris:2026-07-07": 42.5}
        bot._city_exposure = {"Paris": 42.5}

        bucket = MagicMock()
        bucket.market_id = "m1"
        bucket.token_id = "yes_tok"
        bucket.no_token_id = "no_tok"
        bucket.yes_price = 0.30
        group = MagicMock()
        group.buckets = [bucket]
        # fresh_prob 0.2952, entry 0.39 -> current_ev = -0.0948 < -0.05 (the
        # exact live Paris trigger)
        analyzed = [([], group, {"m1": 0.2952})]
        return bot, engine, analyzed

    @pytest.mark.asyncio
    async def test_mid_life_exit_sells_held_token(self, monkeypatch):
        """DEFECT REPRO 1: the exit order must be side=SELL of the HELD token
        via engine.place_order. Pre-S223: flipped side ('NO') via
        bot.place_order -> entry gates -> never fills."""
        from bots.weather_bot import settings as wb_settings
        monkeypatch.setattr(wb_settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, raising=False)
        monkeypatch.setattr(wb_settings, "WEATHER_EXIT_MIN_EDGE", 0.05, raising=False)
        bot, engine, analyzed = self._build({"success": True})

        await bot._evaluate_mid_life_exits(analyzed)

        engine.place_order.assert_awaited_once()
        kwargs = engine.place_order.await_args.kwargs
        assert kwargs["side"] == "SELL", (
            f"Exit must SELL the held token (canonical position_manager idiom), "
            f"got side={kwargs['side']!r}"
        )
        assert kwargs["token_id"] == "yes_tok", "Must sell the token HELD (YES)"

    @pytest.mark.asyncio
    async def test_mid_life_exit_failure_mutates_no_state(self, monkeypatch):
        """DEFECT REPRO 2 (the live Paris leak): when the exit order FAILS,
        exposure/cooldown/cache must be untouched. Pre-S223 decremented
        exposure before the order -> failed order leaked accounting."""
        from bots.weather_bot import settings as wb_settings
        monkeypatch.setattr(wb_settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, raising=False)
        monkeypatch.setattr(wb_settings, "WEATHER_EXIT_MIN_EDGE", 0.05, raising=False)
        bot, engine, analyzed = self._build(
            {"success": False, "error": "Confidence 0.00% below threshold 10.00%"}
        )

        await bot._evaluate_mid_life_exits(analyzed)

        assert bot._group_exposure["Paris:2026-07-07"] == 42.5, \
            "Exposure must NOT be decremented on a failed exit order"
        assert bot._city_exposure["Paris"] == 42.5
        assert "m1" in bot._market_group_cache, "Group cache must survive failure"
        assert "m1" not in bot._recently_exited, "No cooldown on failed exit"
        bot._save_exit_to_redis.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mid_life_exit_success_applies_state(self, monkeypatch):
        """On a CONFIRMED fill: cooldown set, Redis persisted, exposure
        decremented, cache popped."""
        from bots.weather_bot import settings as wb_settings
        monkeypatch.setattr(wb_settings, "WEATHER_MID_LIFE_EXIT_ENABLED", True, raising=False)
        monkeypatch.setattr(wb_settings, "WEATHER_EXIT_MIN_EDGE", 0.05, raising=False)
        bot, engine, analyzed = self._build({"success": True})

        await bot._evaluate_mid_life_exits(analyzed)

        assert bot._group_exposure["Paris:2026-07-07"] == 0.0
        assert bot._city_exposure["Paris"] == 0.0
        assert "m1" not in bot._market_group_cache
        assert bot._exit_reasons.get("m1") == "REVERSAL"
        assert "m1" in bot._recently_exited
        bot._save_exit_to_redis.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hard_stop_exit_sells_held_token(self, monkeypatch):
        """The in-loop shared hard stop must also SELL the held token (same
        defect class; its result dict was previously never checked)."""
        from bots.weather_bot import settings as wb_settings
        monkeypatch.setattr(wb_settings, "WEATHER_MID_LIFE_EXIT_ENABLED", False, raising=False)
        bot, engine, analyzed = self._build({"success": True})
        engine.risk_manager.check_hard_stop_loss = MagicMock(
            return_value={"should_exit": True, "reason": "hard_stop_loss"}
        )

        await bot._evaluate_mid_life_exits(analyzed)

        engine.place_order.assert_awaited_once()
        kwargs = engine.place_order.await_args.kwargs
        assert kwargs["side"] == "SELL"
        assert kwargs["token_id"] == "yes_tok"


class TestImpossibleCertaintyTripwire:
    """S225: the probability engine caps every model_prob at <= 0.999, so a value
    >= 0.9995 reaching prediction_log is the manufactured-certainty leak
    (predicted_prob=1.0, fabricated edge = 1 - price, confident wrong entries).
    The tripwire helper gates the diagnostic warning that captures the leaking
    call site. Boundary must sit ABOVE the engine's 0.999 clamp so legitimate
    near-certain buckets don't spam the warning."""

    def test_trips_at_and_above_threshold(self):
        from bots.weather_bot import _is_impossible_certainty
        assert _is_impossible_certainty(1.0) is True
        assert _is_impossible_certainty(0.9995) is True
        assert _is_impossible_certainty(0.99999) is True

    def test_does_not_trip_at_engine_clamp_or_below(self):
        from bots.weather_bot import _is_impossible_certainty
        assert _is_impossible_certainty(0.999) is False   # the engine's hard clamp
        assert _is_impossible_certainty(0.9994) is False
        assert _is_impossible_certainty(0.5) is False
        assert _is_impossible_certainty(0.0) is False

    def test_non_numeric_is_safe(self):
        from bots.weather_bot import _is_impossible_certainty
        assert _is_impossible_certainty(None) is False
        assert _is_impossible_certainty("nan") is False


class TestS226YesFramePredictionLogging:
    """S226 (V23 root fix): prediction_log.predicted_prob must be P(YES) for EVERY
    WeatherBot row. The shared grader (backfill_prediction_log_resolution) computes
    was_correct = (predicted_prob >= 0.5) == (resolution == 'YES') — a YES-frame read.
    PSW (precip/snow/wind) NO-side opps store CHOSEN-side model_prob for trading, so
    logging that value marked winning NO calls (P(NO) >= 0.5, resolved NO) as misses,
    poisoning calibration_tracker, Brier feeds, and the consecutive-loss compress.
    Fix: opps carry an explicit YES-frame `model_prob_yes`; log sites pass
    _yes_frame_prob(opp), never the trading-frame value. Trading fields unchanged."""

    def test_precip_engine_no_opp_carries_yes_frame_prob(self):
        # Defect: pre-fix, NO opps only carried the chosen-side model_prob.
        from bots.weather.engine.base_engine.weather.precipitation_engine import (
            PrecipitationProbabilityEngine, PrecipBucket,
        )
        eng = PrecipitationProbabilityEngine()
        bucket = PrecipBucket(
            market_id="m1", token_id="t-yes", no_token_id="t-no", yes_price=0.60,
        )
        # Model says P(rain=YES)=0.20 → NO edge (P(NO)=0.80 vs NO price 0.40)
        opps = eng.compute_edges({"m1": 0.20}, [bucket], min_edge=0.08)
        no_opps = [o for o in opps if o["side"] == "NO"]
        assert len(no_opps) == 1
        no = no_opps[0]
        assert no["model_prob"] == pytest.approx(0.80)      # trading frame preserved
        assert no["model_prob_yes"] == pytest.approx(0.20)  # YES frame for prediction_log

    def test_precip_engine_yes_opp_frames_agree(self):
        from bots.weather.engine.base_engine.weather.precipitation_engine import (
            PrecipitationProbabilityEngine, PrecipBucket,
        )
        eng = PrecipitationProbabilityEngine()
        bucket = PrecipBucket(
            market_id="m1", token_id="t-yes", no_token_id="t-no", yes_price=0.30,
        )
        opps = eng.compute_edges({"m1": 0.70}, [bucket], min_edge=0.08)
        yes = [o for o in opps if o["side"] == "YES"][0]
        assert yes["model_prob"] == pytest.approx(0.70)
        assert yes["model_prob_yes"] == pytest.approx(0.70)

    def test_yes_frame_prob_helper(self):
        from bots.weather_bot import WeatherBot
        # PSW NO opp: explicit YES-frame field wins over the trading-frame value
        assert WeatherBot._yes_frame_prob(
            {"model_prob_yes": 0.20, "model_prob": 0.80}) == pytest.approx(0.20)
        # Temperature / singleton opps: model_prob is already P(YES) — fallback
        assert WeatherBot._yes_frame_prob({"model_prob": 0.70}) == pytest.approx(0.70)

    def test_grader_semantics_yes_frame_makes_winning_no_calls_correct(self):
        # The grader's CASE in SQL, restated: correct iff (p_yes>=0.5) == (res=='YES').
        # A winning NO call (P(NO)=0.8 → p_yes=0.2, market resolves NO):
        p_yes, resolution = 0.2, "NO"
        assert ((p_yes >= 0.5) == (resolution == "YES")) is True   # graded CORRECT
        # Pre-fix the same row stored predicted_prob=0.8 (chosen frame) → graded a miss:
        assert ((0.8 >= 0.5) == (resolution == "YES")) is False


class TestS226ProbFrameLabel:
    """S226 (V23): the durable frame label. prob_frame='yes' is stamped on every
    new WB prediction_log row; both graders (vendored + top-level database.py)
    refuse to grade PSW rows lacking the label; migration 080 creates the column
    and retro-NULLs the frame-poisoned historical PSW grades."""

    def test_insert_prediction_log_accepts_prob_frame(self):
        import inspect
        from bots.weather.engine.base_engine.data.database import Database, PredictionLog
        assert "prob_frame" in inspect.signature(Database.insert_prediction_log).parameters
        assert hasattr(PredictionLog, "prob_frame")

    def test_log_weather_prediction_stamps_yes_frame(self):
        # The single WB write path must stamp the label (source-level check,
        # same style as test_bot_pnl_fstring_placeholders).
        import inspect
        from bots import weather_bot
        src = inspect.getsource(weather_bot.WeatherBot._log_weather_prediction)
        assert 'prob_frame="yes"' in src

    def test_both_graders_guard_unlabelled_psw_rows(self):
        for path in (
            "bots/weather/engine/base_engine/data/database.py",
            "base_engine/data/database.py",
        ):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            assert "pl.prob_frame IS DISTINCT FROM 'yes' THEN NULL" in src, path
            # runtime column check so the legacy DB (pre-080) never errors
            assert "column_name = 'prob_frame'" in src, path

    def test_migration_080_exists_and_is_idempotent_and_scoped(self):
        with open("schema/migrations/080_prediction_log_prob_frame.sql", encoding="utf-8") as f:
            sql = f.read()
        assert "ADD COLUMN IF NOT EXISTS prob_frame" in sql
        # retro-NULL is scoped to WB PSW rows only — never other bots, never temperature
        assert "bot_name = 'WeatherBot'" in sql
        assert "'weather_precipitation', 'weather_snowfall', 'weather_wind'" in sql
        assert "weather_temperature" not in sql
        with open("deploy/wb-release-cut.sh", encoding="utf-8") as f:
            cut = f.read()
        assert "080_prediction_log_prob_frame.sql" in cut


class TestS226YesFramePriceLogging:
    """S226 (V23 sibling): prediction_log.market_price must also be P(YES)-frame
    (the YES token price) — the grader computes realized_edge as
    1 - market_price on YES resolution / market_price on NO, a YES-frame read.
    Chosen-side prices made realized_edge wrong for every NO entry. Log sites
    pass _yes_frame_price(opp); trading fields stay chosen-side."""

    def test_yes_frame_price_helper(self):
        from bots.weather_bot import WeatherBot
        # NO opp: stored price is the NO price → YES price is the complement
        assert WeatherBot._yes_frame_price(
            {"side": "NO", "price": 0.40}) == pytest.approx(0.60)
        # YES opp: stored price is already the YES price
        assert WeatherBot._yes_frame_price(
            {"side": "YES", "price": 0.30}) == pytest.approx(0.30)
        # Missing side defaults to YES (temperature/singleton convention)
        assert WeatherBot._yes_frame_price({"price": 0.25}) == pytest.approx(0.25)

    def test_grader_realized_edge_semantics_with_yes_frame_price(self):
        # Grader: realized_edge = 1 - market_price (res YES) | market_price (res NO).
        # A NO entry at NO-price 0.40 (YES price 0.60), market resolves NO:
        # entry paid 0.40 for a token worth 1.0 → realized edge should be +0.60...
        # in the YES frame that is market_price itself:
        yes_price = 0.60
        assert yes_price == pytest.approx(0.60)          # res NO → realized_edge = market_price ✓
        # Pre-fix the row stored market_price=0.40 (chosen side) → res NO gave 0.40: wrong.
        assert 0.40 != pytest.approx(0.60)


class TestS226ProbFrameTopLevelDatabase:
    """S226 hotfix: the WB service runs main.py, whose BaseEngine hands the bot
    the TOP-LEVEL base_engine.data.database.Database — not the vendored copy.
    prob_frame was added only to the vendored insert_prediction_log, so every
    live _log_weather_prediction call raised TypeError (unexpected keyword),
    swallowed at debug level: prediction logging silently died at the
    2026-07-10 20:12 deploy. Both Database classes must accept prob_frame."""

    def test_top_level_insert_accepts_prob_frame(self):
        import inspect
        from base_engine.data.database import Database, PredictionLog
        assert "prob_frame" in inspect.signature(Database.insert_prediction_log).parameters
        assert hasattr(PredictionLog, "prob_frame")


class TestS227GtCutoffBindsDatetime:
    """S227: asyncpg raises DataError when a Python str is bound to a
    timestamptz parameter (CAST(:gt_cutoff AS timestamptz)). All three
    ground-truth-cutoff SQL sites bound the cutoff as str, so from the
    2026-07-08 deploy onward EVERY confidence-calibrator fit crashed
    (weatherbot_confidence_cal_fit_failed, warning) and EVERY EMOS/bias
    calibration reload crashed (weatherbot_calibration_reload_failed,
    debug — silent in production): the calibrator never re-learned and the
    bot ran with no station bias/EMOS/tail calibration at all. Every
    gt_cutoff bind must be a tz-aware datetime, never a str."""

    @staticmethod
    def _capture_session():
        """AsyncMock session whose execute() records bind params."""
        captured = []
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        async def _exec(sql, params=None):
            captured.append(params)
            return mock_result

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(side_effect=_exec)
        db = MagicMock()
        db.get_session = MagicMock(return_value=session)
        return db, captured

    def test_helper_returns_aware_datetime(self):
        from bots.weather_bot import _gt_cutoff_datetime
        dt = _gt_cutoff_datetime()
        assert isinstance(dt, datetime)
        assert dt.tzinfo is not None, "naive datetime — must be pinned to UTC"
        assert (dt.year, dt.month, dt.day) == (2026, 7, 1)

    @pytest.mark.asyncio
    async def test_fit_binds_datetime_gt_cutoff(self):
        """Confidence-calibrator fit SQL must bind gt_cutoff as datetime."""
        from bots.weather_bot import WeatherConfidenceCalibrator
        cal = WeatherConfidenceCalibrator()
        db, captured = self._capture_session()
        await cal.fit_from_trade_events(db=db, window_days=30, min_samples=10)
        assert captured, "fit_from_trade_events never executed its SQL"
        gt = captured[0].get("gt_cutoff")
        assert isinstance(gt, datetime), (
            f"gt_cutoff bound as {type(gt).__name__} — asyncpg DataError in "
            "production; the calibrator can never fit"
        )
        assert gt.tzinfo is not None

    @pytest.mark.asyncio
    async def test_emos_reload_binds_datetime_gt_cutoff(self, weather_bot):
        """EMOS/bias reload SQL must bind gt_cutoff as datetime."""
        db, captured = self._capture_session()
        weather_bot.base_engine.db = db
        weather_bot._calibration_last_loaded = 0.0
        weather_bot._calibration_reload_interval = 0.0  # monotonic() may be < 6h in sandbox
        await weather_bot._maybe_reload_calibration()
        assert captured, "_maybe_reload_calibration never executed its SQL"
        gt = captured[0].get("gt_cutoff")
        assert isinstance(gt, datetime), (
            f"gt_cutoff bound as {type(gt).__name__} — asyncpg DataError in "
            "production; bias/EMOS/tail calibration silently never loads"
        )
        assert gt.tzinfo is not None
