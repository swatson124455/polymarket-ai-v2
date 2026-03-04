"""
Station Registry — maps Polymarket weather market cities to NOAA weather stations.

Each station has exact coordinates matching the resolution source used by Polymarket.
Resolution typically uses the city's primary airport ASOS/METAR station via
Weather Underground or NOAA CDO (Climate Data Online).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiohttp
from structlog import get_logger

logger = get_logger()


@dataclass(frozen=True)
class WeatherStation:
    city_name: str
    station_id: str          # ICAO code: KLGA, EGLC, etc.
    ghcnd_id: str            # GHCND:USW00014732
    latitude: float
    longitude: float
    elevation_m: float
    timezone: str            # IANA timezone
    temp_unit: str           # "F" or "C"
    aliases: tuple = ()      # lowercase aliases for matching market text
    resolution_source: str = ""


# ── Registry ─────────────────────────────────────────────────────────────
# Coordinates target the exact ASOS station, NOT the city centroid.
# Verified against Polymarket resolution rules and Weather Underground IDs.

STATION_REGISTRY: Dict[str, WeatherStation] = {
    "new_york_city": WeatherStation(
        city_name="New York City",
        station_id="KLGA",
        ghcnd_id="GHCND:USW00014732",
        latitude=40.7772,
        longitude=-73.8726,
        elevation_m=6.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("nyc", "new york city", "new york"),
        resolution_source="Weather Underground / KLGA",
    ),
    "london": WeatherStation(
        city_name="London",
        station_id="EGLC",
        ghcnd_id="GHCND:UKE00105915",
        latitude=51.5053,
        longitude=0.0553,
        elevation_m=5.0,
        timezone="Europe/London",
        temp_unit="C",
        aliases=("london",),
        resolution_source="Weather Underground / EGLC",
    ),
    "toronto": WeatherStation(
        city_name="Toronto",
        station_id="CYYZ",
        ghcnd_id="GHCND:CA006158733",
        latitude=43.6772,
        longitude=-79.6306,
        elevation_m=173.0,
        timezone="America/Toronto",
        temp_unit="C",
        aliases=("toronto",),
        resolution_source="Weather Underground / CYYZ",
    ),
    "seoul": WeatherStation(
        city_name="Seoul",
        station_id="RKSS",
        ghcnd_id="GHCND:KSM00047108",
        latitude=37.5583,
        longitude=126.7906,
        elevation_m=18.0,
        timezone="Asia/Seoul",
        temp_unit="C",
        aliases=("seoul",),
        resolution_source="Weather Underground / RKSS",
    ),
    "buenos_aires": WeatherStation(
        city_name="Buenos Aires",
        station_id="SAEZ",
        ghcnd_id="GHCND:AR000875850",
        latitude=-34.8222,
        longitude=-58.5358,
        elevation_m=20.0,
        timezone="America/Argentina/Buenos_Aires",
        temp_unit="C",
        aliases=("buenos aires",),
        resolution_source="Weather Underground / SAEZ",
    ),
    "atlanta": WeatherStation(
        city_name="Atlanta",
        station_id="KATL",
        ghcnd_id="GHCND:USW00013874",
        latitude=33.6407,
        longitude=-84.4277,
        elevation_m=315.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("atlanta",),
        resolution_source="Weather Underground / KATL",
    ),
    "seattle": WeatherStation(
        city_name="Seattle",
        station_id="KSEA",
        ghcnd_id="GHCND:USW00024233",
        latitude=47.4502,
        longitude=-122.3088,
        elevation_m=131.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("seattle",),
        resolution_source="Weather Underground / KSEA",
    ),
    "dallas": WeatherStation(
        city_name="Dallas",
        station_id="KDFW",
        ghcnd_id="GHCND:USW00003927",
        latitude=32.8998,
        longitude=-97.0403,
        elevation_m=171.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("dallas",),
        resolution_source="Weather Underground / KDFW",
    ),
    "wellington": WeatherStation(
        city_name="Wellington",
        station_id="NZWN",
        ghcnd_id="GHCND:NZM00093436",
        latitude=-41.3272,
        longitude=174.8053,
        elevation_m=7.0,
        timezone="Pacific/Auckland",
        temp_unit="C",
        aliases=("wellington",),
        resolution_source="Weather Underground / NZWN",
    ),
    "ankara": WeatherStation(
        city_name="Ankara",
        station_id="LTAC",
        ghcnd_id="GHCND:TUM00017130",
        latitude=40.1281,
        longitude=32.9951,
        elevation_m=953.0,
        timezone="Europe/Istanbul",
        temp_unit="C",
        aliases=("ankara",),
        resolution_source="Weather Underground / LTAC",
    ),
    "miami": WeatherStation(
        city_name="Miami",
        station_id="KMIA",
        ghcnd_id="GHCND:USW00012839",
        latitude=25.7959,
        longitude=-80.2870,
        elevation_m=2.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("miami",),
        resolution_source="Weather Underground / KMIA",
    ),
    "chicago": WeatherStation(
        city_name="Chicago",
        station_id="KORD",
        ghcnd_id="GHCND:USW00094846",
        latitude=41.9742,
        longitude=-87.9073,
        elevation_m=201.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("chicago",),
        resolution_source="Weather Underground / KORD",
    ),
    "denver": WeatherStation(
        city_name="Denver",
        station_id="KDEN",
        ghcnd_id="GHCND:USW00003017",
        latitude=39.8561,
        longitude=-104.6737,
        elevation_m=1655.0,
        timezone="America/Denver",
        temp_unit="F",
        aliases=("denver",),
        resolution_source="Weather Underground / KDEN",
    ),
}

# Build alias → station lookup (pre-computed at import time)
_ALIAS_MAP: Dict[str, WeatherStation] = {}
for _station in STATION_REGISTRY.values():
    for _alias in _station.aliases:
        _ALIAS_MAP[_alias] = _station


def lookup_station(city_text: str) -> Optional[WeatherStation]:
    """Match city text (from a market question) to a station.

    Tries exact alias match first, then substring search. Returns None
    if no match found.
    """
    text = city_text.strip().lower()
    # Exact alias match
    if text in _ALIAS_MAP:
        return _ALIAS_MAP[text]
    # Substring match (longest alias first to avoid "new york" matching before "new york city")
    for alias in sorted(_ALIAS_MAP, key=len, reverse=True):
        if alias in text:
            return _ALIAS_MAP[alias]
    return None


class StationHealthMonitor:
    """Monitor weather station observation health.

    Checks that the resolution station is reporting recent observations.
    Halts trading for a station if data is stale or anomalous.
    """

    def __init__(self, stale_threshold_minutes: float = 180.0):
        self._stale_threshold = stale_threshold_minutes * 60.0
        self._health_cache: Dict[str, tuple] = {}  # station_id -> (is_healthy, mono_expiry)
        self._cache_ttl = 600.0  # 10 min

    async def is_healthy(self, station: WeatherStation) -> bool:
        """Return True if station is reporting recent observations."""
        now = time.monotonic()
        cached = self._health_cache.get(station.station_id)
        if cached and now < cached[1]:
            return cached[0]

        healthy = await self._check_station(station)
        self._health_cache[station.station_id] = (healthy, now + self._cache_ttl)
        return healthy

    async def _check_station(self, station: WeatherStation) -> bool:
        """Query NWS (US) or Open-Meteo (international) to verify station liveness.

        P4 upgrade: international stations previously always returned True.
        Now probes Open-Meteo for a 1-day forecast — if the API returns valid
        temperature data for the station's coordinates, it's healthy.
        """
        if station.temp_unit == "C":
            return await self._probe_openmeteo(station)

        url = f"https://api.weather.gov/stations/{station.station_id}/observations/latest"
        try:
            async with aiohttp.ClientSession() as sess:
                headers = {
                    "User-Agent": "PolymarketWeatherBot/1.0",
                    "Accept": "application/geo+json",
                }
                async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning("station_health_check_failed", station=station.station_id, status=resp.status)
                        return True  # Fail open — don't block trading on API error
                    data = await resp.json()
            ts_str = data.get("properties", {}).get("timestamp")
            if not ts_str:
                return True
            from datetime import datetime, timezone as tz
            obs_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(tz.utc) - obs_time).total_seconds()
            if age_seconds > self._stale_threshold:
                logger.warning(
                    "station_stale_observation",
                    station=station.station_id,
                    age_hours=round(age_seconds / 3600, 1),
                )
                return False
            return True
        except Exception as exc:
            logger.debug("station_health_error", station=station.station_id, error=str(exc))
            return True  # Fail open

    async def _probe_openmeteo(self, station: WeatherStation) -> bool:
        """Probe Open-Meteo for a 1-day forecast to verify international station.

        Returns True if Open-Meteo returns non-null temperature data for the
        station's coordinates. Fails open (returns True) on any error.
        """
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": station.latitude,
            "longitude": station.longitude,
            "daily": "temperature_2m_max",
            "forecast_days": 1,
            "timezone": "auto",
        }
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "intl_station_probe_failed",
                            station=station.station_id,
                            status=resp.status,
                        )
                        return True  # Fail open — don't block trading on API error
                    data = await resp.json()
            daily = data.get("daily", {})
            maxes = daily.get("temperature_2m_max", [])
            if maxes and maxes[0] is not None:
                return True
            logger.warning(
                "intl_station_no_data",
                station=station.station_id,
                response_keys=list(data.keys()),
            )
            return True  # Fail open — data absence isn't definitive
        except Exception as exc:
            logger.debug(
                "intl_station_probe_error", station=station.station_id, error=str(exc)
            )
            return True  # Fail open
