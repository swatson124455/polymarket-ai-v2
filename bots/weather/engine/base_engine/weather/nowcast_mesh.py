"""S232 Phase-2 — PWS-mesh nowcast signal helpers (pure functions).

Operator GO 2026-07-18 after the mesh-lead gate PASSED day-1 (spec §S232
MESH-LEAD VERDICT: 60% of 93 events mesh-led, pooled median lead 74.8 min,
false-crossing 10.8%). Flag stays OFF (WEATHER_NOWCAST_ENTRY_ENABLED) until
the operator's flip review.

Data plane (research layer, ubuntu cron — bot only READS):
  /opt/pa2-weather-feeds/pws_mesh_YYYYMMDD.jsonl  5-min PWS obs mirror
  /opt/pa2-weather-feeds/mesh_debias.json         trailing per-PWS offsets
                                                  (scalar + local hour-block),
                                                  per-city drop rule
All temps in the feed are °F (WU units=e); callers convert to station-native
units via f_to_native() before comparing to bucket bounds / forecasts.

Everything here is side-effect-free and synchronous file reads only — the
weather_bot caller wraps every use behind the env flag and try/except, per
the S231 maker-feed isolation doctrine.
"""

import glob
import json
import os
from datetime import datetime, timedelta
from statistics import median
from typing import Dict, List, Optional, Tuple

BIN_S = 300                      # consensus bin, matches pws_mesh cadence
BLOCKS = (("morning", 6, 11), ("afternoon", 12, 17), ("evening", 18, 23))


def f_to_native(temp_f: float, unit: str) -> float:
    """°F feed value → station-native units ('F' passthrough, 'C' converted)."""
    if unit == "C":
        return (temp_f - 32.0) * 5.0 / 9.0
    return temp_f


def f_delta_to_native(delta_f: float, unit: str) -> float:
    """A °F DIFFERENCE (e.g. the 1.0F E_rem bar) in native units."""
    if unit == "C":
        return delta_f * 5.0 / 9.0
    return delta_f


def load_debias_table(feed_dir: str, max_age_s: int = 172800) -> Optional[Dict]:
    """Load mesh_debias.json; None if missing, unparseable, or stale."""
    path = os.path.join(feed_dir, "mesh_debias.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        gen = datetime.strptime(doc["generated_utc"], "%Y-%m-%dT%H:%M:%SZ")
        if (datetime.utcnow() - gen).total_seconds() > max_age_s:
            return None
        return doc
    except Exception:
        return None


def block_of(local_hour: int) -> Optional[str]:
    for name, lo, hi in BLOCKS:
        if lo <= local_hour <= hi:
            return name
    return None


def pws_offset(entry: Dict, local_hour: int) -> float:
    """Offset to SUBTRACT from a PWS ob: hour-block term when published,
    scalar otherwise (same convention mesh_debias uses for its residuals)."""
    blk = block_of(local_hour)
    blocks = entry.get("blocks") or {}
    if blk in blocks:
        return float(blocks[blk][0])
    return float(entry.get("scalar", 0.0))


def load_mesh_day(feed_dir: str, sid: str, tzinfo, local_date) -> List[Tuple[int, float, str]]:
    """qc==1 obs for `sid` whose LOCAL date == local_date, sorted by epoch.

    Mirrors mesh_validation's two-file rule: a western-hemisphere local
    evening lands in the NEXT UTC day's file, so read date and date+1 tags.
    """
    tags = {local_date.strftime("%Y%m%d"),
            (local_date + timedelta(days=1)).strftime("%Y%m%d")}
    out: List[Tuple[int, float, str]] = []
    for tag in sorted(tags):
        for path in glob.glob(os.path.join(feed_dir, f"pws_mesh_{tag}.jsonl")):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if r.get("sid") != sid or r.get("qc") != 1:
                            continue
                        tf = r.get("temp_f")
                        if tf is None:
                            continue
                        ep = int(r["epoch"])
                        if datetime.fromtimestamp(ep, tzinfo).date() == local_date:
                            out.append((ep, float(tf), r["pws"]))
            except OSError:
                continue
    out.sort()
    return out


def debiased_runmax(
    series: List[Tuple[int, float, str]],
    city_table: Dict,
    tzinfo,
    min_pws: int = 2,
) -> Optional[Dict]:
    """Debiased consensus running max over the day's mesh series (°F).

    Consensus per bin-end epoch = median over the LATEST debiased ob per PWS
    in the trailing BIN_S window; bins with < min_pws PWS are skipped. Only
    PWS present in the debias table contribute (unknown stations have no
    offset and would re-introduce raw bias).

    Returns {"runmax_f", "runmax_epoch", "last_epoch", "last_n_pws"} or None
    if no bin ever reaches min_pws.
    """
    pws_table = (city_table or {}).get("pws") or {}
    if not pws_table:
        return None
    eps = sorted({ep for ep, _, _ in series})
    runmax = None
    runmax_ep = None
    last_ep = None
    last_n = 0
    for ep in eps:
        latest: Dict[str, float] = {}
        lh = datetime.fromtimestamp(ep, tzinfo).hour
        for oep, otf, opws in series:
            if ep - BIN_S <= oep <= ep:
                entry = pws_table.get(opws)
                if entry is not None:
                    latest[opws] = otf - pws_offset(entry, lh)
        if len(latest) < min_pws:
            continue
        c = median(latest.values())
        last_ep, last_n = ep, len(latest)
        if runmax is None or c > runmax:
            runmax, runmax_ep = c, ep
    if runmax is None:
        return None
    return {"runmax_f": runmax, "runmax_epoch": runmax_ep,
            "last_epoch": last_ep, "last_n_pws": last_n}


def bucket_crossed(runmax_native: float, bucket_type: str,
                   low: Optional[float], high: Optional[float]) -> bool:
    """Has the ROUNDED debiased running max entered this bucket?

    Rounded-degree convention matches the resolution world (winner bucket
    contains the rounded print max — spec §S232 REP_BIAS RECOMPUTE) and the
    backtest's crossing definition (nowcast_skill/mesh_validation).
    """
    r = round(runmax_native)
    if bucket_type == "range":
        return low is not None and high is not None and low <= r <= high
    if bucket_type == "at_or_higher":
        return low is not None and r >= low
    return False


def bucket_overshot(runmax_native: float, bucket_type: str,
                    high: Optional[float]) -> bool:
    """Invalidation: the rounded debiased running max exceeds the bucket's
    high bound — a RANGE bucket can no longer win on the mesh's read."""
    if bucket_type != "range" or high is None:
        return False
    return round(runmax_native) > high
