"""
S97: METAR Continuous Monitor — real-time airport weather observations.

Polls AWC (aviationweather.gov) API every 5 minutes for METAR observations
at all active same-day stations. Tracks running daily max temperature per
station and pushes to priority queue when observed temp crosses bracket
boundaries.

Architecture:
  - Started as background asyncio task by WeatherBot alongside ModelRunMonitor
  - Shares priority_queue with ModelRunMonitor
  - Falls through gracefully on any failure (backward compatible)
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

# AWC METAR API endpoint
_AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"


class MetarMonitor:
    """Background monitor for real-time METAR observations at active stations."""

    def __init__(
        self,
        stations: List[Any],
        priority_queue: asyncio.Queue,
        poll_interval: float = 300.0,  # 5 minutes
    ):
        self._stations = stations
        self._priority_queue = priority_queue
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

        # Running daily observations: station_id → (date, max_temp_observed)
        self._observations: Dict[str, Tuple[str, float]] = {}

        # Bucket boundaries for jump detection: station_id → set of boundary temps
        self._bucket_boundaries: Dict[str, Set[float]] = {}

    def set_bucket_boundaries(self, station_id: str, boundaries: Set[float]) -> None:
        """Set temperature boundaries for a station (from market bucket edges)."""
        self._bucket_boundaries[station_id] = boundaries

    def start(self) -> None:
        """Start the background monitoring task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("metar_monitor_started", stations=len(self._stations))

    async def stop(self) -> None:
        """Stop the background monitoring task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("metar_monitor_stopped")

    async def _monitor_loop(self) -> None:
        """Main polling loop — fetches METAR for all active stations."""
        while self._running:
            try:
                await self._poll_all_stations()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("metar_monitor_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _poll_all_stations(self) -> None:
        """Fetch METAR for all stations with same-day markets."""
        today_str = date.today().isoformat()

        # Filter to US stations only (METAR via AWC is US-focused)
        us_stations = [s for s in self._stations if s.temp_unit == "F" and s.station_id.startswith("K")]
        if not us_stations:
            return

        # Batch METAR request — AWC supports comma-separated ICAO codes
        icao_ids = ",".join(s.station_id for s in us_stations[:20])  # Cap at 20

        try:
            session = await self._get_session()
            params = {
                "ids": icao_ids,
                "format": "json",
                "hours": "1",  # Last hour only
            }
            async with session.get(
                _AWC_METAR_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.debug("metar_api_error", status=resp.status)
                    return
                data = await resp.json()

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("metar_fetch_failed", error=str(exc))
            return

        if not isinstance(data, list):
            return

        _updates = 0
        _boundary_crosses = 0

        for obs in data:
            try:
                station_id = obs.get("icaoId", "")
                temp_c = obs.get("temp")
                if station_id and temp_c is not None:
                    # Convert to station unit
                    station = next((s for s in us_stations if s.station_id == station_id), None)
                    if not station:
                        continue

                    temp = float(temp_c)
                    if station.temp_unit == "F":
                        temp = temp * 9.0 / 5.0 + 32.0

                    # Update running daily max
                    prev = self._observations.get(station_id)
                    if prev and prev[0] == today_str:
                        prev_max = prev[1]
                        if temp > prev_max:
                            self._observations[station_id] = (today_str, temp)
                            _updates += 1

                            # Check if crossing a bucket boundary
                            boundaries = self._bucket_boundaries.get(station_id, set())
                            for boundary in boundaries:
                                if prev_max < boundary <= temp:
                                    _boundary_crosses += 1
                                    logger.info(
                                        "metar_boundary_crossed",
                                        station=station_id,
                                        boundary=boundary,
                                        observed_max=round(temp, 1),
                                        prev_max=round(prev_max, 1),
                                    )
                                    try:
                                        self._priority_queue.put_nowait({
                                            "station": station,
                                            "target_date": date.today(),
                                            "observed_max": temp,
                                            "boundary": boundary,
                                            "source": "metar_boundary_cross",
                                        })
                                    except asyncio.QueueFull:
                                        pass
                    else:
                        # New day or first observation
                        self._observations[station_id] = (today_str, temp)
                        _updates += 1

            except (ValueError, TypeError, KeyError):
                continue

        if _updates > 0 or _boundary_crosses > 0:
            logger.info(
                "metar_poll_done",
                stations_polled=len(us_stations),
                observations=len(data),
                updates=_updates,
                boundary_crosses=_boundary_crosses,
            )

    def get_running_max(self, station_id: str) -> Optional[float]:
        """Get today's running max temperature for a station."""
        today_str = date.today().isoformat()
        obs = self._observations.get(station_id)
        if obs and obs[0] == today_str:
            return obs[1]
        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
