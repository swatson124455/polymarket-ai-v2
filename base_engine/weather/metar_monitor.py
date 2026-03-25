"""
S97: METAR Continuous Monitor — real-time airport weather observations.

Polls AWC (aviationweather.gov) API every 5 minutes for METAR observations
at all active same-day stations. Tracks running daily max temperature per
station and pushes to priority queue when observed temp crosses bracket
boundaries.

S102: Daily max observations persisted to Redis with 24h TTL so they survive
restarts. Without this, boundary crossing detection resets to zero on restart
and may miss events until a new observation exceeds the (lost) prior max.

Architecture:
  - Started as background asyncio task by WeatherBot alongside ModelRunMonitor
  - Shares priority_queue with ModelRunMonitor
  - Falls through gracefully on any failure (backward compatible)
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

_REDIS_KEY_PREFIX = "metar:daily_max:"

# AWC METAR API endpoint
_AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"


class MetarMonitor:
    """Background monitor for real-time METAR observations at active stations."""

    def __init__(
        self,
        stations: List[Any],
        priority_queue: asyncio.Queue,
        poll_interval: float = 300.0,  # 5 minutes
        redis_cache: Any = None,
    ):
        self._stations = stations
        self._priority_queue = priority_queue
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._redis_cache = redis_cache  # S102: optional Redis for persistence

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
            _t0 = time.monotonic()
            try:
                # S99: Hard timeout prevents indefinite blocking under event loop saturation
                await asyncio.wait_for(self._poll_all_stations(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("metar_poll_timeout", elapsed_ms=round((time.monotonic() - _t0) * 1000))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("metar_monitor_error", error=str(exc))
            # S99: Jitter avoids collision with scan loop
            await asyncio.sleep(self._poll_interval + random.uniform(0, 30))

    async def _poll_all_stations(self) -> None:
        """Fetch METAR for all stations with same-day markets."""
        today_str = datetime.now(timezone.utc).date().isoformat()

        # Filter to US stations only (METAR via AWC is US-focused)
        us_stations = [s for s in self._stations if s.temp_unit == "F" and s.station_id.startswith("K")]
        if not us_stations:
            return

        # S99: Batch METAR requests in groups of 20 (AWC limit per request)
        _batch_size = 20
        data: List[Dict] = []
        session = await self._get_session()
        _poll_t0 = time.monotonic()
        _batches_sent = 0

        for i in range(0, len(us_stations), _batch_size):
            batch = us_stations[i:i + _batch_size]
            icao_ids = ",".join(s.station_id for s in batch)
            try:
                params = {
                    "ids": icao_ids,
                    "format": "json",
                    "hours": "1",
                }
                async with session.get(
                    _AWC_METAR_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    _batches_sent += 1
                    if resp.status == 200:
                        batch_data = await resp.json()
                        if isinstance(batch_data, list):
                            data.extend(batch_data)
                    else:
                        logger.debug("metar_api_error", status=resp.status, batch=i)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.debug("metar_batch_fetch_failed", batch=i, error=str(exc))

        if not data:
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
                                            "target_date": datetime.now(timezone.utc).date(),
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

        _poll_ms = round((time.monotonic() - _poll_t0) * 1000)
        logger.info(
            "metar_poll_done",
            stations_polled=len(us_stations),
            batches=_batches_sent,
            observations=len(data),
            updates=_updates,
            boundary_crosses=_boundary_crosses,
            poll_ms=_poll_ms,
        )

        # S102: Persist daily max observations to Redis after each poll
        if _updates > 0:
            await self._save_observations_to_redis()

    def get_running_max(self, station_id: str) -> Optional[float]:
        """Get today's running max temperature for a station."""
        today_str = datetime.now(timezone.utc).date().isoformat()
        obs = self._observations.get(station_id)
        if obs and obs[0] == today_str:
            return obs[1]
        return None

    async def _save_observations_to_redis(self) -> None:
        """Persist daily max observations to Redis with 24h TTL.

        S102: Each station's daily max is stored as a separate key so TTL
        naturally expires stale data. Only saves today's observations.
        """
        if self._redis_cache is None or not getattr(self._redis_cache, "redis", None):
            return
        today_str = datetime.now(timezone.utc).date().isoformat()
        saved = 0
        for station_id, (obs_date, max_temp) in self._observations.items():
            if obs_date != today_str:
                continue
            try:
                key = f"{_REDIS_KEY_PREFIX}{station_id}"
                value = json.dumps({"date": obs_date, "max_temp": max_temp})
                await self._redis_cache.set(key, value, ttl=86400)
                saved += 1
            except Exception:
                pass  # Non-fatal: best-effort persistence
        if saved:
            logger.debug("metar_redis_saved", stations=saved)

    async def restore_from_redis(self) -> None:
        """Reload daily max observations from Redis on startup.

        S102: Prevents boundary crossing detection from resetting after restart.
        Only restores today's observations (stale keys auto-expire via TTL).
        """
        if self._redis_cache is None or not getattr(self._redis_cache, "redis", None):
            return
        try:
            keys = await self._redis_cache.redis.keys(f"{_REDIS_KEY_PREFIX}*")
            today_str = datetime.now(timezone.utc).date().isoformat()
            restored = 0
            for key in keys:
                try:
                    raw = await self._redis_cache.get(key)
                    if raw is None:
                        continue
                    data = json.loads(raw)
                    obs_date = data.get("date", "")
                    max_temp = data.get("max_temp")
                    if obs_date == today_str and max_temp is not None:
                        station_id = key.split(_REDIS_KEY_PREFIX, 1)[-1]
                        self._observations[station_id] = (obs_date, float(max_temp))
                        restored += 1
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
            if restored:
                logger.info("metar_redis_restored", stations=restored)
        except Exception as exc:
            logger.warning("metar_redis_restore_failed", error=str(exc))

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
