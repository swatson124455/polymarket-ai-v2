"""
DST Mismatch Calendar (A7).

Detects Daylight Saving Time transition weeks where measurement window
ambiguity affects market resolution.  Uses ``zoneinfo`` (stdlib 3.9+)
to compute transitions dynamically — no hardcoded dates.

Key mismatch periods (2026 example):
  - March 8-29: US switches to EDT (UTC-4), Dublin stays UTC+0
  - Oct 25 - Nov 1: EU switches back before US

NWS Daily Climate Reports use Local Standard Time even during DST,
shifting the measurement window by 1 hour.
"""

from datetime import date, datetime, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


def _find_dst_transitions(tz_name: str, year: int) -> list[date]:
    """Return dates where UTC offset changes for *tz_name* in *year*.

    Walks every day Jan 1 → Dec 31 and detects offset changes.
    Returns a list of transition dates (typically 2: spring-forward, fall-back).
    """
    tz = ZoneInfo(tz_name)
    transitions: list[date] = []
    prev_offset = datetime(year, 1, 1, 12, tzinfo=tz).utcoffset()
    for day_offset in range(1, 366):
        d = date(year, 1, 1) + timedelta(days=day_offset)
        if d.year != year:
            break
        cur_offset = datetime(d.year, d.month, d.day, 12, tzinfo=tz).utcoffset()
        if cur_offset != prev_offset:
            transitions.append(d)
        prev_offset = cur_offset
    return transitions


def is_dst_transition_week(
    tz_name: str,
    check_date: Optional[date] = None,
    *,
    window_days: int = 7,
) -> bool:
    """Return True if *check_date* falls within *window_days* of a DST
    transition for the given timezone.

    Parameters
    ----------
    tz_name : str
        IANA timezone name (e.g. ``"US/Eastern"``, ``"Europe/Dublin"``).
    check_date : date, optional
        Date to check. Defaults to today (UTC).
    window_days : int
        Days before and after the transition to flag.
    """
    if check_date is None:
        check_date = datetime.utcnow().date()

    transitions = _find_dst_transitions(tz_name, check_date.year)
    for t in transitions:
        if abs((check_date - t).days) <= window_days:
            return True
    return False


def dst_mismatch_active(
    station_tz: str,
    vps_tz: str = "Europe/Dublin",
    check_date: Optional[date] = None,
    *,
    window_days: int = 7,
) -> bool:
    """Return True when *station_tz* and *vps_tz* have different DST
    transition dates and *check_date* falls in the mismatch window.

    This is the main entry point for WeatherBot: during mismatch weeks,
    the NWS measurement window (Local Standard Time) differs from what
    the VPS clock implies.
    """
    if check_date is None:
        check_date = datetime.utcnow().date()

    station_transitions = _find_dst_transitions(station_tz, check_date.year)
    vps_transitions = _find_dst_transitions(vps_tz, check_date.year)

    # Mismatch: one has transitioned but the other hasn't yet
    for st in station_transitions:
        near_station = abs((check_date - st).days) <= window_days
        # Check if VPS transitions on a different date
        near_vps = any(abs((check_date - vt).days) <= window_days for vt in vps_transitions)
        if near_station and not near_vps:
            return True
        if near_station and near_vps:
            # Both transitioning in same week — still check if dates differ
            for vt in vps_transitions:
                if abs((st - vt).days) > 0 and abs((check_date - st).days) <= window_days:
                    return True

    for vt in vps_transitions:
        near_vps = abs((check_date - vt).days) <= window_days
        near_station = any(abs((check_date - st).days) <= window_days for st in station_transitions)
        if near_vps and not near_station:
            return True

    return False


def get_nws_measurement_offset_hours(station_tz: str, target_date: date) -> float:
    """Return the UTC offset (in hours) that NWS uses for the measurement
    window on *target_date*.

    NWS Daily Climate Reports use **Local Standard Time** year-round,
    even during DST.  This returns the standard (non-DST) UTC offset.
    """
    tz = ZoneInfo(station_tz)
    # January 15 is always standard time in both hemispheres
    jan = datetime(target_date.year, 1, 15, 12, tzinfo=tz)
    offset = jan.utcoffset()
    if offset is None:
        return 0.0
    return offset.total_seconds() / 3600.0
