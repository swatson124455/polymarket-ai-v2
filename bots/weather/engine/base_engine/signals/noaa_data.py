"""
NOAA Weather Data Source — Tier 3 #24

Free API (api.weather.gov) for US weather data. No auth needed.
Used by WeatherBot for temperature/precipitation prediction markets.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, List
from structlog import get_logger

logger = get_logger()

_BASE = "https://api.weather.gov"
_CACHE_TTL = 1800  # 30 min


class NOAAWeatherData:
    """Fetch weather forecasts from NOAA for US locations."""

    def __init__(self):
        self._point_cache: Dict[str, Dict] = {}
        self._forecast_cache: Dict[str, Dict] = {}

    async def _get_point(self, lat: float, lon: float) -> Optional[Dict]:
        """Get NOAA grid point for lat/lon (cached)."""
        key = f"{lat:.4f},{lon:.4f}"
        if key in self._point_cache:
            cached = self._point_cache[key]
            if (datetime.now(timezone.utc) - cached["fetched_at"]).total_seconds() < 86400:
                return cached["data"]
        try:
            import aiohttp
            url = f"{_BASE}/points/{lat},{lon}"
            headers = {"User-Agent": "PolymarketAI/1.0 (contact@example.com)", "Accept": "application/geo+json"}
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            props = data.get("properties", {})
            result = {
                "forecast_url": props.get("forecast"),
                "forecast_hourly_url": props.get("forecastHourly"),
                "grid_id": props.get("gridId"),
                "grid_x": props.get("gridX"),
                "grid_y": props.get("gridY"),
                "city": props.get("relativeLocation", {}).get("properties", {}).get("city"),
                "state": props.get("relativeLocation", {}).get("properties", {}).get("state"),
            }
            self._point_cache[key] = {"data": result, "fetched_at": datetime.now(timezone.utc)}
            return result
        except Exception as e:
            logger.debug("NOAA point lookup failed: %s", e)
            return None

    async def get_forecast(self, lat: float, lon: float) -> Optional[List[Dict]]:
        """Get 7-day forecast periods for a location."""
        point = await self._get_point(lat, lon)
        if not point or not point.get("forecast_url"):
            return None
        cache_key = point["forecast_url"]
        if cache_key in self._forecast_cache:
            cached = self._forecast_cache[cache_key]
            if (datetime.now(timezone.utc) - cached["fetched_at"]).total_seconds() < _CACHE_TTL:
                return cached["data"]
        try:
            import aiohttp
            headers = {"User-Agent": "PolymarketAI/1.0", "Accept": "application/geo+json"}
            async with aiohttp.ClientSession() as sess:
                async with sess.get(cache_key, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            periods = data.get("properties", {}).get("periods", [])
            result = [
                {
                    "name": p.get("name"),
                    "temperature": p.get("temperature"),
                    "temperature_unit": p.get("temperatureUnit"),
                    "wind_speed": p.get("windSpeed"),
                    "wind_direction": p.get("windDirection"),
                    "forecast": p.get("shortForecast"),
                    "detailed": p.get("detailedForecast"),
                    "start_time": p.get("startTime"),
                    "is_daytime": p.get("isDaytime"),
                    "precipitation_pct": p.get("probabilityOfPrecipitation", {}).get("value"),
                }
                for p in periods[:14]
            ]
            self._forecast_cache[cache_key] = {"data": result, "fetched_at": datetime.now(timezone.utc)}
            return result
        except Exception as e:
            logger.debug("NOAA forecast fetch failed: %s", e)
            return None

    async def get_temperature_forecast(self, lat: float, lon: float) -> Optional[Dict]:
        """Get temperature range forecast (high/low) for next 7 days."""
        periods = await self.get_forecast(lat, lon)
        if not periods:
            return None
        highs = [p["temperature"] for p in periods if p.get("is_daytime") and p.get("temperature") is not None]
        lows = [p["temperature"] for p in periods if not p.get("is_daytime") and p.get("temperature") is not None]
        return {
            "highs": highs,
            "lows": lows,
            "max_high": max(highs) if highs else None,
            "min_low": min(lows) if lows else None,
            "avg_high": sum(highs) / len(highs) if highs else None,
            "avg_low": sum(lows) / len(lows) if lows else None,
            "unit": periods[0].get("temperature_unit", "F") if periods else "F",
        }


# Major US cities lat/lon for weather market matching
US_CITIES = {
    "new york": (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "dallas": (32.7767, -96.7970),
    "miami": (25.7617, -80.1918),
    "denver": (39.7392, -104.9903),
    "seattle": (47.6062, -122.3321),
    "boston": (42.3601, -71.0589),
    "atlanta": (33.7490, -84.3880),
    "san francisco": (37.7749, -122.4194),
    "washington": (38.9072, -77.0369),
    "las vegas": (36.1699, -115.1398),
    "minneapolis": (44.9778, -93.2650),
}
