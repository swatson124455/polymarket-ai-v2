"""
WeatherBot city auto-discovery — runtime geocoding for unmatched cities.

When a city appears in a Polymarket weather market but is not in the static
station_registry, this module:
  1. Calls the Open-Meteo geocoding API (free, no key required).
  2. Scores confidence: top result score ≥ 0.8 AND top-2 gap > 0.2.
  3. High confidence → insert into `dynamic_stations` DB table.
  4. Low confidence → return None (caller fires manual alert).

The dynamic_stations table is then checked by lookup_station() as a fallback
after the static registry misses.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, TYPE_CHECKING

import aiohttp
from structlog import get_logger

if TYPE_CHECKING:
    from base_engine.data.database import PolymarketDatabase

logger = get_logger()

# Confidence thresholds for auto-registration
_CONF_THRESHOLD = 0.8       # minimum score for top result
_CONF_GAP = 0.2             # minimum gap between top-2 scores (ambiguity check)

# Cache: station_key → monotonic time of last insert (avoids repeated geocode calls)
_registered_cache: dict[str, float] = {}
_CACHE_TTL = 300.0  # 5 minutes

# Countries that use Fahrenheit (ISO alpha-2)
_FAHRENHEIT_COUNTRIES = frozenset({
    "US", "PR", "GU", "VI", "AS", "MH", "FM", "PW", "MQ",
})

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _derive_temp_unit(country_code: str) -> str:
    return "F" if country_code.upper() in _FAHRENHEIT_COUNTRIES else "C"


def _to_station_key(city_name: str) -> str:
    """Normalise city name to registry key format: lower, spaces→underscores."""
    return city_name.strip().lower().replace(" ", "_").replace("-", "_")


async def try_auto_register(city_text: str, db: "PolymarketDatabase") -> bool:
    """
    Attempt to auto-register an unmatched city via Open-Meteo geocoding.

    Returns True if the city was successfully registered (or was already cached).
    Returns False if confidence is too low — caller should fire manual alert.
    """
    station_key = _to_station_key(city_text)

    # Check in-process cache first (avoids DB + API on every scan)
    cached_at = _registered_cache.get(station_key)
    if cached_at is not None and (time.monotonic() - cached_at) < _CACHE_TTL:
        return True

    # Skip if DB is not available
    if db is None or db.session_factory is None:
        return False

    # Check DB — another instance may have already registered it
    try:
        from sqlalchemy import text as _sa_text
        async with db.get_session() as sess:
            row = await sess.execute(
                _sa_text(
                    "SELECT station_key FROM dynamic_stations WHERE station_key = :key"
                ),
                {"key": station_key},
            )
            if row.scalar_one_or_none() is not None:
                _registered_cache[station_key] = time.monotonic()
                return True
    except Exception as exc:
        logger.warning("city_autodiscovery_db_check_failed", city=city_text, error=str(exc))
        return False

    # Call Open-Meteo geocoding API
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as http:
            async with http.get(
                _GEOCODE_URL,
                params={"name": city_text, "count": "3", "language": "en", "format": "json"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "city_autodiscovery_geocode_http_error",
                        city=city_text,
                        status=resp.status,
                    )
                    return False
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("city_autodiscovery_geocode_timeout", city=city_text, error=str(exc))
        return False

    results = data.get("results") or []
    if not results:
        logger.warning("city_autodiscovery_no_results", city=city_text)
        return False

    top = results[0]
    top_score: float = float(top.get("score", 0.0))

    # Ambiguity check: require a clear gap between top-2 results
    if len(results) >= 2:
        second_score: float = float(results[1].get("score", 0.0))
        gap = top_score - second_score
    else:
        gap = top_score  # only one result → no ambiguity

    if top_score < _CONF_THRESHOLD or gap < _CONF_GAP:
        logger.warning(
            "city_autodiscovery_low_confidence",
            city=city_text,
            top_score=round(top_score, 3),
            gap=round(gap, 3),
            threshold=_CONF_THRESHOLD,
            required_gap=_CONF_GAP,
        )
        return False

    # High confidence — derive station metadata
    city_name: str = top.get("name", city_text)
    latitude: float = float(top["latitude"])
    longitude: float = float(top["longitude"])
    timezone: str = top.get("timezone", "UTC")
    country_code: str = top.get("country_code", "")
    temp_unit: str = _derive_temp_unit(country_code)
    confidence: float = round(top_score, 4)
    aliases = [station_key]  # at minimum, the normalised key

    # Insert into dynamic_stations
    try:
        from sqlalchemy import text as _sa_text
        async with db.get_session() as sess:
            await sess.execute(
                _sa_text("""
                    INSERT INTO dynamic_stations
                        (station_key, city_name, latitude, longitude, timezone,
                         temp_unit, aliases, icao, confidence, source)
                    VALUES
                        (:key, :city, :lat, :lon, :tz,
                         :unit, :aliases, NULL, :conf, 'open-meteo-geocoding')
                    ON CONFLICT (station_key) DO NOTHING
                """),
                {
                    "key": station_key,
                    "city": city_name,
                    "lat": latitude,
                    "lon": longitude,
                    "tz": timezone,
                    "unit": temp_unit,
                    "aliases": aliases,
                    "conf": confidence,
                },
            )
            await sess.commit()
    except Exception as exc:
        logger.error(
            "city_autodiscovery_db_insert_failed",
            city=city_text,
            station_key=station_key,
            error=str(exc),
        )
        return False

    _registered_cache[station_key] = time.monotonic()

    # Populate in-process registry immediately so the SAME scan cycle can trade
    from base_engine.weather.station_registry import register_dynamic_station
    register_dynamic_station(
        station_key=station_key,
        city_name=city_name,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        temp_unit=temp_unit,
        aliases=aliases,
    )

    logger.warning(
        "weatherbot_city_autodiscovered",
        city=city_name,
        station_key=station_key,
        lat=latitude,
        lon=longitude,
        timezone=timezone,
        temp_unit=temp_unit,
        confidence=confidence,
        note="Dynamic station added. Open-Meteo forecasts will be used; no ICAO/WU resolution source wired.",
    )
    return True
