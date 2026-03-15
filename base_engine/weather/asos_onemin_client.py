"""
ASOS 1-Minute Client — Iowa Environmental Mesonet (IEM) API for sub-hourly observations.

Provides 1-minute resolution temperature data from US ASOS stations. On resolution
day, this gives ~59 minutes faster detection of daily high/low temperatures compared
to standard hourly METAR observations.

Source: Iowa Environmental Mesonet (Iowa State University), free, no API key.
  https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py

US stations only (ICAO codes starting with K). International stations fall back to METAR.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

_IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
_CACHE_TTL_SECONDS = 120.0  # 2 minutes — balance freshness vs politeness


class AsosOneMinClient:
    """Fetch 1-minute ASOS observations from Iowa Environmental Mesonet (free, US only)."""

    def __init__(self, cache_ttl: float = _CACHE_TTL_SECONDS):
        self._session: Optional[aiohttp.ClientSession] = None
        # Cache: "{station_id}:{date_iso}" -> (expiry_mono, daily_max_celsius)
        self._daily_max_cache: Dict[str, Tuple[float, Optional[float]]] = {}
        self._cache_ttl = cache_ttl

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "PolymarketAI/1.0 (weather research)"},
            )
        return self._session

    async def get_running_daily_max(
        self,
        station_id: str,
        target_date: date,
        temp_unit: str = "C",
    ) -> Optional[float]:
        """Fetch 1-minute ASOS observations for target_date and return the running daily max.

        Args:
            station_id: ICAO code (e.g. "KLGA"). Must be a US station (K-prefix).
            target_date: The date to find the daily max for.
            temp_unit: "F" for Fahrenheit, "C" for Celsius (default).

        Returns temperature in requested unit, or None if unavailable.
        """
        # IEM only covers US ASOS stations
        if not station_id or not station_id.upper().startswith("K"):
            return None

        cache_key = f"asos1m:{station_id}:{target_date.isoformat()}"
        now_mono = time.monotonic()
        cached = self._daily_max_cache.get(cache_key)
        if cached and now_mono < cached[0]:
            val = cached[1]
            if val is None:
                return None
            return (val * 9.0 / 5.0 + 32.0) if temp_unit.upper() == "F" else val

        session = await self._ensure_session()

        # IEM expects date range: target_date 00:00 to target_date+1 00:00
        end_date = target_date + timedelta(days=1)
        params = {
            "station": station_id.upper(),
            "sts": f"{target_date.year}/{target_date.month}/{target_date.day}/0000",
            "ets": f"{end_date.year}/{end_date.month}/{end_date.day}/0000",
            "vars": "tmpf",  # Temperature in Fahrenheit (IEM default)
            "sample": "1min",
            "what": "dl",
            "delim": "comma",
        }

        try:
            async with session.get(_IEM_URL, params=params) as resp:
                if resp.status != 200:
                    logger.debug("asos_1min_api_error", station=station_id, status=resp.status)
                    return None
                text = await resp.text()

            if not text or "ERROR" in text[:100]:
                logger.debug("asos_1min_empty_or_error", station=station_id)
                return None

            daily_max_f: Optional[float] = None
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                tmpf_str = row.get("tmpf", "").strip()
                if not tmpf_str or tmpf_str == "M":  # M = missing
                    continue
                try:
                    tmpf = float(tmpf_str)
                except (ValueError, TypeError):
                    continue
                if daily_max_f is None or tmpf > daily_max_f:
                    daily_max_f = tmpf

            # Convert F→C for storage (IEM returns Fahrenheit)
            daily_max_c: Optional[float] = None
            if daily_max_f is not None:
                daily_max_c = (daily_max_f - 32.0) * 5.0 / 9.0

            # Resolution-day: shorter cache (60s) for real-time tracking
            is_resolution_day = target_date == datetime.now(timezone.utc).date()
            effective_ttl = 60.0 if is_resolution_day else self._cache_ttl
            self._daily_max_cache[cache_key] = (now_mono + effective_ttl, daily_max_c)

            if daily_max_c is None:
                logger.debug("asos_1min_no_data", station=station_id, date=target_date.isoformat())
                return None

            logger.debug(
                "asos_1min_daily_max",
                station=station_id,
                date=target_date.isoformat(),
                max_c=round(daily_max_c, 1),
                max_f=round(daily_max_f, 1) if daily_max_f else None,
            )
            return (daily_max_c * 9.0 / 5.0 + 32.0) if temp_unit.upper() == "F" else daily_max_c

        except Exception as exc:
            logger.debug("asos_1min_failed", station=station_id, error=str(exc))
            return None

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
