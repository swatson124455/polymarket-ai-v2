"""
Market Mapper — parses Polymarket weather market questions into structured bucket data.

Handles four temperature question formats + three precipitation formats:
  Temperature:
    1. "Will the highest temperature in NYC be between 48-49°F on January 22?"
    2. "Will the highest temperature in NYC be 42°F or below on January 22?"
    3. "Will the highest temperature in NYC be 55°F or higher on January 22?"
    4. "Will the highest temperature in London be 10°C on February 5?"
  Precipitation:
    5. "between 3-4 inches" / "between 50-75 mm"
    6. "2 inches or below" / "50 mm or below"
    7. "5 inches or higher" / "75 mm or higher"
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

from bots.weather.engine.base_engine.weather.station_registry import WeatherStation, lookup_station

logger = get_logger()

# ── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class TemperatureBucket:
    """A single temperature outcome market within a group."""

    market_id: str
    token_id: str           # YES token ID
    no_token_id: str        # NO token ID
    yes_price: float
    bucket_type: str        # "range", "at_or_below", "at_or_higher", "exact"
    low_bound: Optional[float]   # Inclusive lower bound (None for at_or_below)
    high_bound: Optional[float]  # Inclusive upper bound (None for at_or_higher)
    temp_unit: str          # "F" or "C"


@dataclass
class WeatherMarketGroup:
    """All bucket markets for a single city + target date."""

    city: str               # Normalized city name from station
    target_date: date
    station: WeatherStation
    buckets: List[TemperatureBucket] = field(default_factory=list)
    slug_prefix: str = ""
    temp_unit: str = "F"


@dataclass
class PrecipitationBucket:
    """A single precipitation outcome market within a group."""

    market_id: str
    token_id: str
    no_token_id: str
    yes_price: float
    bucket_type: str                # "range", "at_or_below", "at_or_higher"
    low_bound: Optional[float]
    high_bound: Optional[float]
    precip_unit: str                # "in" or "mm"


@dataclass
class PrecipitationMarketGroup:
    """All precipitation bucket markets for a single city + time period."""

    city: str
    target_date: date
    station: WeatherStation
    buckets: List[PrecipitationBucket] = field(default_factory=list)
    slug_prefix: str = ""
    precip_unit: str = "in"
    period: str = "daily"           # daily, monthly


@dataclass
class SnowfallBucket:
    """A single snowfall outcome market within a group."""

    market_id: str
    token_id: str
    no_token_id: str
    yes_price: float
    bucket_type: str                # "range", "at_or_below", "at_or_higher"
    low_bound: Optional[float]
    high_bound: Optional[float]
    snow_unit: str                  # "in" or "cm"


@dataclass
class SnowfallMarketGroup:
    """All snowfall bucket markets for a single city + time period."""

    city: str
    target_date: date
    station: WeatherStation
    buckets: List[SnowfallBucket] = field(default_factory=list)
    slug_prefix: str = ""
    snow_unit: str = "in"


@dataclass
class WindBucket:
    """A single wind gust outcome market within a group."""

    market_id: str
    token_id: str
    no_token_id: str
    yes_price: float
    bucket_type: str                # "range", "at_or_below", "at_or_higher"
    low_bound: Optional[float]
    high_bound: Optional[float]
    wind_unit: str                  # "mph" or "kmh"


@dataclass
class WindMarketGroup:
    """All wind gust bucket markets for a single city + time period."""

    city: str
    target_date: date
    station: WeatherStation
    buckets: List[WindBucket] = field(default_factory=list)
    slug_prefix: str = ""
    wind_unit: str = "mph"


# ── Regex patterns ────────────────────────────────────────────────────────

# Pattern 1: "between 48-49°F"
_RE_RANGE = re.compile(
    r"highest\s+temperature\s+in\s+(.+?)\s+be\s+between\s+"
    r"(-?\d+)\s*[-–]\s*(-?\d+)\s*°\s*([FC])\s+on\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

# Pattern 2: "42°F or below"
_RE_AT_OR_BELOW = re.compile(
    r"highest\s+temperature\s+in\s+(.+?)\s+be\s+"
    r"(-?\d+)\s*°\s*([FC])\s+or\s+below\s+on\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

# Pattern 3: "55°F or higher"
_RE_AT_OR_HIGHER = re.compile(
    r"highest\s+temperature\s+in\s+(.+?)\s+be\s+"
    r"(-?\d+)\s*°\s*([FC])\s+or\s+higher\s+on\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

# Pattern 4: exact — "10°C on February 5" (no "between", no "or below/higher")
_RE_EXACT = re.compile(
    r"highest\s+temperature\s+in\s+(.+?)\s+be\s+"
    r"(-?\d+)\s*°\s*([FC])\s+on\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

# ── Precipitation patterns ─────────────────────────────────────────────────
# Polymarket precipitation markets use patterns like:
#   "Will total precipitation in NYC be between 3-4 inches in March 2026?"
#   "Will total precipitation in NYC be 2 inches or below in March 2026?"
#   "Will total precipitation in NYC be 5 inches or higher in March 2026?"

_RE_PRECIP_RANGE = re.compile(
    r"(?:total\s+)?precipitation\s+in\s+(.+?)\s+be\s+between\s+"
    r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(inch(?:es)?|mm|millimeters?)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_PRECIP_AT_OR_BELOW = re.compile(
    r"(?:total\s+)?precipitation\s+in\s+(.+?)\s+be\s+"
    r"(\d+(?:\.\d+)?)\s*(inch(?:es)?|mm|millimeters?)\s+or\s+(?:below|less)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_PRECIP_AT_OR_HIGHER = re.compile(
    r"(?:total\s+)?precipitation\s+in\s+(.+?)\s+be\s+"
    r"(\d+(?:\.\d+)?)\s*(inch(?:es)?|mm|millimeters?)\s+or\s+(?:higher|more|above)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_PRECIP_QUICK = re.compile(
    r"precipitation",
    re.IGNORECASE,
)

# V2 patterns — actual Polymarket format (2026-03):
#   "Will NYC have between 3 and 4 inches of precipitation in March?"
#   "Will NYC have less than 2 inches of precipitation in March?"
#   "Will NYC have more than 6 inches of precipitation in March?"
_RE_PRECIP_RANGE_V2 = re.compile(
    r"(?:Will\s+)?(.+?)\s+have\s+between\s+"
    r"(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)\s+(inch(?:es)?|mm|millimeters?)\s+"
    r"of\s+precipitation\s+(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_PRECIP_BELOW_V2 = re.compile(
    r"(?:Will\s+)?(.+?)\s+have\s+(?:less\s+than|under|at\s+most|no\s+more\s+than)\s+"
    r"(\d+(?:\.\d+)?)\s+(inch(?:es)?|mm|millimeters?)\s+"
    r"of\s+precipitation\s+(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_PRECIP_HIGHER_V2 = re.compile(
    r"(?:Will\s+)?(.+?)\s+have\s+(?:more\s+than|over|at\s+least|exceed(?:ing)?)\s+"
    r"(\d+(?:\.\d+)?)\s+(inch(?:es)?|mm|millimeters?)\s+"
    r"of\s+precipitation\s+(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

# ── Snowfall patterns ─────────────────────────────────────────────────────
# "Will total snowfall in NYC be between 3-6 inches on January 15?"
# "Will snowfall in NYC be 2 inches or below on January 15?"

_RE_SNOW_RANGE = re.compile(
    r"(?:total\s+)?snowfall\s+in\s+(.+?)\s+be\s+between\s+"
    r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(inch(?:es)?|cm|centimeters?)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_SNOW_AT_OR_BELOW = re.compile(
    r"(?:total\s+)?snowfall\s+in\s+(.+?)\s+be\s+"
    r"(\d+(?:\.\d+)?)\s*(inch(?:es)?|cm|centimeters?)\s+or\s+(?:below|less)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_SNOW_AT_OR_HIGHER = re.compile(
    r"(?:total\s+)?snowfall\s+in\s+(.+?)\s+be\s+"
    r"(\d+(?:\.\d+)?)\s*(inch(?:es)?|cm|centimeters?)\s+or\s+(?:higher|more|above)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_SNOW_QUICK = re.compile(
    r"snowfall\s+in\s+",
    re.IGNORECASE,
)

# ── Wind gust patterns ────────────────────────────────────────────────────
# "Will wind gusts in NYC be between 30-40 mph on March 12?"
# "Will wind gusts in NYC exceed 50 mph on March 12?"
# "Will wind gusts in NYC be 20 mph or below on March 12?"

_RE_WIND_RANGE = re.compile(
    r"wind\s+gusts?\s+in\s+(.+?)\s+be\s+between\s+"
    r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(mph|km/?h|knots?)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_WIND_AT_OR_BELOW = re.compile(
    r"wind\s+gusts?\s+in\s+(.+?)\s+be\s+"
    r"(\d+(?:\.\d+)?)\s*(mph|km/?h|knots?)\s+or\s+(?:below|less)\s+"
    r"(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_WIND_AT_OR_HIGHER = re.compile(
    r"wind\s+gusts?\s+in\s+(.+?)\s+"
    r"(?:be|exceed|reach)\s+(\d+(?:\.\d+)?)\s*(mph|km/?h|knots?)\s+"
    r"(?:or\s+(?:higher|more|above)\s+)?(?:in|on|during)\s+(.+?)[\?\.]?\s*$",
    re.IGNORECASE,
)

_RE_WIND_QUICK = re.compile(
    r"wind\s+gusts?\s+in\s+",
    re.IGNORECASE,
)

# Quick pre-filter — matches "highest/high/maximum temperature in" and decimal variants
_RE_WEATHER_QUICK = re.compile(
    r"(?:highest|high|max(?:imum)?)\s+temp(?:erature)?\s+in\s+",
    re.IGNORECASE,
)

# Alternative pre-filter — catches "temperature in [city] ... degrees Fahrenheit/Celsius"
# Used as second-pass for markets that spell out the unit instead of using °F/°C.
_RE_WEATHER_ALT = re.compile(
    r"temperature\s+in\s+.+?\s+(?:reach|be|exceed|hit)\s+-?\d+.*?"
    r"(?:degrees?\s+[FC]|°\s*[FC]|fahrenheit|celsius)",
    re.IGNORECASE,
)

# ── Date parsing ──────────────────────────────────────────────────────────

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

_RE_DATE = re.compile(
    r"(\w+)\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?",
    re.IGNORECASE,
)


def _parse_date(date_str: str) -> Optional[date]:
    """Parse 'January 22', 'Feb 3, 2026', etc. into a date object."""
    m = _RE_DATE.match(date_str.strip())
    if not m:
        return None
    month_str = m.group(1).lower()
    day = int(m.group(2))
    explicit_year = m.group(3)
    year = int(explicit_year) if explicit_year else datetime.now(timezone.utc).year
    month = _MONTH_MAP.get(month_str)
    if month is None:
        return None
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    # L2: If no explicit year and parsed date is >180 days in the past,
    # the market likely targets next year (e.g., "January 5" asked in December).
    if not explicit_year:
        today = datetime.now(timezone.utc).date()
        if (today - parsed).days > 180:
            try:
                parsed = date(year + 1, month, day)
            except ValueError:
                pass
    return parsed


def _parse_month_period(date_str: str) -> Optional[date]:
    """Parse month-only period: 'March', 'March 2026' → last day of that month.

    Returns last day of the month for monthly precipitation/snowfall markets.
    Falls back to _parse_date if a specific day is present.
    """
    s = date_str.strip()
    # Try "Month Year" (e.g., "March 2026")
    m = re.match(r"(\w+)\s+(\d{4})$", s)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        if month:
            year = int(m.group(2))
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, last_day)

    # Try just "Month" (e.g., "March")
    month = _MONTH_MAP.get(s.lower().split()[0] if s else "")
    if month:
        year = datetime.now(timezone.utc).year
        last_day = calendar.monthrange(year, month)[1]
        parsed = date(year, month, last_day)
        # Same L2 logic: if >180 days in the past, assume next year
        today = datetime.now(timezone.utc).date()
        if (today - parsed).days > 180:
            year += 1
            last_day = calendar.monthrange(year, month)[1]
            parsed = date(year, month, last_day)
        return parsed

    # Fall through: try specific date parsing
    return _parse_date(date_str)


# ── Market Mapper ─────────────────────────────────────────────────────────


class WeatherMarketMapper:
    """Parse and group Polymarket weather markets."""

    def __init__(self) -> None:
        # B2: Cache parsed buckets by market_id → (bucket_type, low, high, unit)
        # Question text and bounds never change; only prices and token IDs update.
        self._parse_cache: Dict[str, Tuple[str, Optional[float], Optional[float], str]] = {}
        # Same pattern for precipitation buckets
        self._precip_parse_cache: Dict[str, Tuple[str, Optional[float], Optional[float], str]] = {}
        # Same pattern for snowfall buckets
        self._snow_parse_cache: Dict[str, Tuple[str, Optional[float], Optional[float], str]] = {}
        # Same pattern for wind gust buckets
        self._wind_parse_cache: Dict[str, Tuple[str, Optional[float], Optional[float], str]] = {}
        # S101b: Unmatched cities from last group_markets() call
        self._last_unmatched_cities: set = set()

    @staticmethod
    def is_weather_market(market_data: Dict) -> bool:
        """Fast check: is this a temperature-bucket weather market?"""
        q = market_data.get("question") or market_data.get("title") or ""
        return bool(_RE_WEATHER_QUICK.search(q) or _RE_WEATHER_ALT.search(q))

    @staticmethod
    def parse_market(market_data: Dict) -> Optional[TemperatureBucket]:
        """Parse a single market dict into a TemperatureBucket, or None."""
        q = market_data.get("question") or market_data.get("title") or ""
        mid = str(market_data.get("id", ""))

        # Extract token IDs
        yes_token = market_data.get("yes_token_id") or ""
        no_token = market_data.get("no_token_id") or ""

        # Fallback: tokens array
        if not yes_token:
            tokens = market_data.get("tokens", [])
            if isinstance(tokens, list):
                for t in tokens:
                    if isinstance(t, dict):
                        outcome = (t.get("outcome") or "").upper()
                        if outcome == "YES":
                            yes_token = t.get("token_id") or t.get("tokenId") or ""
                        elif outcome == "NO":
                            no_token = t.get("token_id") or t.get("tokenId") or ""

        yes_price = float(market_data.get("yes_price") or 0.0)

        # Try patterns in order of specificity
        # 1. Range: "between 48-49°F"
        m = _RE_RANGE.search(q)
        if m:
            return TemperatureBucket(
                market_id=mid,
                token_id=yes_token,
                no_token_id=no_token,
                yes_price=yes_price,
                bucket_type="range",
                low_bound=float(m.group(2)),
                high_bound=float(m.group(3)),
                temp_unit=m.group(4).upper(),
            )

        # 2. At-or-below: "42°F or below"
        m = _RE_AT_OR_BELOW.search(q)
        if m:
            return TemperatureBucket(
                market_id=mid,
                token_id=yes_token,
                no_token_id=no_token,
                yes_price=yes_price,
                bucket_type="at_or_below",
                low_bound=None,
                high_bound=float(m.group(2)),
                temp_unit=m.group(3).upper(),
            )

        # 3. At-or-higher: "55°F or higher"
        m = _RE_AT_OR_HIGHER.search(q)
        if m:
            return TemperatureBucket(
                market_id=mid,
                token_id=yes_token,
                no_token_id=no_token,
                yes_price=yes_price,
                bucket_type="at_or_higher",
                low_bound=float(m.group(2)),
                high_bound=None,
                temp_unit=m.group(3).upper(),
            )

        # 4. Exact: "10°C on February 5"
        m = _RE_EXACT.search(q)
        if m:
            val = float(m.group(2))
            return TemperatureBucket(
                market_id=mid,
                token_id=yes_token,
                no_token_id=no_token,
                yes_price=yes_price,
                bucket_type="exact",
                low_bound=val,
                high_bound=val,
                temp_unit=m.group(3).upper(),
            )

        return None

    @staticmethod
    def _extract_city_and_date(question: str) -> Tuple[Optional[str], Optional[date]]:
        """Extract city name and target date from any weather question."""
        for pattern in (_RE_RANGE, _RE_AT_OR_BELOW, _RE_AT_OR_HIGHER, _RE_EXACT):
            m = pattern.search(question)
            if m:
                groups = m.groups()
                city = groups[0].strip()
                date_str = groups[-1].strip()
                return city, _parse_date(date_str)
        return None, None

    def group_markets(
        self,
        weather_markets: List[Dict],
    ) -> List[WeatherMarketGroup]:
        """Group parsed weather markets by (city, target_date).

        Returns a list of WeatherMarketGroup, each containing all
        temperature-bucket markets for that city+date.

        B2: Uses _parse_cache to skip regex on previously-seen market IDs.
        Bucket bounds and type are immutable per market; only prices change.
        """
        groups: Dict[str, WeatherMarketGroup] = {}
        # S101b: Track grouping drops for observability
        _city_parse_fail = 0
        _no_station = 0
        _bucket_parse_fail = 0
        _unmatched_cities: set = set()

        for mkt in weather_markets:
            mid = str(mkt.get("id", ""))
            q = mkt.get("question") or mkt.get("title") or ""
            cache_key = f"{mid}:{hash(q)}" if mid else ""

            # B2: Check parse cache for previously-parsed bucket metadata
            cached = self._parse_cache.get(cache_key) if cache_key else None
            if cached:
                bucket_type, low, high, unit = cached
                city_text, target_date = self._extract_city_and_date(q)
                if not city_text or not target_date:
                    _city_parse_fail += 1
                    logger.debug("weather_city_parse_fail", question=q[:120])
                    continue
                station = lookup_station(city_text)
                if not station:
                    _no_station += 1
                    _unmatched_cities.add(city_text)
                    continue
                # Rebuild bucket with fresh price + token IDs
                yes_token = mkt.get("yes_token_id") or ""
                no_token = mkt.get("no_token_id") or ""
                if not yes_token:
                    tokens = mkt.get("tokens", [])
                    if isinstance(tokens, list):
                        for t in tokens:
                            if isinstance(t, dict):
                                outcome = (t.get("outcome") or "").upper()
                                if outcome == "YES":
                                    yes_token = t.get("token_id") or t.get("tokenId") or ""
                                elif outcome == "NO":
                                    no_token = t.get("token_id") or t.get("tokenId") or ""
                bucket = TemperatureBucket(
                    market_id=mid,
                    token_id=yes_token,
                    no_token_id=no_token,
                    yes_price=float(mkt.get("yes_price") or 0.0),
                    bucket_type=bucket_type,
                    low_bound=low,
                    high_bound=high,
                    temp_unit=unit,
                )
            else:
                city_text, target_date = self._extract_city_and_date(q)
                if not city_text or not target_date:
                    _city_parse_fail += 1
                    logger.debug("weather_city_parse_fail", question=q[:120])
                    continue
                station = lookup_station(city_text)
                if not station:
                    _no_station += 1
                    _unmatched_cities.add(city_text)
                    logger.debug("weather_no_station_match", city=city_text)
                    continue
                bucket = self.parse_market(mkt)
                if not bucket:
                    _bucket_parse_fail += 1
                    continue
                # Cache the immutable bucket metadata
                if cache_key:
                    self._parse_cache[cache_key] = (
                        bucket.bucket_type, bucket.low_bound,
                        bucket.high_bound, bucket.temp_unit,
                    )

            key = f"{station.station_id}:{target_date.isoformat()}"
            if key not in groups:
                groups[key] = WeatherMarketGroup(
                    city=station.city_name,
                    target_date=target_date,
                    station=station,
                    buckets=[],
                    slug_prefix=mkt.get("slug", ""),
                    temp_unit=station.temp_unit,
                )
            groups[key].buckets.append(bucket)

        result = list(groups.values())
        # Sort buckets within each group by bound values
        for g in result:
            g.buckets.sort(key=lambda b: (b.low_bound or float("-inf")))

        # S101b: Store unmatched cities for WeatherBot alert consumption
        self._last_unmatched_cities = _unmatched_cities

        # S101b: Log grouping drops at INFO level for observability
        if _no_station or _city_parse_fail or _bucket_parse_fail:
            logger.info(
                "weather_grouping_drops",
                input_markets=len(weather_markets),
                output_groups=len(result),
                city_parse_fail=_city_parse_fail,
                no_station=_no_station,
                bucket_parse_fail=_bucket_parse_fail,
                unmatched_cities=sorted(_unmatched_cities) if _unmatched_cities else [],
            )

        return result

    # ── Precipitation market parsing ──────────────────────────────────────

    @staticmethod
    def is_precipitation_market(market_data: Dict) -> bool:
        """Fast check: is this a precipitation market?"""
        q = market_data.get("question") or market_data.get("title") or ""
        return bool(_RE_PRECIP_QUICK.search(q))

    @staticmethod
    def parse_precipitation_market(market_data: Dict) -> Optional[PrecipitationBucket]:
        """Parse a single precipitation market dict into a PrecipitationBucket."""
        q = market_data.get("question") or market_data.get("title") or ""
        mid = str(market_data.get("id", ""))

        yes_token = market_data.get("yes_token_id") or ""
        no_token = market_data.get("no_token_id") or ""
        if not yes_token:
            tokens = market_data.get("tokens", [])
            if isinstance(tokens, list):
                for t in tokens:
                    if isinstance(t, dict):
                        outcome = (t.get("outcome") or "").upper()
                        if outcome == "YES":
                            yes_token = t.get("token_id") or t.get("tokenId") or ""
                        elif outcome == "NO":
                            no_token = t.get("token_id") or t.get("tokenId") or ""

        yes_price = float(market_data.get("yes_price") or 0.0)

        def _normalize_unit(u: str) -> str:
            u = u.lower()
            return "mm" if "mm" in u or "millimeter" in u else "in"

        # V1 Range: "precipitation in CITY be between 3-4 inches on DATE"
        m = _RE_PRECIP_RANGE.search(q)
        if m:
            return PrecipitationBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="range",
                low_bound=float(m.group(2)), high_bound=float(m.group(3)),
                precip_unit=_normalize_unit(m.group(4)),
            )

        # V2 Range: "CITY have between 3 and 4 inches of precipitation in MONTH"
        m = _RE_PRECIP_RANGE_V2.search(q)
        if m:
            return PrecipitationBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="range",
                low_bound=float(m.group(2)), high_bound=float(m.group(3)),
                precip_unit=_normalize_unit(m.group(4)),
            )

        # V1 At-or-below: "precipitation in CITY be 2 inches or below"
        m = _RE_PRECIP_AT_OR_BELOW.search(q)
        if m:
            return PrecipitationBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_below",
                low_bound=None, high_bound=float(m.group(2)),
                precip_unit=_normalize_unit(m.group(3)),
            )

        # V2 Below: "CITY have less than 2 inches of precipitation in MONTH"
        m = _RE_PRECIP_BELOW_V2.search(q)
        if m:
            return PrecipitationBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_below",
                low_bound=None, high_bound=float(m.group(2)),
                precip_unit=_normalize_unit(m.group(3)),
            )

        # V1 At-or-higher: "precipitation in CITY be 5 inches or higher"
        m = _RE_PRECIP_AT_OR_HIGHER.search(q)
        if m:
            return PrecipitationBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_higher",
                low_bound=float(m.group(2)), high_bound=None,
                precip_unit=_normalize_unit(m.group(3)),
            )

        # V2 Higher: "CITY have more than 6 inches of precipitation in MONTH"
        m = _RE_PRECIP_HIGHER_V2.search(q)
        if m:
            return PrecipitationBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_higher",
                low_bound=float(m.group(2)), high_bound=None,
                precip_unit=_normalize_unit(m.group(3)),
            )

        return None

    @staticmethod
    def _extract_precip_city_and_date(
        question: str,
    ) -> Tuple[Optional[str], Optional[date], str]:
        """Extract city, date/period, and period type from precipitation question.

        Returns (city, target_date, period_type) where period_type is 'daily' or 'monthly'.
        For monthly markets, target_date is the last day of the month.
        """
        # V1 patterns: "precipitation in CITY be ... on DATE"
        for pattern in (_RE_PRECIP_RANGE, _RE_PRECIP_AT_OR_BELOW, _RE_PRECIP_AT_OR_HIGHER):
            m = pattern.search(question)
            if m:
                groups = m.groups()
                city = groups[0].strip()
                date_str = groups[-1].strip()
                return city, _parse_date(date_str), "daily"

        # V2 patterns: "CITY have ... inches of precipitation in MONTH"
        for pattern in (_RE_PRECIP_RANGE_V2, _RE_PRECIP_BELOW_V2, _RE_PRECIP_HIGHER_V2):
            m = pattern.search(question)
            if m:
                groups = m.groups()
                city = groups[0].strip()
                date_str = groups[-1].strip()
                # Try specific date first (e.g. "March 12")
                specific = _parse_date(date_str)
                if specific:
                    return city, specific, "daily"
                # Fall back to month-only (e.g. "March", "March 2026")
                monthly = _parse_month_period(date_str)
                if monthly:
                    return city, monthly, "monthly"

        return None, None, "daily"

    def group_precipitation_markets(
        self,
        markets: List[Dict],
    ) -> List[PrecipitationMarketGroup]:
        """Group precipitation markets by (city, target_date/period).

        Uses _precip_parse_cache to skip regex on previously-seen market IDs.
        """
        groups: Dict[str, PrecipitationMarketGroup] = {}

        for mkt in markets:
            mid = str(mkt.get("id", ""))
            q = mkt.get("question") or mkt.get("title") or ""
            if not _RE_PRECIP_QUICK.search(q):
                continue

            cache_key = f"{mid}:{hash(q)}" if mid else ""

            city_text, target_date, period_type = self._extract_precip_city_and_date(q)
            if not city_text or not target_date:
                continue

            station = lookup_station(city_text)
            if not station:
                continue

            # Check precip parse cache
            cached = self._precip_parse_cache.get(cache_key) if cache_key else None
            if cached:
                bucket_type, low, high, unit = cached
                # Rebuild bucket with fresh price + token IDs
                yes_token = mkt.get("yes_token_id") or ""
                no_token = mkt.get("no_token_id") or ""
                if not yes_token:
                    tokens = mkt.get("tokens", [])
                    if isinstance(tokens, list):
                        for t in tokens:
                            if isinstance(t, dict):
                                outcome = (t.get("outcome") or "").upper()
                                if outcome == "YES":
                                    yes_token = t.get("token_id") or t.get("tokenId") or ""
                                elif outcome == "NO":
                                    no_token = t.get("token_id") or t.get("tokenId") or ""
                bucket = PrecipitationBucket(
                    market_id=mid,
                    token_id=yes_token,
                    no_token_id=no_token,
                    yes_price=float(mkt.get("yes_price") or 0.0),
                    bucket_type=bucket_type,
                    low_bound=low,
                    high_bound=high,
                    precip_unit=unit,
                )
            else:
                bucket = self.parse_precipitation_market(mkt)
                if not bucket:
                    continue
                # Cache the immutable bucket metadata
                if cache_key:
                    self._precip_parse_cache[cache_key] = (
                        bucket.bucket_type, bucket.low_bound,
                        bucket.high_bound, bucket.precip_unit,
                    )

            key = f"precip:{station.station_id}:{target_date.isoformat()}"
            if key not in groups:
                groups[key] = PrecipitationMarketGroup(
                    city=station.city_name,
                    target_date=target_date,
                    station=station,
                    buckets=[],
                    slug_prefix=mkt.get("slug", ""),
                    precip_unit=bucket.precip_unit,
                    period=period_type,
                )
            groups[key].buckets.append(bucket)

        precip_result = list(groups.values())
        for g in precip_result:
            g.buckets.sort(key=lambda b: (b.low_bound or float("-inf")))
        return precip_result

    # ── Snowfall market parsing ───────────────────────────────────────────

    @staticmethod
    def is_snowfall_market(market_data: Dict) -> bool:
        """Fast check: is this a snowfall market?"""
        q = market_data.get("question") or market_data.get("title") or ""
        return bool(_RE_SNOW_QUICK.search(q))

    @staticmethod
    def parse_snowfall_market(market_data: Dict) -> Optional[SnowfallBucket]:
        """Parse a single snowfall market dict into a SnowfallBucket."""
        q = market_data.get("question") or market_data.get("title") or ""
        mid = str(market_data.get("id", ""))

        yes_token = market_data.get("yes_token_id") or ""
        no_token = market_data.get("no_token_id") or ""
        if not yes_token:
            tokens = market_data.get("tokens", [])
            if isinstance(tokens, list):
                for t in tokens:
                    if isinstance(t, dict):
                        outcome = (t.get("outcome") or "").upper()
                        if outcome == "YES":
                            yes_token = t.get("token_id") or t.get("tokenId") or ""
                        elif outcome == "NO":
                            no_token = t.get("token_id") or t.get("tokenId") or ""

        yes_price = float(market_data.get("yes_price") or 0.0)

        def _normalize_unit(u: str) -> str:
            u = u.lower()
            return "cm" if "cm" in u or "centimeter" in u else "in"

        m = _RE_SNOW_RANGE.search(q)
        if m:
            return SnowfallBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="range",
                low_bound=float(m.group(2)), high_bound=float(m.group(3)),
                snow_unit=_normalize_unit(m.group(4)),
            )

        m = _RE_SNOW_AT_OR_BELOW.search(q)
        if m:
            return SnowfallBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_below",
                low_bound=None, high_bound=float(m.group(2)),
                snow_unit=_normalize_unit(m.group(3)),
            )

        m = _RE_SNOW_AT_OR_HIGHER.search(q)
        if m:
            return SnowfallBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_higher",
                low_bound=float(m.group(2)), high_bound=None,
                snow_unit=_normalize_unit(m.group(3)),
            )

        return None

    @staticmethod
    def _extract_snow_city_and_date(question: str) -> Tuple[Optional[str], Optional[date]]:
        """Extract city and date from snowfall market question."""
        for pattern in (_RE_SNOW_RANGE, _RE_SNOW_AT_OR_BELOW, _RE_SNOW_AT_OR_HIGHER):
            m = pattern.search(question)
            if m:
                groups = m.groups()
                city = groups[0].strip()
                date_str = groups[-1].strip()
                return city, _parse_date(date_str)
        return None, None

    def group_snowfall_markets(
        self,
        markets: List[Dict],
    ) -> List[SnowfallMarketGroup]:
        """Group snowfall markets by (city, target_date).

        Uses _snow_parse_cache to skip regex on previously-seen market IDs.
        """
        groups: Dict[str, SnowfallMarketGroup] = {}

        for mkt in markets:
            mid = str(mkt.get("id", ""))
            q = mkt.get("question") or mkt.get("title") or ""
            if not _RE_SNOW_QUICK.search(q):
                continue

            cache_key = f"{mid}:{hash(q)}" if mid else ""

            city_text, target_date = self._extract_snow_city_and_date(q)
            if not city_text or not target_date:
                continue

            station = lookup_station(city_text)
            if not station:
                continue

            cached = self._snow_parse_cache.get(cache_key) if cache_key else None
            if cached:
                bucket_type, low, high, unit = cached
                yes_token = mkt.get("yes_token_id") or ""
                no_token = mkt.get("no_token_id") or ""
                if not yes_token:
                    tokens = mkt.get("tokens", [])
                    if isinstance(tokens, list):
                        for t in tokens:
                            if isinstance(t, dict):
                                outcome = (t.get("outcome") or "").upper()
                                if outcome == "YES":
                                    yes_token = t.get("token_id") or t.get("tokenId") or ""
                                elif outcome == "NO":
                                    no_token = t.get("token_id") or t.get("tokenId") or ""
                bucket = SnowfallBucket(
                    market_id=mid,
                    token_id=yes_token,
                    no_token_id=no_token,
                    yes_price=float(mkt.get("yes_price") or 0.0),
                    bucket_type=bucket_type,
                    low_bound=low,
                    high_bound=high,
                    snow_unit=unit,
                )
            else:
                bucket = self.parse_snowfall_market(mkt)
                if not bucket:
                    continue
                if cache_key:
                    self._snow_parse_cache[cache_key] = (
                        bucket.bucket_type, bucket.low_bound,
                        bucket.high_bound, bucket.snow_unit,
                    )

            key = f"snow:{station.station_id}:{target_date.isoformat()}"
            if key not in groups:
                groups[key] = SnowfallMarketGroup(
                    city=station.city_name,
                    target_date=target_date,
                    station=station,
                    buckets=[],
                    slug_prefix=mkt.get("slug", ""),
                    snow_unit=bucket.snow_unit,
                )
            groups[key].buckets.append(bucket)

        snow_result = list(groups.values())
        for g in snow_result:
            g.buckets.sort(key=lambda b: (b.low_bound or float("-inf")))
        return snow_result

    # ── Wind gust market parsing ──────────────────────────────────────────

    @staticmethod
    def is_wind_market(market_data: Dict) -> bool:
        """Fast check: is this a wind gust market?"""
        q = market_data.get("question") or market_data.get("title") or ""
        return bool(_RE_WIND_QUICK.search(q))

    @staticmethod
    def parse_wind_market(market_data: Dict) -> Optional[WindBucket]:
        """Parse a single wind gust market dict into a WindBucket."""
        q = market_data.get("question") or market_data.get("title") or ""
        mid = str(market_data.get("id", ""))

        yes_token = market_data.get("yes_token_id") or ""
        no_token = market_data.get("no_token_id") or ""
        if not yes_token:
            tokens = market_data.get("tokens", [])
            if isinstance(tokens, list):
                for t in tokens:
                    if isinstance(t, dict):
                        outcome = (t.get("outcome") or "").upper()
                        if outcome == "YES":
                            yes_token = t.get("token_id") or t.get("tokenId") or ""
                        elif outcome == "NO":
                            no_token = t.get("token_id") or t.get("tokenId") or ""

        yes_price = float(market_data.get("yes_price") or 0.0)

        def _normalize_unit(u: str) -> str:
            u = u.lower().replace("/", "")
            if "knot" in u:
                return "knots"
            if "km" in u:
                return "kmh"
            return "mph"

        m = _RE_WIND_RANGE.search(q)
        if m:
            return WindBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="range",
                low_bound=float(m.group(2)), high_bound=float(m.group(3)),
                wind_unit=_normalize_unit(m.group(4)),
            )

        m = _RE_WIND_AT_OR_BELOW.search(q)
        if m:
            return WindBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_below",
                low_bound=None, high_bound=float(m.group(2)),
                wind_unit=_normalize_unit(m.group(3)),
            )

        m = _RE_WIND_AT_OR_HIGHER.search(q)
        if m:
            return WindBucket(
                market_id=mid, token_id=yes_token, no_token_id=no_token,
                yes_price=yes_price, bucket_type="at_or_higher",
                low_bound=float(m.group(2)), high_bound=None,
                wind_unit=_normalize_unit(m.group(3)),
            )

        return None

    @staticmethod
    def _extract_wind_city_and_date(question: str) -> Tuple[Optional[str], Optional[date]]:
        """Extract city and date from wind gust market question."""
        for pattern in (_RE_WIND_RANGE, _RE_WIND_AT_OR_BELOW, _RE_WIND_AT_OR_HIGHER):
            m = pattern.search(question)
            if m:
                groups = m.groups()
                city = groups[0].strip()
                date_str = groups[-1].strip()
                return city, _parse_date(date_str)
        return None, None

    def group_wind_markets(
        self,
        markets: List[Dict],
    ) -> List[WindMarketGroup]:
        """Group wind gust markets by (city, target_date).

        Uses _wind_parse_cache to skip regex on previously-seen market IDs.
        """
        groups: Dict[str, WindMarketGroup] = {}

        for mkt in markets:
            mid = str(mkt.get("id", ""))
            q = mkt.get("question") or mkt.get("title") or ""
            if not _RE_WIND_QUICK.search(q):
                continue

            cache_key = f"{mid}:{hash(q)}" if mid else ""

            city_text, target_date = self._extract_wind_city_and_date(q)
            if not city_text or not target_date:
                continue

            station = lookup_station(city_text)
            if not station:
                continue

            cached = self._wind_parse_cache.get(cache_key) if cache_key else None
            if cached:
                bucket_type, low, high, unit = cached
                yes_token = mkt.get("yes_token_id") or ""
                no_token = mkt.get("no_token_id") or ""
                if not yes_token:
                    tokens = mkt.get("tokens", [])
                    if isinstance(tokens, list):
                        for t in tokens:
                            if isinstance(t, dict):
                                outcome = (t.get("outcome") or "").upper()
                                if outcome == "YES":
                                    yes_token = t.get("token_id") or t.get("tokenId") or ""
                                elif outcome == "NO":
                                    no_token = t.get("token_id") or t.get("tokenId") or ""
                bucket = WindBucket(
                    market_id=mid,
                    token_id=yes_token,
                    no_token_id=no_token,
                    yes_price=float(mkt.get("yes_price") or 0.0),
                    bucket_type=bucket_type,
                    low_bound=low,
                    high_bound=high,
                    wind_unit=unit,
                )
            else:
                bucket = self.parse_wind_market(mkt)
                if not bucket:
                    continue
                if cache_key:
                    self._wind_parse_cache[cache_key] = (
                        bucket.bucket_type, bucket.low_bound,
                        bucket.high_bound, bucket.wind_unit,
                    )

            key = f"wind:{station.station_id}:{target_date.isoformat()}"
            if key not in groups:
                groups[key] = WindMarketGroup(
                    city=station.city_name,
                    target_date=target_date,
                    station=station,
                    buckets=[],
                    slug_prefix=mkt.get("slug", ""),
                    wind_unit=bucket.wind_unit,
                )
            groups[key].buckets.append(bucket)

        wind_result = list(groups.values())
        for g in wind_result:
            g.buckets.sort(key=lambda b: (b.low_bound or float("-inf")))
        return wind_result
