"""
S97: Model Run Monitor — event-driven forecast pipeline.

Polls NOMADS/AWS for new GFS/HRRR/ECMWF model runs. When a new run is
detected, pre-fetches forecasts for all active stations and populates
the forecast_client._model_run_cache. Also implements jump detection:
when ensemble mean shifts ≥3°F between model runs, pushes to a priority
queue for immediate evaluation by WeatherBot.

Architecture:
  - Started as background asyncio task by WeatherBot on first scan
  - Shares forecast_client reference (writes to _model_run_cache)
  - Priority queue (asyncio.Queue) consumed by WeatherBot scan loop
  - Falls through gracefully on any failure (backward compatible)
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

# Model run schedule (UTC hours when new runs become available)
# GFS: 00z, 06z, 12z, 18z — available ~3.5h after init time
# HRRR: hourly — available ~1h after init time
# ECMWF: 00z, 12z — available ~6h after init time (via Open-Meteo)
_GFS_INIT_HOURS = [0, 6, 12, 18]
_ECMWF_INIT_HOURS = [0, 12]


class ModelRunMonitor:
    """Background monitor that detects new model runs and pre-fetches forecasts."""

    def __init__(
        self,
        forecast_client: Any,
        stations: List[Any],
        priority_queue: asyncio.Queue,
        poll_interval: float = 300.0,  # 5 minutes
    ):
        self._fc = forecast_client
        self._stations = stations
        self._priority_queue = priority_queue
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Track last known model run init times
        self._last_gfs_run: Optional[str] = None      # e.g. "2026031612"
        self._last_ecmwf_run: Optional[str] = None
        self._last_hrrr_run: Optional[str] = None

        # Prior forecasts for jump detection: station_id:date → ensemble_mean
        self._prior_forecasts: Dict[str, float] = {}

        # Jump threshold (°F)
        self._jump_threshold: float = 3.0

    def start(self) -> None:
        """Start the background monitoring task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("model_run_monitor_started")

    async def stop(self) -> None:
        """Stop the background monitoring task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("model_run_monitor_stopped")

    async def _monitor_loop(self) -> None:
        """Main polling loop — checks for new model runs every poll_interval."""
        while self._running:
            try:
                await self._check_for_new_runs()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("model_run_monitor_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _check_for_new_runs(self) -> None:
        """Check NOMADS for new GFS/ECMWF model runs."""
        now_utc = datetime.now(timezone.utc)
        new_run_detected = False

        # Check GFS — poll NOMADS index
        gfs_run = await self._check_gfs_availability(now_utc)
        if gfs_run and gfs_run != self._last_gfs_run:
            logger.info("model_run_new_gfs", run=gfs_run, prev=self._last_gfs_run)
            self._last_gfs_run = gfs_run
            new_run_detected = True

        # Check ECMWF — poll Open-Meteo response freshness
        ecmwf_run = self._estimate_ecmwf_run(now_utc)
        if ecmwf_run and ecmwf_run != self._last_ecmwf_run:
            logger.info("model_run_new_ecmwf", run=ecmwf_run, prev=self._last_ecmwf_run)
            self._last_ecmwf_run = ecmwf_run
            new_run_detected = True

        if new_run_detected:
            await self._refresh_forecasts()

    async def _check_gfs_availability(self, now_utc: datetime) -> Optional[str]:
        """Check NOMADS for latest available GFS run.

        Probes the AWS Open Data bucket which mirrors NOMADS with lower latency.
        """
        try:
            session = await self._get_session()
            # Check from most recent init time backwards
            for hours_back in range(0, 24, 6):
                check_time = now_utc - timedelta(hours=hours_back)
                run_date = check_time.strftime("%Y%m%d")
                # Find the most recent init hour at or before check_time
                init_hour = max(h for h in _GFS_INIT_HOURS if h <= check_time.hour) if check_time.hour >= 0 else 18
                run_id = f"{run_date}{init_hour:02d}"

                # GFS data availability: ~3.5h after init. Check if f003 exists.
                url = f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{run_date}/{init_hour:02d}/atmos/gfs.t{init_hour:02d}z.pgrb2.0p25.f003"
                try:
                    async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            return run_id
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue
        except Exception as exc:
            logger.debug("model_run_gfs_check_failed", error=str(exc))
        return None

    def _estimate_ecmwf_run(self, now_utc: datetime) -> Optional[str]:
        """Estimate current ECMWF run based on time.

        ECMWF 00z available ~7h after init (07:00 UTC), 12z at ~19:00 UTC.
        """
        hour = now_utc.hour
        run_date = now_utc.strftime("%Y%m%d")
        if hour >= 19:
            return f"{run_date}12"
        elif hour >= 7:
            return f"{run_date}00"
        else:
            # Previous day's 12z
            prev = (now_utc - timedelta(days=1)).strftime("%Y%m%d")
            return f"{prev}12"

    async def _refresh_forecasts(self) -> None:
        """Re-fetch forecasts for all active stations on new model run.

        Populates _model_run_cache and checks for forecast jumps.
        """
        today = date.today()
        target_dates = [today + timedelta(days=d) for d in range(0, 8)]

        # Only refresh stations that have active markets (limit API calls)
        # Use all stations for now — discovery cache will tell us which are active
        _refreshed = 0
        _jumps = 0

        for station in self._stations:
            for target_date in target_dates:
                try:
                    # Fetch fresh forecast (bypasses cache by going to _model_run_cache)
                    forecast = await self._fc.get_combined_forecast(station, target_date)
                    if not forecast or not forecast.ensemble_members:
                        continue

                    cache_key = f"{station.station_id}:{target_date.isoformat()}"

                    # Store in model_run_cache
                    self._fc._model_run_cache[cache_key] = forecast
                    _refreshed += 1

                    # Jump detection: compare to prior ensemble mean
                    new_mean = sum(forecast.ensemble_members) / len(forecast.ensemble_members)
                    prior_mean = self._prior_forecasts.get(cache_key)

                    if prior_mean is not None:
                        delta = abs(new_mean - prior_mean)
                        if delta >= self._jump_threshold:
                            _jumps += 1
                            logger.info(
                                "model_run_jump_detected",
                                station=station.station_id,
                                date=target_date.isoformat(),
                                prior_mean=round(prior_mean, 1),
                                new_mean=round(new_mean, 1),
                                delta=round(delta, 1),
                            )
                            # Push to priority queue for immediate evaluation
                            try:
                                self._priority_queue.put_nowait({
                                    "station": station,
                                    "target_date": target_date,
                                    "delta": delta,
                                    "source": "model_run_jump",
                                })
                            except asyncio.QueueFull:
                                pass  # Non-fatal: normal scan will pick it up

                    self._prior_forecasts[cache_key] = new_mean

                except Exception as exc:
                    logger.debug(
                        "model_run_refresh_failed",
                        station=station.station_id,
                        date=target_date.isoformat(),
                        error=str(exc),
                    )

                # Small jitter between stations to avoid API burst
                await asyncio.sleep(0.05)

        logger.info(
            "model_run_refresh_done",
            stations=len(self._stations),
            dates=len(target_dates),
            refreshed=_refreshed,
            jumps=_jumps,
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session from forecast client."""
        if self._fc._session is None or self._fc._session.closed:
            self._fc._session = aiohttp.ClientSession()
        return self._fc._session
