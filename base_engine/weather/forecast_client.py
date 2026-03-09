"""
Forecast Client — async Open-Meteo API wrapper for ensemble weather forecasts.

Uses Open-Meteo (free, no API key) to access:
  - GFS: 0.11° resolution, 6h updates, 16-day forecast
  - HRRR: 3km resolution, hourly updates, 18h forecast (US only)
  - GEFS/GFS025: 31 ensemble members for probability distributions
  - ICON: European model alternative

Temperature unit handling:
  - US stations: request Fahrenheit directly
  - International: request Celsius (default)
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
from structlog import get_logger

from base_engine.weather.station_registry import WeatherStation

logger = get_logger()

_DETERMINISTIC_URL = "https://api.open-meteo.com/v1/forecast"
_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
_HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
# NWS API (free, no key) — NBM-based daily forecasts for US stations
_NWS_POINTS_URL = "https://api.weather.gov/points"


@dataclass
class CombinedForecast:
    """Merged deterministic + ensemble forecast for a specific station + date."""

    ensemble_members: List[float]    # Daily-max temperature per ensemble member
    deterministic_high: float        # Best single-model estimate
    model_spread: float              # Std deviation across models
    lead_time_hours: float           # Hours from now until target date noon
    fetch_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    models_used: List[str] = field(default_factory=list)


class WeatherForecastClient:
    """Fetch ensemble forecasts from Open-Meteo API."""

    def __init__(self, cache_ttl: float = 900.0, rate_limit_per_min: int = 50):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, Tuple[float, CombinedForecast]] = {}
        self._precip_cache: Dict[str, Tuple[float, List[float]]] = {}
        self._snowfall_cache: Dict[str, Tuple[float, List[float]]] = {}
        self._wind_cache: Dict[str, Tuple[float, List[float]]] = {}
        self._cache_ttl = cache_ttl
        self._rate_limit = rate_limit_per_min
        self._request_times: List[float] = []
        # NWS grid forecast URLs per station: station_id → (expiry_mono, forecast_url)
        # Grid coordinates are static per station; cache for 24h to avoid repeated lookups.
        self._nws_forecast_url_cache: Dict[str, Tuple[float, Optional[str]]] = {}
        # Climate normals cache: cache_key → (expiry_mono, Optional[(mean, std)])
        self._climate_cache: Dict[str, Tuple[float, Optional[Tuple[float, float]]]] = {}
        self._climate_computed_this_cycle: int = 0
        self._climate_max_per_cycle: int = 3
        # Fix A: per-model 429 backoff. When a model returns 429, skip it until
        # this timestamp passes (1-hour cooldown). Prevents retry storms after
        # quota exhaustion — all 3 models share this dict.
        self._model_429_until: Dict[str, float] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "PolymarketWeatherBot/1.0"},
            )
        return self._session

    async def get_session(self) -> aiohttp.ClientSession:
        """Public accessor — reuse the shared aiohttp session for external calls."""
        return await self._ensure_session()

    async def _rate_limit_wait(self) -> None:
        """Simple sliding-window rate limiter."""
        now = time.monotonic()
        # Remove timestamps older than 60s
        self._request_times = [t for t in self._request_times if now - t < 60.0]
        if len(self._request_times) >= self._rate_limit:
            wait = 60.0 - (now - self._request_times[0]) + 0.1
            if wait > 0:
                await asyncio.sleep(wait)
        self._request_times.append(time.monotonic())

    async def get_deterministic_forecast(
        self,
        latitude: float,
        longitude: float,
        temp_unit: str = "celsius",
        forecast_days: int = 7,
    ) -> Optional[Dict]:
        """Fetch deterministic daily max/min from GFS + ICON.

        Returns dict with keys per model: {model: [daily_max_temps...]}
        """
        await self._rate_limit_wait()
        session = await self._ensure_session()

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min",
            "models": "gfs_seamless,icon_seamless",
            "forecast_days": forecast_days,
            "timezone": "auto",
        }
        if temp_unit.upper() == "F":
            params["temperature_unit"] = "fahrenheit"

        try:
            async with session.get(_DETERMINISTIC_URL, params=params) as resp:
                if resp.status != 200:
                    logger.warning("open_meteo_deterministic_error", status=resp.status)
                    return None
                data = await resp.json()
            return data
        except Exception as exc:
            logger.warning("open_meteo_deterministic_failed", error=str(exc))
            return None

    async def get_ensemble_forecast(
        self,
        latitude: float,
        longitude: float,
        temp_unit: str = "celsius",
        forecast_days: int = 7,
    ) -> Optional[Dict]:
        """Fetch ensemble forecasts from GEFS (31) + ECMWF IFS ENS (51) + ECMWF AIFS ENS (51).

        P6 upgrade: adds ECMWF AIFS ENS (free CC-BY-4.0, available Oct 2025) as a third
        parallel model. AIFS ENS improves CRPS for 2m temperature at all lead times vs IFS ENS.
        Combined count: up to 133 members vs prior 82 (~25% further variance reduction).

        Returns a synthetic merged response dict with all member columns combined.
        """
        # Fetch all three ensemble models in parallel — each costs 1 API request
        gefs_task = self._fetch_ensemble_model(latitude, longitude, temp_unit, "gfs025", forecast_days)
        ecmwf_task = self._fetch_ensemble_model(latitude, longitude, temp_unit, "ecmwf_ifs025", forecast_days)
        aifs_task = self._fetch_ensemble_model(latitude, longitude, temp_unit, "ecmwf_aifs025", forecast_days)
        gefs_data, ecmwf_data, aifs_data = await asyncio.gather(
            gefs_task, ecmwf_task, aifs_task, return_exceptions=True
        )

        if isinstance(gefs_data, Exception):
            logger.debug("gefs_ensemble_exception", error=str(gefs_data))
            gefs_data = None
        if isinstance(ecmwf_data, Exception):
            logger.debug("ecmwf_ifs_ensemble_exception", error=str(ecmwf_data))
            ecmwf_data = None
        if isinstance(aifs_data, Exception):
            logger.debug("ecmwf_aifs_ensemble_exception", error=str(aifs_data))
            aifs_data = None

        if gefs_data is None and ecmwf_data is None and aifs_data is None:
            return None

        # Use first available model as base; merge remaining sources with offset key renaming
        sources = [gefs_data, ecmwf_data, aifs_data]
        merged = next(s for s in sources if s is not None)
        running_offset = sum(
            1 for k in merged["daily"] if k.startswith("temperature_2m_max_member")
        )

        for src_data in sources:
            if src_data is None or src_data is merged:
                continue
            if "daily" not in src_data or "daily" not in merged:
                continue
            src_count = 0
            for key, vals in src_data["daily"].items():
                if key.startswith("temperature_2m_max_member"):
                    suffix = key[len("temperature_2m_max_member"):]
                    try:
                        src_idx = int(suffix)
                        new_key = f"temperature_2m_max_member{running_offset + src_idx:02d}"
                        merged["daily"][new_key] = vals
                        src_count = max(src_count, src_idx + 1)
                    except ValueError:
                        pass
            running_offset += src_count

        return merged

    async def _fetch_ensemble_model(
        self,
        latitude: float,
        longitude: float,
        temp_unit: str,
        model: str,
        forecast_days: int,
    ) -> Optional[Dict]:
        """Fetch one ensemble model from Open-Meteo /v1/ensemble endpoint."""
        # Fix A: skip if this model is in 429 cooldown (quota exhausted)
        if time.monotonic() < self._model_429_until.get(model, 0.0):
            return None
        await self._rate_limit_wait()
        session = await self._ensure_session()

        # L5: Cap forecast_days at 15 — ECMWF IFS025 max is 15 days, GEFS max is 16.
        # Requesting beyond model horizon causes Open-Meteo to return NaN members.
        _capped_days = min(forecast_days, 15)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max",
            "models": model,
            "forecast_days": _capped_days,
            "timezone": "auto",
        }
        if temp_unit.upper() == "F":
            params["temperature_unit"] = "fahrenheit"

        try:
            async with session.get(_ENSEMBLE_URL, params=params) as resp:
                if resp.status == 429:
                    # Fix A: set 1-hour cooldown so subsequent scans skip this model
                    self._model_429_until[model] = time.monotonic() + 3600.0
                if resp.status != 200:
                    logger.warning(
                        "open_meteo_ensemble_error", model=model, status=resp.status
                    )
                    return None
                return await resp.json()
        except Exception as exc:
            logger.warning("open_meteo_ensemble_failed", model=model, error=str(exc))
            return None

    async def get_precipitation_ensemble(
        self,
        station: WeatherStation,
        target_date: date,
        forecast_days: int = 7,
    ) -> Optional[List[float]]:
        """D3: Fetch daily precipitation_sum ensemble members from Open-Meteo.

        Returns list of precipitation totals (mm or inches) per ensemble member
        for the target_date. Uses same GEFS + ECMWF IFS + AIFS models as temperature.
        Precipitation is always returned in mm from Open-Meteo; convert to inches for US.
        """
        cache_key = f"precip:{station.station_id}:{target_date.isoformat()}"
        cached = self._precip_cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        lat, lon = station.latitude, station.longitude
        _capped_days = min(forecast_days, 15)

        all_members: List[float] = []
        for model in ("gfs025", "ecmwf_ifs025", "ecmwf_aifs025"):
            # Fix A: skip model if still in 429 cooldown
            if time.monotonic() < self._model_429_until.get(model, 0.0):
                continue
            await self._rate_limit_wait()
            session = await self._ensure_session()
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "precipitation_sum",
                "models": model,
                "forecast_days": _capped_days,
                "timezone": "auto",
            }
            try:
                async with session.get(_ENSEMBLE_URL, params=params) as resp:
                    if resp.status == 429:
                        self._model_429_until[model] = time.monotonic() + 3600.0
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                # Find index matching target_date
                target_str = target_date.isoformat()
                if target_str not in dates:
                    continue
                idx = dates.index(target_str)
                # Collect all member values at that index
                for key, vals in daily.items():
                    if key.startswith("precipitation_sum_member") and isinstance(vals, list):
                        if idx < len(vals) and vals[idx] is not None:
                            val = float(vals[idx])
                            # Convert mm → inches for US stations
                            if station.temp_unit.upper() == "F":
                                val = val / 25.4
                            all_members.append(max(0.0, val))
            except Exception as exc:
                logger.debug("precip_ensemble_fetch_failed", model=model, error=str(exc))

        if not all_members:
            return None

        # Cache — Fix B: jitter expiry to spread API refreshes
        self._precip_cache[cache_key] = (
            time.monotonic() + self._cache_ttl + random.uniform(0, self._cache_ttl * 0.5),
            all_members,
        )
        logger.debug(
            "precip_ensemble_fetched",
            station=station.station_id,
            date=target_date.isoformat(),
            n_members=len(all_members),
            mean=round(sum(all_members) / len(all_members), 2),
        )
        return all_members

    async def get_monthly_precipitation_ensemble(
        self,
        station: WeatherStation,
        month: int,
        year: int,
    ) -> Optional[List[float]]:
        """Fetch monthly cumulative precipitation ensemble for a given month.

        Combines:
          1. Historical actuals (Open-Meteo archive) for elapsed days.
          2. Ensemble forecasts for remaining days in the month.

        Each "member" value = actual_so_far + member's sum of remaining daily forecasts.
        Returns list of monthly totals (one per ensemble member), in inches (US) or mm.
        """
        import calendar

        cache_key = f"precip_monthly:{station.station_id}:{year}-{month:02d}"
        cached = self._precip_cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        lat, lon = station.latitude, station.longitude
        last_day = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, last_day)
        today = datetime.now(timezone.utc).date()

        # 1. Historical actuals for elapsed days (month_start to yesterday)
        actual_total = 0.0
        yesterday = today - timedelta(days=1)
        if yesterday >= month_start:
            hist_end = min(yesterday, month_end)
            await self._rate_limit_wait()
            session = await self._ensure_session()
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": month_start.isoformat(),
                "end_date": hist_end.isoformat(),
                "daily": "precipitation_sum",
                "timezone": "auto",
            }
            try:
                async with session.get(_HISTORICAL_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        daily = data.get("daily", {})
                        precip_vals = daily.get("precipitation_sum", [])
                        for v in precip_vals:
                            if v is not None:
                                actual_total += float(v)
            except Exception as exc:
                logger.debug("monthly_precip_hist_failed", error=str(exc))

            # Convert mm → inches for US
            if station.temp_unit.upper() == "F":
                actual_total = actual_total / 25.4

        # 2. If entire month is in the past, return actual as single-value list
        if today > month_end:
            result = [actual_total]
            self._precip_cache[cache_key] = (
                time.monotonic() + self._cache_ttl + random.uniform(0, self._cache_ttl * 0.5),
                result,
            )
            return result

        # 3. Ensemble forecasts for remaining days (today through month_end)
        remaining_days = (month_end - today).days + 1  # inclusive
        # GEFS goes 35 days, ECMWF IFS 15, AIFS 6 — use max feasible
        forecast_days = min(remaining_days + 3, 35)  # +3 buffer for date alignment

        # Collect per-member daily sums for remaining period
        # member_sums[member_key] = sum of daily precip for remaining days
        member_sums: List[float] = []

        for model in ("gfs025", "ecmwf_ifs025", "ecmwf_aifs025"):
            # Fix A: skip model if still in 429 cooldown
            if time.monotonic() < self._model_429_until.get(model, 0.0):
                continue
            await self._rate_limit_wait()
            session = await self._ensure_session()
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "precipitation_sum",
                "models": model,
                "forecast_days": forecast_days,
                "timezone": "auto",
            }
            try:
                async with session.get(_ENSEMBLE_URL, params=params) as resp:
                    if resp.status == 429:
                        self._model_429_until[model] = time.monotonic() + 3600.0
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                daily = data.get("daily", {})
                dates = daily.get("time", [])

                # Build date→index mapping for remaining month dates
                target_dates = set()
                d = max(today, month_start)
                while d <= month_end:
                    target_dates.add(d.isoformat())
                    d += timedelta(days=1)

                target_indices = [
                    i for i, dt in enumerate(dates) if dt in target_dates
                ]
                if not target_indices:
                    continue

                # Sum each member's daily values over the remaining period
                members_before = len(member_sums)
                for key, vals in daily.items():
                    if not key.startswith("precipitation_sum_member"):
                        continue
                    if not isinstance(vals, list):
                        continue
                    member_total = 0.0
                    valid = 0
                    for idx in target_indices:
                        if idx < len(vals) and vals[idx] is not None:
                            member_total += float(vals[idx])
                            valid += 1
                    # Only include if we got data for most remaining days
                    if valid >= len(target_indices) * 0.7:
                        val = member_total
                        if station.temp_unit.upper() == "F":
                            val = val / 25.4  # mm → inches
                        member_sums.append(max(0.0, actual_total + val))
                if len(member_sums) == members_before:
                    logger.warning(
                        "monthly_precip_model_no_members",
                        model=model,
                        remaining_days=len(target_indices),
                        reason="all members below 70% coverage threshold",
                    )
            except Exception as exc:
                logger.debug(
                    "monthly_precip_ensemble_failed",
                    model=model, error=str(exc),
                )

        if not member_sums:
            return None

        self._precip_cache[cache_key] = (
            time.monotonic() + self._cache_ttl + random.uniform(0, self._cache_ttl * 0.5),
            member_sums,
        )
        logger.debug(
            "monthly_precip_ensemble_fetched",
            station=station.station_id,
            month=f"{year}-{month:02d}",
            actual_so_far=round(actual_total, 2),
            n_members=len(member_sums),
            mean=round(sum(member_sums) / len(member_sums), 2),
        )
        return member_sums

    async def get_snowfall_ensemble(
        self,
        station: WeatherStation,
        target_date: date,
        forecast_days: int = 7,
    ) -> Optional[List[float]]:
        """Fetch daily snowfall_sum ensemble members from Open-Meteo.

        Returns list of snowfall totals per ensemble member.
        Open-Meteo returns snowfall in cm; convert to inches for US stations.
        """
        cache_key = f"snow:{station.station_id}:{target_date.isoformat()}"
        cached = self._snowfall_cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        lat, lon = station.latitude, station.longitude
        _capped_days = min(forecast_days, 15)

        all_members: List[float] = []
        for model in ("gfs025", "ecmwf_ifs025", "ecmwf_aifs025"):
            # Fix A: skip model if still in 429 cooldown
            if time.monotonic() < self._model_429_until.get(model, 0.0):
                continue
            await self._rate_limit_wait()
            session = await self._ensure_session()
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "snowfall_sum",
                "models": model,
                "forecast_days": _capped_days,
                "timezone": "auto",
            }
            try:
                async with session.get(_ENSEMBLE_URL, params=params) as resp:
                    if resp.status == 429:
                        self._model_429_until[model] = time.monotonic() + 3600.0
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                target_str = target_date.isoformat()
                if target_str not in dates:
                    continue
                idx = dates.index(target_str)
                for key, vals in daily.items():
                    if key.startswith("snowfall_sum_member") and isinstance(vals, list):
                        if idx < len(vals) and vals[idx] is not None:
                            val = float(vals[idx])
                            # Open-Meteo snowfall is in cm; convert to inches for US
                            if station.temp_unit.upper() == "F":
                                val = val / 2.54
                            all_members.append(max(0.0, val))
            except Exception as exc:
                logger.debug("snowfall_ensemble_fetch_failed", model=model, error=str(exc))

        if not all_members:
            return None

        self._snowfall_cache[cache_key] = (
            time.monotonic() + self._cache_ttl + random.uniform(0, self._cache_ttl * 0.5),
            all_members,
        )
        logger.debug(
            "snowfall_ensemble_fetched",
            station=station.station_id,
            date=target_date.isoformat(),
            n_members=len(all_members),
            mean=round(sum(all_members) / len(all_members), 2),
        )
        return all_members

    async def get_wind_ensemble(
        self,
        station: WeatherStation,
        target_date: date,
        forecast_days: int = 7,
    ) -> Optional[List[float]]:
        """Fetch daily wind_gusts_10m_max ensemble members from Open-Meteo.

        Returns list of max wind gust values per ensemble member.
        Open-Meteo returns wind gusts in km/h; convert to mph for US stations.
        """
        cache_key = f"wind:{station.station_id}:{target_date.isoformat()}"
        cached = self._wind_cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        lat, lon = station.latitude, station.longitude
        _capped_days = min(forecast_days, 15)

        all_members: List[float] = []
        for model in ("gfs025", "ecmwf_ifs025", "ecmwf_aifs025"):
            # Fix A: skip model if still in 429 cooldown
            if time.monotonic() < self._model_429_until.get(model, 0.0):
                continue
            await self._rate_limit_wait()
            session = await self._ensure_session()
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "wind_gusts_10m_max",
                "models": model,
                "forecast_days": _capped_days,
                "timezone": "auto",
            }
            try:
                async with session.get(_ENSEMBLE_URL, params=params) as resp:
                    if resp.status == 429:
                        self._model_429_until[model] = time.monotonic() + 3600.0
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                target_str = target_date.isoformat()
                if target_str not in dates:
                    continue
                idx = dates.index(target_str)
                for key, vals in daily.items():
                    if key.startswith("wind_gusts_10m_max_member") and isinstance(vals, list):
                        if idx < len(vals) and vals[idx] is not None:
                            val = float(vals[idx])
                            # Open-Meteo wind is km/h; convert to mph for US
                            if station.temp_unit.upper() == "F":
                                val = val / 1.609
                            all_members.append(max(0.0, val))
            except Exception as exc:
                logger.debug("wind_ensemble_fetch_failed", model=model, error=str(exc))

        if not all_members:
            return None

        self._wind_cache[cache_key] = (
            time.monotonic() + self._cache_ttl + random.uniform(0, self._cache_ttl * 0.5),
            all_members,
        )
        logger.debug(
            "wind_ensemble_fetched",
            station=station.station_id,
            date=target_date.isoformat(),
            n_members=len(all_members),
            mean=round(sum(all_members) / len(all_members), 2),
        )
        return all_members

    async def get_nbm_forecast(
        self,
        latitude: float,
        longitude: float,
        station_id: str,
        target_date: date,
    ) -> Optional[float]:
        """Fetch NBM-based daily high temperature from NWS API for US stations.

        NWS 7-day forecast is generated directly from NBM (National Blend of Models),
        which applies MAE-weighted blending of 31+ model systems with bias correction.
        NBM MAE at day-1 is 0.8-1.2°F — better than or equal to raw GFS deterministic.

        Returns daily high in °F (NWS always uses °F for US stations), or None if
        the NWS API is unavailable or target_date is beyond the 7-day horizon.

        Two-step NWS call:
          1. GET /points/{lat},{lon} → returns forecast URL for this grid point
          2. GET {forecast_url}       → returns 7-day periods; extract daytime high
        The forecast URL is cached per station for 24h (grid coordinates are static).
        """
        # Step 1: Get or cache the NWS forecast URL for this station
        now_mono = time.monotonic()
        cached_url = self._nws_forecast_url_cache.get(station_id)
        if cached_url and now_mono < cached_url[0]:
            forecast_url = cached_url[1]
        else:
            forecast_url = await self._fetch_nws_forecast_url(latitude, longitude, station_id)
            # Cache for 24h regardless of result (None = station not served by NWS)
            self._nws_forecast_url_cache[station_id] = (now_mono + 86400.0, forecast_url)

        if not forecast_url:
            return None

        # Step 2: Fetch 7-day forecast and find the daytime period for target_date
        session = await self._ensure_session()
        target_iso = target_date.isoformat()
        try:
            async with session.get(
                forecast_url,
                headers={"Accept": "application/geo+json"},
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        "nws_forecast_api_error",
                        station=station_id,
                        status=resp.status,
                    )
                    return None
                data = await resp.json(content_type=None)
            periods = data.get("properties", {}).get("periods", [])
            for period in periods:
                is_daytime = period.get("isDaytime", False)
                if not is_daytime:
                    continue
                # H3: Parse ISO datetime properly instead of string search
                # to avoid timezone-related off-by-one day errors.
                start_time = period.get("startTime", "")
                try:
                    # ISO 8601 with offset: "2026-03-06T06:00:00-05:00"
                    st = datetime.fromisoformat(start_time)
                    if st.date() != target_date:
                        continue
                except (ValueError, TypeError):
                    # Fallback to string match if ISO parse fails
                    if target_iso not in start_time:
                        continue
                temp = period.get("temperature")
                temp_unit = period.get("temperatureUnit", "F")
                if temp is not None:
                    temp_f = float(temp)
                    if temp_unit.upper() == "C":
                        temp_f = temp_f * 9.0 / 5.0 + 32.0
                    logger.debug(
                        "nws_nbm_forecast_retrieved",
                        station=station_id,
                        date=target_iso,
                        temp_f=round(temp_f, 1),
                    )
                    return temp_f
            return None
        except Exception as exc:
            logger.debug("nws_forecast_failed", station=station_id, error=str(exc))
            return None

    async def _fetch_nws_forecast_url(
        self,
        latitude: float,
        longitude: float,
        station_id: str,
    ) -> Optional[str]:
        """Call NWS /points/{lat},{lon} to get the 7-day forecast URL for this grid cell."""
        session = await self._ensure_session()
        url = f"{_NWS_POINTS_URL}/{latitude:.4f},{longitude:.4f}"
        try:
            async with session.get(
                url,
                headers={"Accept": "application/geo+json"},
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        "nws_points_api_error",
                        station=station_id,
                        status=resp.status,
                    )
                    return None
                data = await resp.json(content_type=None)
            forecast_url = data.get("properties", {}).get("forecast")
            if forecast_url:
                logger.debug(
                    "nws_forecast_url_cached",
                    station=station_id,
                    forecast_url=forecast_url,
                )
            return forecast_url
        except Exception as exc:
            logger.debug("nws_points_failed", station=station_id, error=str(exc))
            return None

    async def get_combined_forecast(
        self,
        station: WeatherStation,
        target_date: date,
    ) -> Optional[CombinedForecast]:
        """Fetch and merge deterministic + ensemble forecasts for a station and date.

        Returns CombinedForecast with ensemble members, deterministic high,
        model spread, and lead time. Caches results for cache_ttl seconds.
        """
        cache_key = f"{station.station_id}:{target_date.isoformat()}"
        now_mono = time.monotonic()

        # Check cache
        cached = self._cache.get(cache_key)
        if cached and now_mono < cached[0]:
            return cached[1]

        # Fetch deterministic, ensemble, and (for US stations) NBM in parallel
        det_task = self.get_deterministic_forecast(
            station.latitude, station.longitude, station.temp_unit,
        )
        ens_task = self.get_ensemble_forecast(
            station.latitude, station.longitude, station.temp_unit,
        )
        # NBM via NWS API is only available for US stations (temp_unit == "F")
        nbm_task = (
            self.get_nbm_forecast(
                station.latitude, station.longitude, station.station_id, target_date,
            )
            if station.temp_unit.upper() == "F"
            else asyncio.sleep(0, result=None)  # no-op coroutine for non-US stations
        )
        det_data, ens_data, nbm_high = await asyncio.gather(
            det_task, ens_task, nbm_task, return_exceptions=True
        )

        if isinstance(det_data, Exception):
            logger.warning("forecast_deterministic_exception", error=str(det_data))
            det_data = None
        if isinstance(ens_data, Exception):
            logger.warning("forecast_ensemble_exception", error=str(ens_data))
            ens_data = None
        if isinstance(nbm_high, Exception):
            logger.debug("forecast_nbm_exception", error=str(nbm_high))
            nbm_high = None

        if det_data is None and ens_data is None:
            return None

        target_iso = target_date.isoformat()

        # Extract deterministic daily max for target date
        deterministic_high = None
        models_used = []
        if det_data and "daily" in det_data:
            daily = det_data["daily"]
            dates = daily.get("time", [])
            maxes = daily.get("temperature_2m_max", [])
            if target_iso in dates:
                idx = dates.index(target_iso)
                if idx < len(maxes) and maxes[idx] is not None:
                    deterministic_high = float(maxes[idx])
                    models_used.append("gfs_seamless")

        # NBM override for US stations: NWS NBM has lower MAE than raw GFS at day 1-3.
        # Use NBM as the primary deterministic_high when available; GFS is the fallback.
        # NBM is already in °F (NWS API always returns °F); no unit conversion needed.
        if nbm_high is not None:
            deterministic_high = nbm_high
            if "gfs_seamless" in models_used:
                models_used.remove("gfs_seamless")
            models_used.append("nbm")

        # Extract ensemble members for target date
        ensemble_members = []
        if ens_data and "daily" in ens_data:
            daily = ens_data["daily"]
            dates = daily.get("time", [])
            if target_iso in dates:
                idx = dates.index(target_iso)
                # Open-Meteo ensemble returns temperature_2m_max_member01..31
                for key in sorted(daily.keys()):
                    if key.startswith("temperature_2m_max_member"):
                        vals = daily[key]
                        if idx < len(vals) and vals[idx] is not None:
                            val = float(vals[idx])
                            # C1: Filter NaN/Inf at extraction point
                            if math.isfinite(val):
                                ensemble_members.append(val)
                if ensemble_members:
                    models_used.append("gfs025_ensemble")
                    # P5: If member count exceeds GEFS-only count (31), ECMWF IFS was also merged
                    if len(ensemble_members) > 31:
                        models_used.append("ecmwf_ifs025")
                    # P6: If member count exceeds GEFS+IFS count (82), ECMWF AIFS was also merged
                    if len(ensemble_members) > 82:
                        models_used.append("ecmwf_aifs025")

        if not ensemble_members and deterministic_high is None:
            logger.debug(
                "forecast_no_data_for_date",
                station=station.station_id,
                date=target_iso,
                det_available=det_data is not None,
                ens_available=ens_data is not None,
            )
            return None

        # If no ensemble, create a synthetic spread around deterministic
        if not ensemble_members and deterministic_high is not None:
            # Use typical HRRR MAE of ~2°F / ~1.1°C as synthetic spread
            spread = 2.0 if station.temp_unit == "F" else 1.1
            rng = random.Random(hash((station.station_id, target_iso)))
            ensemble_members = [deterministic_high + rng.gauss(0, spread) for _ in range(31)]

        if deterministic_high is None and ensemble_members:
            deterministic_high = sum(ensemble_members) / len(ensemble_members)

        # Calculate lead time
        now_utc = datetime.now(timezone.utc)
        # Target date noon local ≈ 18:00 UTC for US Eastern, 12:00 UTC for London
        target_noon_utc = datetime(target_date.year, target_date.month, target_date.day, 18, 0, tzinfo=timezone.utc)
        lead_time_hours = max(0.0, (target_noon_utc - now_utc).total_seconds() / 3600.0)

        # Model spread
        if len(ensemble_members) > 1:
            mean = sum(ensemble_members) / len(ensemble_members)
            variance = sum((x - mean) ** 2 for x in ensemble_members) / len(ensemble_members)
            model_spread = variance ** 0.5
        else:
            model_spread = 2.0 if station.temp_unit == "F" else 1.1

        result = CombinedForecast(
            ensemble_members=ensemble_members,
            deterministic_high=deterministic_high,
            model_spread=model_spread,
            lead_time_hours=lead_time_hours,
            models_used=models_used,
        )

        # Cache — Fix B: jitter expiry so all stations don't expire simultaneously
        self._cache[cache_key] = (
            now_mono + self._cache_ttl + random.uniform(0, self._cache_ttl * 0.5),
            result,
        )
        return result

    async def get_historical_temperature(
        self,
        latitude: float,
        longitude: float,
        target_date: date,
        temp_unit: str = "celsius",
    ) -> Optional[float]:
        """Fetch actual historical daily-max temperature from Open-Meteo archive API.

        Used by the calibration feedback loop to fill in actual_temp for past
        forecast rows so bias correction can be computed.

        Returns the daily maximum temperature for target_date, or None if unavailable.
        """
        await self._rate_limit_wait()
        session = await self._ensure_session()

        target_iso = target_date.isoformat()
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": target_iso,
            "end_date": target_iso,
            "daily": "temperature_2m_max",
            "timezone": "auto",
        }
        if temp_unit.upper() == "F":
            params["temperature_unit"] = "fahrenheit"

        try:
            async with session.get(_HISTORICAL_URL, params=params) as resp:
                if resp.status != 200:
                    logger.warning(
                        "historical_api_error",
                        status=resp.status,
                        date=target_iso,
                    )
                    return None
                data = await resp.json()
            daily = data.get("daily", {})
            maxes = daily.get("temperature_2m_max", [])
            if maxes and maxes[0] is not None:
                return float(maxes[0])
            return None
        except Exception as exc:
            logger.debug("historical_api_failed", date=target_iso, error=str(exc))
            return None

    async def get_climate_normal(
        self,
        latitude: float,
        longitude: float,
        target_date: date,
        temp_unit: str = "celsius",
    ) -> Optional[Tuple[float, float]]:
        """Compute climate normal (mean, std) for this day-of-year from archive data.

        Uses Open-Meteo archive API for a ±3 day window across the last 10 years.
        Returns (mean_daily_max, std_daily_max) or None if insufficient data.
        Cached per (lat, lon, day_of_year) for 30 days.
        Limited to 1 new computation per scan cycle to avoid rate-limit pressure.
        """
        doy = target_date.timetuple().tm_yday
        cache_key = f"clim:{latitude:.2f}:{longitude:.2f}:{doy}:{temp_unit}"

        cached = self._climate_cache.get(cache_key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        # Limit new climate normal computations per scan cycle
        if self._climate_computed_this_cycle >= self._climate_max_per_cycle:
            return None
        self._climate_computed_this_cycle += 1

        # Fetch ±3 day window from last 10 years
        temps: List[float] = []
        current_year = target_date.year

        for year_offset in range(1, 11):
            year = current_year - year_offset
            try:
                center = date(year, target_date.month, target_date.day)
            except ValueError:
                continue  # Feb 29 in non-leap year
            start = center - timedelta(days=3)
            end = center + timedelta(days=3)

            year_temps = await self._fetch_archive_range(
                latitude, longitude, start, end, temp_unit,
            )
            temps.extend(year_temps)

        if len(temps) < 20:
            # Not enough data — cache the miss for 24h
            self._climate_cache[cache_key] = (time.monotonic() + 86400.0, None)
            logger.debug("climate_normal_insufficient_data", doy=doy, samples=len(temps))
            return None

        mean = sum(temps) / len(temps)
        std = max(
            (sum((t - mean) ** 2 for t in temps) / (len(temps) - 1)) ** 0.5,
            0.5,
        )
        result = (mean, std)

        # Cache for 30 days (climate normals are stable)
        self._climate_cache[cache_key] = (time.monotonic() + 86400.0 * 30, result)
        logger.info(
            "climate_normal_computed",
            doy=doy,
            mean=round(mean, 1),
            std=round(std, 1),
            samples=len(temps),
            unit=temp_unit,
        )
        return result

    async def _fetch_archive_range(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        temp_unit: str,
    ) -> List[float]:
        """Fetch daily max temperatures for a date range from Open-Meteo archive."""
        await self._rate_limit_wait()
        session = await self._ensure_session()

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "temperature_2m_max",
            "timezone": "auto",
        }
        if temp_unit.upper() == "F":
            params["temperature_unit"] = "fahrenheit"

        try:
            async with session.get(_HISTORICAL_URL, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
            maxes = data.get("daily", {}).get("temperature_2m_max", [])
            return [float(t) for t in maxes if t is not None]
        except Exception:
            return []

    async def get_ndfd_pop(
        self,
        station: WeatherStation,
    ) -> Optional[List[Tuple[str, float, str]]]:
        """D2: Fetch NDFD probability of precipitation from NWS digital forecast.

        Returns list of (period_name, pop_pct, start_date_iso) tuples for next 72h.
        start_date_iso is YYYY-MM-DD extracted from the period's startTime.
        Uses same NWS /forecast endpoint as NBM (includes PoP in periods).
        US stations only.
        """
        if station.temp_unit.upper() != "F":
            return None

        # Reuse NWS forecast URL cache (same as get_nbm_forecast)
        now_mono = time.monotonic()
        cached_url = self._nws_forecast_url_cache.get(station.station_id)
        if cached_url and now_mono < cached_url[0]:
            forecast_url = cached_url[1]
        else:
            forecast_url = await self._fetch_nws_forecast_url(
                station.latitude, station.longitude, station.station_id,
            )
            self._nws_forecast_url_cache[station.station_id] = (
                now_mono + 86400.0, forecast_url,
            )

        if not forecast_url:
            return None

        await self._rate_limit_wait()
        session = await self._ensure_session()
        try:
            async with session.get(
                forecast_url,
                headers={"Accept": "application/geo+json"},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)

            periods = data.get("properties", {}).get("periods", [])
            pop_data: List[Tuple[str, float, str]] = []
            for p in periods:
                pop = p.get("probabilityOfPrecipitation", {})
                val = pop.get("value")
                if val is not None:
                    # Extract date from startTime ISO 8601 (e.g. "2026-03-08T06:00:00-05:00")
                    start_time = p.get("startTime", "")
                    try:
                        start_date_iso = datetime.fromisoformat(start_time).date().isoformat()
                    except (ValueError, TypeError):
                        start_date_iso = ""
                    pop_data.append((p.get("name", ""), float(val), start_date_iso))
            return pop_data if pop_data else None
        except Exception as exc:
            logger.debug("ndfd_pop_failed", station=station.station_id, error=str(exc))
            return None

    def invalidate_forecast_cache(self) -> None:
        """Clear the temperature forecast cache on NWP model update windows.

        Called at the start of ECMWF/GFS update windows so the bot picks up new
        model runs immediately instead of waiting for the cache TTL to expire.

        Only clears _cache (temperature). Precipitation, snowfall, and wind caches
        are NOT cleared — monthly aggregates don't change meaningfully within a
        6h NWP cycle, and clearing them wastes daily API quota (Open-Meteo 429).
        """
        self._cache.clear()

    def reset_climate_cycle(self) -> None:
        """Reset the per-cycle climate normal computation limiter.

        Called at the start of each scan cycle to allow up to 3 new climate
        normal computations per scan.
        """
        self._climate_computed_this_cycle = 0

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
