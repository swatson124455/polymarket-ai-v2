"""Unit tests for WeatherBot city auto-discovery (runtime geocoding).

Covers bots/weather/engine/base_engine/weather/city_autodiscovery.py::try_auto_register.

These tests close the 0%-coverage gap that let TWO S236 (2026-05-31) bugs ship undetected:

  Bug 1 (e20f007): the gate read ``top.get("score", 0.0)`` and required score >= 0.8, but
    the Open-Meteo geocoding API returns NO "score" field — so every city defaulted to 0.0,
    failed the gate, and NOTHING was ever auto-registered since the feature shipped.

  Bug 2 (b8c3f65): after the gate was repaired, the INSERT path still referenced the deleted
    ``top_score`` variable (``confidence = round(top_score, 4)``) → NameError at runtime for
    any city that passed the gate.

Both bugs live on the SUCCESS path, so the keystone test
(``test_valid_city_registers_despite_missing_score_field``) catches both at once: a realistic
no-"score" response must register successfully, which (1) requires the structural gate to pass
without a score and (2) requires the INSERT path to run without NameError.

Mock idioms (aiohttp nested async-CM, get_session()/execute()/scalar_one_or_none chain) mirror
the established patterns in test_weather_bot.py (TestStationHealthMonitor, ~L1037 / ~L1543).
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from bots.weather.engine.base_engine.weather import city_autodiscovery as cad

# Where try_auto_register imports register_dynamic_station from (function-local import at call time).
_REGISTER_TARGET = (
    "bots.weather.engine.base_engine.weather.station_registry.register_dynamic_station"
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_registered_cache():
    """The module-level _registered_cache persists across tests; reset it around each one."""
    cad._registered_cache.clear()
    yield
    cad._registered_cache.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _geocode_result(**overrides):
    """A realistic Open-Meteo geocoding result for Busan.

    Mirrors the REAL API shape — note there is deliberately NO "score" key, because the
    Open-Meteo geocoding endpoint does not return one. That omission is the exact condition
    that triggered Bug 1.
    """
    base = {
        "id": 1838524,
        "name": "Busan",
        "latitude": 35.10168,
        "longitude": 129.03004,
        "feature_code": "PPLA",
        "country_code": "KR",
        "timezone": "Asia/Seoul",
        "population": 3678555,
        "country": "South Korea",
    }
    base.update(overrides)
    return base


def _geocode_payload(*results):
    return {"results": list(results), "generationtime_ms": 0.5}


def _patch_geocode(payload, status=200):
    """Patch aiohttp.ClientSession to serve `payload` through the nested async-CM call.

    Matches the established idiom in test_weather_bot.py::TestStationHealthMonitor:
        async with aiohttp.ClientSession() as http:
            async with http.get(...) as resp:
                resp.status / await resp.json()
    """
    mock_resp = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=payload)

    mock_sess = MagicMock()
    mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_sess.__aexit__ = AsyncMock(return_value=False)
    mock_sess.get = MagicMock(return_value=mock_resp)

    return patch("aiohttp.ClientSession", return_value=mock_sess)


def _patch_geocode_raises(exc):
    """Patch aiohttp.ClientSession so the geocode request raises `exc` (transport failure)."""
    mock_sess = MagicMock()
    mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_sess.__aexit__ = AsyncMock(return_value=False)
    mock_sess.get = MagicMock(side_effect=exc)
    return patch("aiohttp.ClientSession", return_value=mock_sess)


def _make_db(existing_row=None):
    """Mock PolymarketDatabase whose existence-check SELECT returns `existing_row`.

    existing_row=None  → station not yet in dynamic_stations (proceed to geocode/insert)
    existing_row=<key> → station already registered (early True)

    Returns (db, session) so tests can assert on commit()/execute().
    """
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=existing_row)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_db = MagicMock()
    mock_db.session_factory = MagicMock()  # non-None so the availability guard passes
    mock_db.get_session = MagicMock(return_value=mock_session)
    return mock_db, mock_session


# ── Keystone: the success path that both S236 bugs broke ──────────────────────

class TestAutoRegisterSuccessPath:
    @pytest.mark.asyncio
    async def test_valid_city_registers_despite_missing_score_field(self):
        """KEYSTONE — guards BOTH S236 bugs.

        A realistic Open-Meteo response (no "score" key) for a real, populated city must
        register. Old code (Bug 1) rejected it via score<0.8; the regression (Bug 2) raised
        NameError on the INSERT path. Either failure breaks this test.
        """
        payload = _geocode_payload(_geocode_result())
        assert "score" not in payload["results"][0]  # documents the Bug-1 trigger condition

        db, session = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET) as reg:
            result = await cad.try_auto_register("Busan", db)

        assert result is True
        reg.assert_called_once()              # in-process registry populated for same-scan trading
        session.commit.assert_awaited_once()  # DB INSERT committed

        # The INSERT must carry confidence=1.0 (Bug 2 fix: was round(top_score,4) → NameError).
        insert_params = session.execute.call_args_list[-1].args[1]
        assert insert_params["conf"] == 1.0
        assert insert_params["key"] == "busan"
        assert insert_params["city"] == "Busan"

    @pytest.mark.asyncio
    async def test_registers_when_same_name_rival_is_dominated(self):
        """Negative control for the ambiguity guard: a same-name rival that the top clearly
        dominates (rival_pop * 2 <= top_pop) must NOT trip ambiguity → city still registers."""
        payload = _geocode_payload(
            _geocode_result(population=3678555),
            _geocode_result(name="Busan", population=1000, feature_code="PPL", country_code="KP"),
        )
        db, session = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET):
            result = await cad.try_auto_register("Busan", db)

        assert result is True
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_us_city_registers_with_fahrenheit_unit(self):
        """A US populated place registers with temp_unit='F' carried into the INSERT."""
        payload = _geocode_payload(
            _geocode_result(name="Chicago", country_code="US", timezone="America/Chicago",
                            population=2746388, latitude=41.85003, longitude=-87.65005)
        )
        db, session = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET):
            result = await cad.try_auto_register("Chicago", db)

        assert result is True
        insert_params = session.execute.call_args_list[-1].args[1]
        assert insert_params["unit"] == "F"
        assert insert_params["key"] == "chicago"


# ── Gate rejections (each isolates one rejection cause) ───────────────────────

class TestAutoRegisterGateRejections:
    @pytest.mark.asyncio
    async def test_name_mismatch_rejected(self):
        """Top result name doesn't match the query → low confidence → no registration."""
        payload = _geocode_payload(_geocode_result(name="Seoul"))  # queried "Busan"
        db, session = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET) as reg:
            result = await cad.try_auto_register("Busan", db)

        assert result is False
        reg.assert_not_called()
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_populated_place_rejected(self):
        """feature_code not starting with PPL (e.g. an administrative region) → rejected."""
        payload = _geocode_payload(_geocode_result(feature_code="ADM1"))
        db, session = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET):
            result = await cad.try_auto_register("Busan", db)

        assert result is False
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_below_population_floor_rejected(self):
        """Population below _MIN_POPULATION → geocoding noise / tiny place → rejected."""
        payload = _geocode_payload(_geocode_result(population=cad._MIN_POPULATION - 1))
        db, _ = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET):
            result = await cad.try_auto_register("Busan", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_missing_population_treated_as_zero_rejected(self):
        """population present but None → int(None or 0) == 0 → below floor → rejected (no crash)."""
        payload = _geocode_payload(_geocode_result(population=None))
        db, _ = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET):
            result = await cad.try_auto_register("Busan", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_nonnumeric_population_rejected(self):
        """population is a non-numeric value → int() raises ValueError, caught → 0 → rejected."""
        payload = _geocode_payload(_geocode_result(population="not-a-number"))
        db, _ = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET):
            result = await cad.try_auto_register("Busan", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_ambiguous_same_name_rival_rejected(self):
        """Two real cities share the name and the rival rivals the top by population → rejected.

        Isolates the ambiguity guard: name matches, feature is PPL, population is above the
        floor, so the ONLY rejection cause is ambiguity (rival_pop * 2 > top_pop)."""
        payload = _geocode_payload(
            _geocode_result(name="Springfield", population=120000),
            _geocode_result(name="Springfield", population=110000, country_code="US"),
        )
        db, _ = _make_db(existing_row=None)
        with _patch_geocode(payload), patch(_REGISTER_TARGET) as reg:
            result = await cad.try_auto_register("Springfield", db)

        assert result is False
        reg.assert_not_called()


# ── Pre-geocode short circuits ────────────────────────────────────────────────

class TestAutoRegisterShortCircuits:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_true_without_geocoding(self):
        """A fresh _registered_cache entry short-circuits before any DB or API call."""
        cad._registered_cache["busan"] = time.monotonic()
        db, session = _make_db(existing_row=None)
        with patch("aiohttp.ClientSession", side_effect=AssertionError("geocoded on cache hit")):
            result = await cad.try_auto_register("Busan", db)

        assert result is True
        session.execute.assert_not_awaited()  # DB not touched on a cache hit either

    @pytest.mark.asyncio
    async def test_stale_cache_entry_does_not_short_circuit(self):
        """An entry older than _CACHE_TTL must NOT be treated as a hit → proceeds to DB check."""
        cad._registered_cache["busan"] = time.monotonic() - (cad._CACHE_TTL + 1.0)
        db, session = _make_db(existing_row="busan")  # DB says already-registered → True
        with patch("aiohttp.ClientSession", side_effect=AssertionError("should hit DB, not geocode")):
            result = await cad.try_auto_register("Busan", db)

        assert result is True
        session.execute.assert_awaited()  # the stale entry forced the DB existence check

    @pytest.mark.asyncio
    async def test_already_in_db_returns_true_and_caches(self):
        """Station already present in dynamic_stations → early True, cached, no geocode/insert."""
        db, session = _make_db(existing_row="busan")
        with patch("aiohttp.ClientSession", side_effect=AssertionError("geocoded despite DB hit")):
            result = await cad.try_auto_register("Busan", db)

        assert result is True
        assert "busan" in cad._registered_cache
        session.commit.assert_not_awaited()  # no INSERT — it already existed

    @pytest.mark.asyncio
    async def test_db_none_returns_false(self):
        """No DB handle → cannot register → False (and no crash)."""
        result = await cad.try_auto_register("Busan", None)
        assert result is False

    @pytest.mark.asyncio
    async def test_session_factory_none_returns_false(self):
        """DB present but not initialised (session_factory is None) → False."""
        db = MagicMock()
        db.session_factory = None
        result = await cad.try_auto_register("Busan", db)
        assert result is False


# ── Geocode IO failures ───────────────────────────────────────────────────────

class TestAutoRegisterGeocodeFailures:
    @pytest.mark.asyncio
    async def test_no_results_returns_false(self):
        """Empty geocoding results → False (caller fires manual alert)."""
        db, _ = _make_db(existing_row=None)
        with _patch_geocode(_geocode_payload()):  # results: []
            result = await cad.try_auto_register("Nowheresville", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_missing_results_key_returns_false(self):
        """Response without a 'results' key → data.get('results') or [] → [] → False."""
        db, _ = _make_db(existing_row=None)
        with _patch_geocode({"generationtime_ms": 0.1}):
            result = await cad.try_auto_register("Busan", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        """Non-200 geocoding response → False before any JSON parse."""
        db, _ = _make_db(existing_row=None)
        with _patch_geocode(_geocode_payload(_geocode_result()), status=503):
            result = await cad.try_auto_register("Busan", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        """A transport-level failure (aiohttp.ClientError) is caught → False (no crash)."""
        db, _ = _make_db(existing_row=None)
        with _patch_geocode_raises(aiohttp.ClientError("connection reset")):
            result = await cad.try_auto_register("Busan", db)

        assert result is False


# ── DB error handling ─────────────────────────────────────────────────────────

class TestAutoRegisterDbErrors:
    @pytest.mark.asyncio
    async def test_db_check_failure_returns_false(self):
        """An exception during the existence-check SELECT is caught → False, no geocode attempt."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db unavailable"))
        db = MagicMock()
        db.session_factory = MagicMock()
        db.get_session = MagicMock(return_value=mock_session)

        with patch("aiohttp.ClientSession", side_effect=AssertionError("geocoded despite DB error")):
            result = await cad.try_auto_register("Busan", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_insert_failure_returns_false_without_registering(self):
        """If the INSERT raises, return False and leave NO phantom in-process registration
        (register_dynamic_station not called, cache not populated) — DB and memory stay consistent."""
        select_result = MagicMock()
        select_result.scalar_one_or_none = MagicMock(return_value=None)  # not yet in DB
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # 1st execute = existence SELECT (ok); 2nd execute = INSERT (raises)
        mock_session.execute = AsyncMock(side_effect=[select_result, RuntimeError("insert failed")])
        mock_session.commit = AsyncMock()
        db = MagicMock()
        db.session_factory = MagicMock()
        db.get_session = MagicMock(return_value=mock_session)

        with _patch_geocode(_geocode_payload(_geocode_result())), patch(_REGISTER_TARGET) as reg:
            result = await cad.try_auto_register("Busan", db)

        assert result is False
        reg.assert_not_called()                       # no phantom in-process station
        assert "busan" not in cad._registered_cache   # cache not populated on failed insert
        mock_session.commit.assert_not_awaited()


# ── Pure helpers ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_to_station_key_normalises_spaces_and_hyphens(self):
        assert cad._to_station_key("Cape Town") == "cape_town"
        assert cad._to_station_key("Panama City") == "panama_city"
        assert cad._to_station_key("  Busan  ") == "busan"
        assert cad._to_station_key("Foo-Bar") == "foo_bar"

    def test_derive_temp_unit_fahrenheit_countries(self):
        assert cad._derive_temp_unit("US") == "F"
        assert cad._derive_temp_unit("us") == "F"  # case-insensitive
        assert cad._derive_temp_unit("PR") == "F"

    def test_derive_temp_unit_defaults_to_celsius(self):
        assert cad._derive_temp_unit("KR") == "C"
        assert cad._derive_temp_unit("") == "C"
