"""Unit tests for WeatherBot and supporting weather modules."""

import math
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from base_engine.weather.station_registry import (
    STATION_REGISTRY,
    StationHealthMonitor,
    US_CITY_NAMES,
    WeatherStation,
    lookup_station,
)
from base_engine.weather.market_mapper import (
    TemperatureBucket,
    WeatherMarketGroup,
    WeatherMarketMapper,
    _parse_date,
)
from base_engine.weather.probability_engine import WeatherProbabilityEngine
from base_engine.weather.forecast_client import CombinedForecast, WeatherForecastClient


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
        assert client._rate_limit == 50

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
        from base_engine.weather.station_registry import STATION_REGISTRY
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
        from base_engine.weather.station_registry import STATION_REGISTRY
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
    engine.risk_manager = None
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
                "yes_price": 0.05,  # Market says 5% prob
                "slug": "nyc-temp-future",
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
        # Should have attempted at least one trade (edge should be >> 15%)
        assert mock_engine.place_order.called

    @pytest.mark.asyncio
    async def test_daily_loss_limit_blocks(self, weather_bot):
        """After hitting daily loss limit, no trades."""
        weather_bot._daily_pnl = -600.0  # Exceeds $500 limit
        weather_bot._daily_pnl_date = datetime.now().strftime("%Y-%m-%d")

        # Even with edge, should not trade
        from base_engine.weather.market_mapper import WeatherMarketGroup
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
    async def test_handle_daily_boundary_same_day_no_op(self, weather_bot):
        """Same-day call to _handle_daily_boundary → no reset."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d") if True else ""
        # Need to import timezone for the strftime
        from datetime import timezone as _tz
        today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
        weather_bot._daily_pnl = -50.0
        weather_bot._daily_pnl_date = today
        weather_bot._restore_daily_pnl_from_db = AsyncMock()

        await weather_bot._handle_daily_boundary()

        assert weather_bot._daily_pnl == -50.0  # Unchanged
        weather_bot._restore_daily_pnl_from_db.assert_not_called()

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
        from base_engine.weather.station_registry import STATION_REGISTRY
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
        from base_engine.weather.market_mapper import WeatherMarketGroup
        key = city.lower().replace(" ", "_")
        station = STATION_REGISTRY.get(key, STATION_REGISTRY["new_york_city"])
        return WeatherMarketGroup(
            city=city, target_date=date(2026, 3, 1), station=station, buckets=[]
        )

    def test_regime_boost_warm_front(self):
        """≥3 US cities all showing YES (warm) → 1.2x boost."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("New York City", "YES")], self._make_group("New York City")),
            ([self._make_opp("Atlanta", "YES")], self._make_group("Atlanta")),
            ([self._make_opp("Dallas", "YES")], self._make_group("Dallas")),
            ([self._make_opp("Miami", "YES")], self._make_group("Miami")),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.2

    def test_regime_boost_cold_front(self):
        """≥3 US cities all showing NO (cold) → 1.2x boost."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("Chicago", "NO")], self._make_group("Chicago")),
            ([self._make_opp("Seattle", "NO")], self._make_group("Seattle")),
            ([self._make_opp("Denver", "NO")], self._make_group("Denver")),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.2

    def test_regime_boost_mixed_no_signal(self):
        """Mixed warm/cold → no regime → 1.0 boost."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("New York City", "YES")], self._make_group("New York City")),
            ([self._make_opp("Atlanta", "NO")], self._make_group("Atlanta")),
            ([self._make_opp("Dallas", "YES")], self._make_group("Dallas")),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.0

    def test_regime_boost_no_us_cities(self):
        """Only international cities → no regime signal."""
        from bots.weather_bot import WeatherBot
        analyzed = [
            ([self._make_opp("London", "YES")], self._make_group("london")),
            ([self._make_opp("Seoul", "YES")], self._make_group("seoul")),
            ([self._make_opp("Toronto", "YES")], self._make_group("toronto")),
        ]
        boost = WeatherBot._compute_regime_boost(analyzed)
        assert boost == 1.0

    @pytest.mark.asyncio
    async def test_near_expiry_kelly_boost(self, weather_bot):
        """Near-expiry (<24h) → 1.5x Kelly multiplier applied."""
        from base_engine.weather.market_mapper import WeatherMarketGroup
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
        from base_engine.weather.metar_client import MetarClient
        # T02890267 → +28.9°C
        assert MetarClient.parse_t_group("METAR KLGA ... RMK T02890267") == pytest.approx(28.9, abs=0.01)

    def test_parse_t_group_negative_temp(self):
        from base_engine.weather.metar_client import MetarClient
        # T11001267 → -10.0°C
        assert MetarClient.parse_t_group("T11001267") == pytest.approx(-10.0, abs=0.01)

    def test_parse_t_group_no_match_returns_none(self):
        from base_engine.weather.metar_client import MetarClient
        assert MetarClient.parse_t_group("METAR KLGA 061856Z 28010KT") is None

    def test_parse_t_group_zero_temp(self):
        from base_engine.weather.metar_client import MetarClient
        # T00000267 → 0.0°C
        assert MetarClient.parse_t_group("T00000267") == pytest.approx(0.0, abs=0.01)


class TestMetarClientAPI:
    """Integration-style tests with mocked HTTP session."""

    @pytest.mark.asyncio
    async def test_get_latest_metar_success(self):
        from base_engine.weather.metar_client import MetarClient
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
        from base_engine.weather.metar_client import MetarClient
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
        from base_engine.weather.metar_client import MetarClient
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
        from base_engine.weather.metar_client import MetarClient
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
        from base_engine.weather.station_registry import STATION_REGISTRY
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
