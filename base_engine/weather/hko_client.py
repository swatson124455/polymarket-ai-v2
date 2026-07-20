"""HKO Client — async Hong Kong Observatory open-data wrapper for Hong Kong's
RESOLUTION-SOURCE temperature.

Why this exists (S233): Polymarket's Hong Kong temperature markets resolve on the
HK Observatory's urban HQ station, NOT the VHHH airport METAR the bot grounds every
other city on. HK is therefore in the "resolution source is not a METAR station"
class (spec §S232 REGISTRY-ADDITIONS EVIDENCE) and is knowingly mis-grounded to
VHHH today. This client supplies the HKO HQ reading so a later (operator-gated)
dispatch change can ground HK on its true resolution station.

Two HKO open-data endpoints (keyless, official, verified live 2026-07-20):
  rhrread  — https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en
             temperature.data[] = [{place, value, unit:"C"}], recordTime HOURLY (HKT).
             The HQ reading is place == "Hong Kong Observatory". This is the CURRENT
             hour only, so the resolution-DAY running max must be ACCUMULATED across
             polls (get_running_daily_max, cache-backed).
  CLMMAXT  — https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT
             &rformat=json&station=HKO&year=YYYY — authoritative daily-max history,
             but LAGS ~3 weeks, so it serves only DELAYED reconciliation, never the
             next-day calibration truth.

Interface mirrors MetarClient.get_running_daily_max so the dispatch is a drop-in.
All temperatures are Celsius (HK is a °C city); temp_unit="F" converts on return.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

import aiohttp
from structlog import get_logger

logger = get_logger()

_RHRREAD_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
_OPENDATA_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
_HQ_PLACE = "Hong Kong Observatory"
# rhrread updates hourly; a 60s cache is plenty and avoids redundant calls within
# a scan cycle (matches MetarClient's resolution-day cadence).
_CACHE_TTL_SECONDS = 60.0
# HK is UTC+8 year-round (no DST) — the resolution "day" is the HK local day.
_HK_TZ = timezone(timedelta(hours=8))


def _c_to_unit(temp_c: Optional[float], temp_unit: str) -> Optional[float]:
    if temp_c is None:
        return None
    return (temp_c * 9.0 / 5.0 + 32.0) if temp_unit.upper() == "F" else temp_c


class HKOClient:
    """Fetch Hong Kong Observatory HQ temperature (free, no key)."""

    def __init__(self, cache_ttl: float = _CACHE_TTL_SECONDS):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache_ttl = cache_ttl

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "PolymarketWeatherBot/1.0"},
            )
        return self._session

    @staticmethod
    def parse_hq_temp(rhrread: dict) -> Optional[Tuple[float, str]]:
        """Extract (temp_c, record_time_iso) for the HK Observatory HQ reading
        from an rhrread payload. Returns None if the HQ reading is absent.

        Pure function (no I/O) so it can be unit-tested against a captured sample.
        """
        temp = (rhrread or {}).get("temperature") or {}
        record_time = temp.get("recordTime")
        for item in temp.get("data") or []:
            if (item.get("place") or "") == _HQ_PLACE:
                val = item.get("value")
                if val is None:
                    return None
                try:
                    return float(val), record_time
                except (TypeError, ValueError):
                    return None
        return None

    async def get_current_hq_temp(self) -> Optional[Tuple[float, str]]:
        """Current HK Observatory HQ temperature as (°C, recordTime ISO), or None."""
        session = await self._ensure_session()
        params = {"dataType": "rhrread", "lang": "en"}
        try:
            async with session.get(_RHRREAD_URL, params=params) as resp:
                if resp.status != 200:
                    logger.debug("hko_rhrread_api_error", status=resp.status)
                    return None
                data = await resp.json()
            return self.parse_hq_temp(data)
        except Exception as exc:
            logger.debug("hko_rhrread_failed", error=str(exc))
            return None

    async def get_running_daily_max(
        self,
        target_date: date,
        temp_unit: str = "C",
        cache=None,
    ) -> Optional[float]:
        """Resolution-DAY running maximum HQ temperature.

        rhrread carries only the CURRENT hour, so the day's max is accumulated
        across polls: each call reads the stored max for target_date from `cache`,
        folds in the current reading (only if its recordTime falls on target_date
        in HK local time), writes the new max back with a day-length TTL, and
        returns it. Interface + (value, unit) contract match
        MetarClient.get_running_daily_max so a dispatch can swap them 1:1.

        FAIL-CLOSED contract: the accumulation REQUIRES a working, persistent
        cache. A single instantaneous rhrread reading is NOT a daily max, and
        feeding one to the resolution-day override's aggressive branches would
        manufacture a near-certain YES/NO on wrong information in the highest-
        leverage window. So when a working cache is unavailable — `cache` is
        None, its Redis handle is down (`cache.redis` falsy), or a Redis op
        errors — this returns None (the caller then leaves probabilities
        unchanged). This mirrors the codebase `cache and cache.redis` idiom
        (base_engine.py) and the c1 fail-closed lesson (RedisCache swallows
        errors to None, so a downed Redis is indistinguishable from a miss unless
        we check the handle + pass raise_on_error).

        cache: the bot's RedisCache — must expose async get/set with the shared
               `raise_on_error` kwarg. `getattr(self.base_engine, "cache", None)`.
        Returns the accumulated max in the requested unit, or None.
        """
        if cache is None or not getattr(cache, "redis", None):
            return None   # no working cache → cannot accumulate → fail CLOSED
        key = self._runmax_key(target_date)
        try:
            raw = await cache.get(key, raise_on_error=True)
            stored = float(raw) if raw is not None else None

            candidate = None
            current = await self.get_current_hq_temp()
            if current is not None:
                temp_c, record_time = current
                if self._is_target_day(record_time, target_date):
                    candidate = temp_c

            new_max = max([v for v in (stored, candidate) if v is not None], default=None)
            if new_max is not None and new_max != stored:
                # 30h TTL always covers the resolution day (simpler than
                # seconds-to-midnight) so a late reconciliation still sees it.
                await cache.set(key, new_max, 30 * 3600, raise_on_error=True)
            return _c_to_unit(new_max, temp_unit)
        except (ValueError, TypeError):
            return None   # corrupt stored value → treat as no data
        except Exception as exc:
            # Redis error mid-accumulation → the max cannot be trusted → fail CLOSED.
            logger.warning("hko_runmax_failed_closed", error=str(exc))
            return None

    @staticmethod
    def _is_target_day(record_time: Optional[str], target_date: date) -> bool:
        """Whether an rhrread recordTime falls on target_date in HK local time.
        Unparseable/missing time → True (do not silently drop the observation)."""
        if not record_time:
            return True
        try:
            return datetime.fromisoformat(record_time).astimezone(_HK_TZ).date() == target_date
        except (ValueError, TypeError):
            return True

    @staticmethod
    def _runmax_key(target_date: date) -> str:
        return "hko:runmax:%s" % target_date.isoformat()

    async def _read_stored_max(self, cache, target_date: date) -> Optional[float]:
        try:
            v = await cache.get(self._runmax_key(target_date))
        except Exception as exc:
            logger.debug("hko_runmax_read_failed", error=str(exc))
            return None
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    async def get_daily_max_history(self, year: int) -> List[Tuple[date, float]]:
        """CLMMAXT authoritative daily-max history for HKO (°C). Lags ~3 weeks —
        for delayed reconciliation only. Returns [(date, max_c)] sorted; [] on error."""
        session = await self._ensure_session()
        params = {"dataType": "CLMMAXT", "rformat": "json", "station": "HKO", "year": str(year)}
        out: List[Tuple[date, float]] = []
        try:
            async with session.get(_OPENDATA_URL, params=params) as resp:
                if resp.status != 200:
                    logger.debug("hko_clmmaxt_api_error", status=resp.status)
                    return out
                data = await resp.json()
        except Exception as exc:
            logger.debug("hko_clmmaxt_failed", error=str(exc))
            return out
        for row in (data or {}).get("data") or []:
            # row = [Year, Month, Day, Value, Completeness]
            try:
                y, m, d = int(row[0]), int(row[1]), int(row[2])
                val = float(row[3])
            except (IndexError, TypeError, ValueError):
                continue
            out.append((date(y, m, d), val))
        out.sort()
        return out

    async def get_resolved_daily_max(
        self,
        target_date: date,
        temp_unit: str = "C",
        cache=None,
    ) -> Optional[float]:
        """RESOLVED daily max for a PAST target_date, for calibration backfill.

        Primary source = the rhrread running-max we accumulated on that day
        (stored in `cache` by get_running_daily_max) — available the next day.
        Fallback = CLMMAXT authoritative daily-max (lags ~3 weeks, so it only
        covers dates older than ~3 weeks). Returns None if neither has it.
        Mirrors the (value, unit) contract of a WU/METAR daily-high fetch so the
        calibration path can swap 1:1.
        """
        if cache is not None:
            stored = await self._read_stored_max(cache, target_date)
            if stored is not None:
                return _c_to_unit(stored, temp_unit)
        for d, val in await self.get_daily_max_history(target_date.year):
            if d == target_date:
                return _c_to_unit(val, temp_unit)
        return None

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
