"""
METAR Client — async Aviation Weather Center API wrapper for real-time observations.

Resolution-day front-running:
  Polymarket weather markets have NO trading cutoff before resolution. On the
  resolution day, METAR T-group data arrives 1-5 minutes after each observation —
  well before Weather Underground compiles its daily high. This lets the bot
  track the running daily maximum temperature with 0.1°C precision in near-real-time,
  enabling decisive trades when the market misprices already-locked-in outcomes.

T-group format in METAR remarks section:
  T{sign_T}{T_tenths}{sign_D}{D_tenths}
  Example: T02890267
    - '0' = positive temperature
    - '289' = 28.9°C
    - '0' = positive dewpoint
    - '267' = 26.7°C
  Example: T11001267 → temperature = -10.0°C (sign bit '1' = negative)

Source: Aviation Weather Center (NOAA), free, no API key required.
  GET https://aviationweather.gov/api/data/metar?ids=KLGA&format=json&hours=N
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

_METAR_URL = "https://aviationweather.gov/api/data/metar"
# Cache TTL: 5 minutes — METAR observations are issued hourly (or more frequently at
# ASOS stations), so caching for 5 min avoids redundant calls within the same scan cycle.
_CACHE_TTL_SECONDS = 300.0


class MetarClient:
    """Fetch METAR observations from Aviation Weather Center API (free, no key)."""

    def __init__(self, cache_ttl: float = _CACHE_TTL_SECONDS):
        self._session: Optional[aiohttp.ClientSession] = None
        # Cache: "{station_id}:{date_iso}" → (expiry_monotonic, daily_max_celsius)
        self._daily_max_cache: Dict[str, Tuple[float, Optional[float]]] = {}
        self._cache_ttl = cache_ttl
        self._asos_client = None  # Set via set_asos_client() for 1-min resolution-day data

    def set_asos_client(self, asos_client) -> None:
        """Wire ASOS 1-minute client for enhanced resolution-day tracking."""
        self._asos_client = asos_client

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "PolymarketWeatherBot/1.0"},
            )
        return self._session

    @staticmethod
    def parse_t_group(remarks: str) -> Optional[float]:
        """Extract temperature from METAR T-group remark in remarks section.

        Format: T{sign_T}{T_tenths}{sign_D}{D_tenths}
          T02890267 → temp = +28.9°C
          T12890267 → temp = -28.9°C (sign bit '1' = negative)

        Returns temperature in °C with 0.1° precision, or None if no T-group found.
        """
        # T-group regex: T followed by sign (0/1), 3-digit temp tenths, sign, 3-digit dew tenths
        match = re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", remarks)
        if not match:
            return None
        sign_t = -1.0 if match.group(1) == "1" else 1.0
        temp_c = sign_t * int(match.group(2)) / 10.0
        return temp_c

    async def get_latest_metar(self, station_id: str) -> Optional[Dict]:
        """Fetch the most recent METAR observation for a station.

        Returns dict with keys:
          temp_c    — temperature in °C (T-group precision, 0.1°C)
          dew_c     — dewpoint in °C
          obs_time  — observation time string from API
          raw_text  — full raw METAR string
          station_id — echoed back for convenience

        Returns None if unavailable (API error, offline station, etc.)
        """
        session = await self._ensure_session()
        params = {"ids": station_id, "format": "json", "hours": 1}
        try:
            async with session.get(_METAR_URL, params=params) as resp:
                if resp.status != 200:
                    logger.debug(
                        "metar_api_error", station=station_id, status=resp.status
                    )
                    return None
                data = await resp.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                return None
            obs = data[0]
            raw = obs.get("rawOb", "") or obs.get("raw_text", "") or ""
            temp_c = obs.get("temp")
            dew_c = obs.get("dewp")
            # Prefer T-group (0.1°C) over API field (rounded to 1°C)
            if raw:
                t_group = self.parse_t_group(raw)
                if t_group is not None:
                    temp_c = t_group
            return {
                "temp_c": float(temp_c) if temp_c is not None else None,
                "dew_c": float(dew_c) if dew_c is not None else None,
                "obs_time": obs.get("obsTime") or obs.get("observation_time"),
                "raw_text": raw,
                "station_id": station_id,
            }
        except Exception as exc:
            logger.debug("metar_fetch_failed", station=station_id, error=str(exc))
            return None

    async def get_running_daily_max(
        self,
        station_id: str,
        target_date: date,
        temp_unit: str = "C",
    ) -> Optional[float]:
        """Fetch all METARs for target_date and return the running daily maximum.

        Queries the last 24h of METARs and filters observations to target_date.
        T-group precision (0.1°C) preferred over rounded API temp field.
        Results cached for cache_ttl seconds (default 5 min).

        Args:
            station_id: ICAO code (e.g. "KLGA", "EGLL")
            target_date: The date to find the daily max for
            temp_unit:   "F" to return °F (US stations), "C" for °C (default)

        Returns temperature in the requested unit, or None if unavailable.
        """
        cache_key = f"{station_id}:{target_date.isoformat()}"
        now_mono = time.monotonic()
        cached = self._daily_max_cache.get(cache_key)
        if cached and now_mono < cached[0]:
            val = cached[1]
            if val is None:
                return None
            return (val * 9.0 / 5.0 + 32.0) if temp_unit.upper() == "F" else val

        # Try 1-minute ASOS data on resolution day for US stations (higher granularity)
        is_resolution_day = target_date == datetime.now(timezone.utc).date()
        if is_resolution_day and self._asos_client and station_id.upper().startswith("K"):
            try:
                asos_max = await self._asos_client.get_running_daily_max(station_id, target_date, temp_unit)
                if asos_max is not None:
                    logger.debug("metar_using_asos_1min", station=station_id, max=round(asos_max, 1))
                    return asos_max
            except Exception as exc:
                logger.warning("asos_1min_fallback_to_metar", station=station_id, error=str(exc))

        session = await self._ensure_session()
        target_iso = target_date.isoformat()
        params = {"ids": station_id, "format": "json", "hours": 24}
        try:
            async with session.get(_METAR_URL, params=params) as resp:
                if resp.status != 200:
                    logger.debug(
                        "metar_daily_api_error",
                        station=station_id,
                        status=resp.status,
                    )
                    return None
                data = await resp.json()

            if not data or not isinstance(data, list):
                return None

            daily_max_c: Optional[float] = None
            for obs in data:
                raw = obs.get("rawOb", "") or obs.get("raw_text", "") or ""
                obs_time_str = str(obs.get("obsTime") or obs.get("observation_time") or "")
                # Filter to target date (obs_time format: "2026-03-06 14:00:00")
                if target_iso not in obs_time_str:
                    continue
                # Prefer T-group precision; fallback to API temp field
                temp_c: Optional[float] = None
                if raw:
                    temp_c = self.parse_t_group(raw)
                if temp_c is None:
                    raw_temp = obs.get("temp")
                    if raw_temp is not None:
                        try:
                            temp_c = float(raw_temp)
                        except (TypeError, ValueError):
                            pass
                if temp_c is not None:
                    if daily_max_c is None or temp_c > daily_max_c:
                        daily_max_c = temp_c

            # M6: Resolution-day cache TTL — use 60s (not 5min) for same-day
            # queries where midday highs can spike 2°C in 5 minutes.
            is_resolution_day = target_date == datetime.now(timezone.utc).date()
            effective_ttl = 60.0 if is_resolution_day else self._cache_ttl
            self._daily_max_cache[cache_key] = (now_mono + effective_ttl, daily_max_c)

            if daily_max_c is None:
                logger.debug(
                    "metar_no_observations_for_date",
                    station=station_id,
                    date=target_iso,
                )
                return None

            logger.debug(
                "metar_daily_max_computed",
                station=station_id,
                date=target_iso,
                daily_max_c=round(daily_max_c, 1),
            )
            return (daily_max_c * 9.0 / 5.0 + 32.0) if temp_unit.upper() == "F" else daily_max_c

        except Exception as exc:
            logger.debug(
                "metar_daily_max_failed", station=station_id, error=str(exc)
            )
            return None

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
