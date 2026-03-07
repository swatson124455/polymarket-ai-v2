"""
Market Mapper — parses Polymarket weather market questions into structured bucket data.

Handles four question formats observed in 8,600+ historical weather markets:
  1. "Will the highest temperature in NYC be between 48-49°F on January 22?"
  2. "Will the highest temperature in NYC be 42°F or below on January 22?"
  3. "Will the highest temperature in NYC be 55°F or higher on January 22?"
  4. "Will the highest temperature in London be 10°C on February 5?"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

from base_engine.weather.station_registry import WeatherStation, lookup_station

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


# ── Market Mapper ─────────────────────────────────────────────────────────


class WeatherMarketMapper:
    """Parse and group Polymarket weather markets."""

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
        """
        groups: Dict[str, WeatherMarketGroup] = {}

        for mkt in weather_markets:
            q = mkt.get("question") or mkt.get("title") or ""
            city_text, target_date = self._extract_city_and_date(q)
            if not city_text or not target_date:
                continue

            station = lookup_station(city_text)
            if not station:
                logger.debug("weather_no_station_match", city=city_text)
                continue

            bucket = self.parse_market(mkt)
            if not bucket:
                continue

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
        return result
