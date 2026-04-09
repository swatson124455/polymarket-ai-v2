#!/usr/bin/env python3
"""
WeatherBot city onboarding helper — generates ready-to-paste WeatherStation() blocks.

Usage:
    python scripts/onboard_weather_cities.py --cities "Riyadh,Springfield,Cape Town"

For each city:
1. Calls Open-Meteo geocoding API — returns lat/lon/timezone/country.
2. Finds the nearest known ICAO station from the existing static registry
   (as a starting-point suggestion — human must verify against Weather Underground).
3. Prints a WeatherStation() block ready to paste into station_registry.py.

This script makes NO DB writes and does NOT modify any code. It is a read-only
codegen helper for the human operator to review before deploying.

Requires: pip install aiohttp  (already a project dependency)
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from typing import Optional

import aiohttp

# ---------------------------------------------------------------------------
# Fahrenheit countries
# ---------------------------------------------------------------------------
_FAHRENHEIT_COUNTRIES = frozenset({
    "US", "PR", "GU", "VI", "AS", "MH", "FM", "PW", "MQ",
})


def _derive_temp_unit(country_code: str) -> str:
    return "F" if country_code.upper() in _FAHRENHEIT_COUNTRIES else "C"


def _to_station_key(city_name: str) -> str:
    return city_name.strip().lower().replace(" ", "_").replace("-", "_")


# ---------------------------------------------------------------------------
# Nearest ICAO from existing static registry
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _nearest_icao_from_registry(lat: float, lon: float) -> tuple[str, float]:
    """Return (icao_code, distance_km) of closest station in static registry."""
    # Import here to avoid circular issues at module level
    sys.path.insert(0, str(__file__).replace("scripts/onboard_weather_cities.py", ""))
    from base_engine.weather.station_registry import STATION_REGISTRY

    best_icao = ""
    best_dist = float("inf")
    for station in STATION_REGISTRY.values():
        d = _haversine_km(lat, lon, station.latitude, station.longitude)
        if d < best_dist:
            best_dist = d
            best_icao = station.station_id
    return best_icao, best_dist


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


async def _geocode(session: aiohttp.ClientSession, city: str) -> Optional[dict]:
    try:
        async with session.get(
            _GEOCODE_URL,
            params={"name": city, "count": "5", "language": "en", "format": "json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                print(f"  [WARN] Geocoding HTTP {resp.status} for '{city}'")
                return None
            data = await resp.json()
    except Exception as e:
        print(f"  [ERROR] Geocoding failed for '{city}': {e}")
        return None
    results = data.get("results") or []
    if not results:
        print(f"  [WARN] No geocoding results for '{city}'")
        return None
    return data


async def _get_elevation(session: aiohttp.ClientSession, lat: float, lon: float) -> float:
    try:
        async with session.get(
            _ELEVATION_URL,
            params={"latitude": str(lat), "longitude": str(lon)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return 0.0
            data = await resp.json()
            elevations = data.get("elevation") or [0.0]
            return float(elevations[0]) if elevations else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Main per-city processor
# ---------------------------------------------------------------------------

async def process_city(session: aiohttp.ClientSession, city: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  City: {city}")
    print(f"{'=' * 60}")

    geo_data = await _geocode(session, city)
    if not geo_data:
        print(f"  [SKIP] Could not geocode '{city}'")
        return

    results = geo_data["results"]
    top = results[0]
    top_score = float(top.get("score", 0.0))

    # Show all candidates so the human can pick
    print(f"\n  Geocoding candidates (showing up to {len(results)}):")
    for i, r in enumerate(results, 1):
        admin = r.get("admin1") or r.get("country") or ""
        print(
            f"    [{i}] {r.get('name')}, {admin}, {r.get('country_code', '?')}  "
            f"lat={r.get('latitude'):.4f} lon={r.get('longitude'):.4f}  "
            f"score={float(r.get('score', 0.0)):.3f}"
        )

    # Warn if ambiguous
    if len(results) >= 2:
        gap = top_score - float(results[1].get("score", 0.0))
        if gap < 0.2:
            print(f"\n  [WARN] Ambiguous! Top-2 score gap = {gap:.3f} (< 0.2). Verify the correct row.")

    # Use top result for code generation
    city_name: str = top.get("name", city)
    lat: float = float(top["latitude"])
    lon: float = float(top["longitude"])
    timezone: str = top.get("timezone", "UTC")
    country_code: str = top.get("country_code", "")
    temp_unit: str = _derive_temp_unit(country_code)
    station_key: str = _to_station_key(city_name)

    # Elevation
    elevation_m = await _get_elevation(session, lat, lon)

    # Nearest ICAO from existing registry
    nearest_icao, icao_dist_km = _nearest_icao_from_registry(lat, lon)
    wu_url = f"https://www.wunderground.com/weather/{nearest_icao}"

    print(f"\n  Nearest existing ICAO: {nearest_icao} ({icao_dist_km:.0f} km away)")
    print(f"  Verify resolution source at: {wu_url}")
    print(f"  (If Polymarket uses a different station, replace station_id and ghcnd_id below)\n")

    # Print the ready-to-paste block
    aliases_tuple = f'("{station_key}",)'
    print("  ── PASTE INTO station_registry.py → STATION_REGISTRY dict ──")
    print()
    print(f'    "{station_key}": WeatherStation(')
    print(f'        city_name="{city_name}",')
    print(f'        station_id="{nearest_icao}",    # TODO: verify against Polymarket resolution source')
    print(f'        ghcnd_id="",                   # TODO: look up GHCND ID at https://www.ncdc.noaa.gov/cdo-web/search')
    print(f'        latitude={lat},')
    print(f'        longitude={lon},')
    print(f'        elevation_m={elevation_m:.1f},')
    print(f'        timezone="{timezone}",')
    print(f'        temp_unit="{temp_unit}",')
    print(f'        aliases={aliases_tuple},')
    print(f'        resolution_source="Weather Underground / {nearest_icao}",  # TODO: verify')
    print(f'    ),')
    print()
    print(f"  Weather Underground verification: {wu_url}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(cities: list[str]) -> None:
    print(f"\nWeatherBot City Onboarding Helper")
    print(f"Cities to process: {cities}\n")
    print("NOTE: This script generates suggestions only. All TODO fields must be")
    print("      verified against the Polymarket resolution source before deploying.\n")

    async with aiohttp.ClientSession() as session:
        for city in cities:
            await process_city(session, city.strip())

    print("\nDone. Review the output above, fill in the TODOs, then:")
    print("  1. Paste the WeatherStation(...) block into station_registry.py")
    print("  2. Run: pytest tests/unit/test_weather_bot.py -k 'test_station'")
    print("  3. Deploy with deploy.sh\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate WeatherStation() blocks for new Polymarket weather cities"
    )
    parser.add_argument(
        "--cities",
        required=True,
        help='Comma-separated city names, e.g. "Riyadh,Cape Town,Springfield"',
    )
    args = parser.parse_args()
    city_list = [c.strip() for c in args.cities.split(",") if c.strip()]
    if not city_list:
        print("Error: --cities must contain at least one city name")
        sys.exit(1)
    asyncio.run(main(city_list))
