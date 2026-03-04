"""Unit tests for WeatherBot and supporting weather modules."""

import math
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from base_engine.weather.station_registry import (
    STATION_REGISTRY,
    StationHealthMonitor,
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
    def test_registry_has_all_major_cities(self):
        assert "new_york_city" in STATION_REGISTRY
        assert "london" in STATION_REGISTRY
        assert "toronto" in STATION_REGISTRY
        assert "seoul" in STATION_REGISTRY
        assert "buenos_aires" in STATION_REGISTRY
        assert "atlanta" in STATION_REGISTRY
        assert "seattle" in STATION_REGISTRY
        assert "dallas" in STATION_REGISTRY
        assert "miami" in STATION_REGISTRY
        assert "chicago" in STATION_REGISTRY
        assert "denver" in STATION_REGISTRY

    def test_lookup_nyc_aliases(self):
        assert lookup_station("nyc") is not None
        assert lookup_station("new york city") is not None
        assert lookup_station("new york") is not None
        assert lookup_station("NYC").station_id == "KLGA"

    def test_lookup_london(self):
        s = lookup_station("London")
        assert s is not None
        assert s.station_id == "EGLC"
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
        for key in ["new_york_city", "atlanta", "seattle", "dallas", "miami", "chicago", "denver"]:
            assert STATION_REGISTRY[key].temp_unit == "F"

    def test_international_cities_use_celsius(self):
        for key in ["london", "toronto", "seoul", "buenos_aires", "wellington", "ankara"]:
            assert STATION_REGISTRY[key].temp_unit == "C"

    def test_station_has_coordinates(self):
        for station in STATION_REGISTRY.values():
            assert -90 <= station.latitude <= 90
            assert -180 <= station.longitude <= 180


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

    def test_lead_time_inflates_uncertainty(self):
        members = [50.0] * 31
        loc_short, scale_short, _ = self.engine.fit_distribution(members, lead_time_hours=6.0)
        loc_long, scale_long, _ = self.engine.fit_distribution(members, lead_time_hours=72.0)
        # Longer lead time → wider scale
        assert scale_long > scale_short

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
        """P5: get_ensemble_forecast fetches GEFS + ECMWF in parallel."""
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
        # Simulate ECMWF response with 2 members
        ecmwf_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [47.5],
                "temperature_2m_max_member01": [48.5],
            }
        }

        with patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [gefs_resp, ecmwf_resp]
            merged = await client.get_ensemble_forecast(40.77, -73.87, temp_unit="F")

        assert merged is not None
        daily = merged["daily"]
        # GEFS: member00..02, ECMWF offset: member03..04
        assert "temperature_2m_max_member00" in daily
        assert "temperature_2m_max_member03" in daily  # ECMWF offset
        assert daily["temperature_2m_max_member03"] == [47.5]
        assert daily["temperature_2m_max_member04"] == [48.5]

    @pytest.mark.asyncio
    async def test_ensemble_falls_back_to_gefs_on_ecmwf_failure(self):
        """P5: If ECMWF fails, still return GEFS-only result."""
        client = WeatherForecastClient()

        gefs_resp = {
            "daily": {
                "time": ["2026-03-01"],
                "temperature_2m_max_member00": [48.0],
            }
        }

        with patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [gefs_resp, Exception("ECMWF timeout")]
            merged = await client.get_ensemble_forecast(40.77, -73.87)

        assert merged is not None
        assert "temperature_2m_max_member00" in merged["daily"]

    @pytest.mark.asyncio
    async def test_combined_forecast_uses_all_members(self):
        """P5: Combined forecast incorporates ECMWF members in ensemble list."""
        from base_engine.weather.station_registry import STATION_REGISTRY
        client = WeatherForecastClient()
        station = STATION_REGISTRY["new_york_city"]

        # GEFS: 3 members, ECMWF: 2 members → 5 total
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
             patch.object(client, "_fetch_ensemble_model", new_callable=AsyncMock, side_effect=[gefs_resp, ecmwf_resp]):
            fc = await client.get_combined_forecast(station, date(2026, 3, 10))

        assert fc is not None
        assert len(fc.ensemble_members) == 5  # 3 GEFS + 2 ECMWF


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
