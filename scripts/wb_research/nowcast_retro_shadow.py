#!/usr/bin/env python3
"""S232 — RETRO SHADOW REPLAY (read-only, VPS). Operator order 2026-07-19
("can we get what you missed back?").

Replays the EXACT deployed nowcast rule over the mesh days recorded BEFORE
shadow logging went live (2026-07-17, 07-18, and 07-19 capped at the shadow
deploy 13:50Z), and grades each would-have-been prediction against the actual
market resolution. Everything used was recorded contemporaneously — mesh obs
(pws_mesh jsonl), forecasts (weather_forecasts, forecast_time-stamped), and
resolutions (markets table) — so this is a no-lookahead reconstruction, NOT a
backtest of a new rule.

RESULTS GO TO THE RESEARCH LEDGER ONLY (this .out + spec). Never insert into
prediction_log — retro rows in the production calibration table would defeat
the temporal-ordering guard.

Per replay day D:
  1. Build the debias table AS-OF D: per-PWS trailing offsets from mesh days
     STRICTLY BEFORE D (<=5d), same block/scalar + drop rule as mesh_debias.
     (07-17's table = 07-16 data only, US-only; disclosed.)
  2. For each non-dropped city with resolved markets on D: walk the mesh
     consensus bins chronologically; on the FIRST bin where the ROUNDED
     debiased running-max enters a bucket AND local hour >= 12 AND
     E_rem <= 1.0F-equivalent (E from the latest stored forecast issued
     before the crossing; det-high = the STORED deterministic_high the live
     rule uses, median only as a NULL fallback), record ONE shadow prediction
     (prob 0.44) for that bucket.
  3. Grade vs resolution; report per day + pooled (win rate, Brier), with
     per-city counts and unit class.

Usage: nowcast_retro_shadow.py [D1 D2 ...]   (default 20260717 20260718 20260719)
"""
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
sys.path.insert(0, "/home/ubuntu/wb_research")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa
import mesh_debias as md  # noqa — reuse load_mesh_window/iem_prints/block_of/consts

CITY = {s.city_name.lower(): s for s in STATION_REGISTRY.values()}
SID = {s.station_id: s for s in STATION_REGISTRY.values()}
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
MODEL_PROB = 0.44
MIN_EDGE_NA = None          # price gate NOT applied — shadow set grades the signal
EREM_MAX_F = 1.0
MIN_HOUR = 12
MIN_PWS = 2
BIN_S = 300
# live shadow logging deployed 2026-07-19 ~13:50Z — cap the last retro day there
CAP_UTC = {"20260719": datetime(2026, 7, 19, 13, 50, tzinfo=timezone.utc).timestamp()}


def psql(q):
    return subprocess.run(["sudo", "-u", "postgres", "psql", "polymarket", "-At", "-c", q],
                          capture_output=True, text=True, timeout=180).stdout.strip()


def parse_q(q):
    m = re.match(r"Will the highest temperature in (.+?) be (.+?) on (\w+) (\d+)\?", q or "")
    if not m:
        return None
    city, spec, mon, day = m.groups()
    if mon not in MONTHS:
        return None
    s = spec.replace("°", "")
    mm = re.match(r"between (-?\d+)-(-?\d+)", s)
    if mm:
        lo, hi, typ = float(mm.group(1)), float(mm.group(2)), "range"
    else:
        mm = re.match(r"(-?\d+)\D*or (?:above|higher)", s)
        if mm:
            lo, hi, typ = float(mm.group(1)), 1e9, "at_or_higher"
        else:
            return None                 # at_or_below not a crossing target
    return city.lower(), MONTHS[mon], int(day), lo, hi, typ


def build_asof_table(D):
    """Debias table from mesh days strictly BEFORE D (trailing <=5d)."""
    d1 = D - timedelta(days=1)
    d0 = d1 - timedelta(days=4)
    mesh = md.load_mesh_window(datetime.combine(d0, datetime.min.time()),
                               datetime.combine(d1, datetime.min.time()))
    cities = {}
    import time as _t
    for sid, series in sorted(mesh.items()):
        st = SID.get(sid)
        if st is None:
            continue
        tz = ZoneInfo(st.timezone)
        prints = md.iem_prints(md.iem_station(sid), d0, d1)
        _t.sleep(0.15)
        if not prints:
            continue
        per_pws = defaultdict(list)
        for t, v in prints:
            ep = int(t.timestamp())
            latest = {}
            for oep, otf, opws in series:
                if ep - 900 <= oep <= ep + 60:
                    latest[opws] = otf
            lh = t.astimezone(tz).hour
            for opws, otf in latest.items():
                per_pws[opws].append((otf - v, lh))
        pws_table = {}
        residuals = []
        for pws, offs in per_pws.items():
            if len(offs) < md.MIN_PWS_N:
                continue
            vals = [o for o, _ in offs]
            scalar = round(median(vals), 2)
            blocks = {}
            for name, lo, hi in md.BLOCKS:
                bv = [o for o, h in offs if lo <= h <= hi]
                if len(bv) >= md.MIN_BLOCK_N:
                    blocks[name] = [round(median(bv), 2), len(bv)]
            pws_table[pws] = {"scalar": scalar, "blocks": blocks}
            for o, h in offs:
                blk = md.block_of(h)
                off = blocks[blk][0] if blk in blocks else scalar
                residuals.append(o - off)
        if not pws_table:
            continue
        from statistics import pstdev
        rsd = round(pstdev(residuals), 2) if len(residuals) > 1 else None
        cities[sid] = {"pws": pws_table, "residual_sd": rsd,
                       "dropped": bool(rsd is not None and rsd > md.DROP_SD_F)}
    return cities


def load_day_series(sid, tzinfo, local_date):
    tags = {(local_date - timedelta(days=1)).strftime("%Y%m%d"),
            local_date.strftime("%Y%m%d"),
            (local_date + timedelta(days=1)).strftime("%Y%m%d")}
    out = []
    for tag in sorted(tags):
        try:
            with open(f"/home/ubuntu/wb_research/pws_mesh_{tag}.jsonl", encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("sid") != sid or r.get("qc") != 1 or r.get("temp_f") is None:
                        continue
                    ep = int(r["epoch"])
                    if datetime.fromtimestamp(ep, tzinfo).date() == local_date:
                        out.append((ep, float(r["temp_f"]), r["pws"]))
        except OSError:
            continue
    out.sort()
    return out


def pws_offset(entry, lh):
    blk = md.block_of(lh)
    blocks = entry.get("blocks") or {}
    return float(blocks[blk][0]) if blk in blocks else float(entry["scalar"])


def main():
    tags = [a for a in sys.argv[1:] if a.isdigit()] or ["20260717", "20260718", "20260719"]
    # resolved temperature markets for those month/days
    rows = json.loads(psql("""SELECT json_agg(row_to_json(t)) FROM (
      SELECT question, resolution FROM markets
      WHERE category='weather' AND resolution IN ('YES','NO')
        AND question LIKE 'Will the highest temperature%%'
        AND (end_date_iso IS NULL OR end_date_iso >= '2026-07-01')) t""") or "[]")
    fams = defaultdict(list)
    for r in rows or []:
        p = parse_q(r["question"])
        if not p:
            continue
        city, mon, day, lo, hi, typ = p
        fams[(city, mon, day)].append(dict(lo=lo, hi=hi, typ=typ,
                                           won=r["resolution"] == "YES"))
    # forecasts
    # S232 re-review c6: pull the STORED deterministic_high — the live rule's
    # E_rem uses forecast.deterministic_high exactly, so proxying it with the
    # ensemble median made the retro's crossing set diverge from what the
    # deployed rule would have fired. Median only as a fallback for rows where
    # deterministic_high is NULL (non-NBM stations).
    fc = json.loads(psql("""SELECT json_agg(row_to_json(t)) FROM (
      SELECT station_id sid, target_date::date::text td, forecast_time ft,
             ensemble_members mem, deterministic_high dh FROM weather_forecasts
      WHERE target_date::date >= '2026-07-16' AND ensemble_members IS NOT NULL) t""") or "[]")
    fmap = defaultdict(list)
    for f in fc or []:
        if f["mem"] and len(f["mem"]) >= 10:
            ft = datetime.fromisoformat(f["ft"])
            if ft.tzinfo is None:
                ft = ft.replace(tzinfo=timezone.utc)
            _dh = f.get("dh")
            det_high = float(_dh) if _dh is not None else median(f["mem"])
            fmap[(f["sid"], f["td"])].append((ft, det_high))
    for k in fmap:
        fmap[k].sort()

    pooled = []
    for tag in tags:
        D = datetime.strptime(tag, "%Y%m%d").date()
        cap = CAP_UTC.get(tag)
        table = build_asof_table(D)
        n_clean = sum(1 for c in table.values() if not c["dropped"])
        print(f"\n=== RETRO {tag} — as-of table: {len(table)} cities, {n_clean} clean ===")
        day_preds = []
        for (city, mon, day), buckets in sorted(fams.items()):
            if (mon, day) != (D.month, D.day):
                continue
            st = CITY.get(city)
            if not st:
                continue
            ctab = table.get(st.station_id)
            if not ctab or ctab["dropped"]:
                continue
            tz = ZoneInfo(st.timezone)
            series = load_day_series(st.station_id, tz, D)
            if not series:
                continue
            flist = fmap.get((st.station_id, D.isoformat()), [])
            unit = st.temp_unit
            erem_bar = EREM_MAX_F if unit == "F" else EREM_MAX_F * 5.0 / 9.0
            taken = set()
            runmax = None
            eps = sorted({ep for ep, _, _ in series})
            for ep in eps:
                if cap and ep > cap:
                    break
                latest = {}
                lh = datetime.fromtimestamp(ep, tz).hour
                for oep, otf, opws in series:
                    if ep - BIN_S <= oep <= ep:
                        entry = ctab["pws"].get(opws)
                        if entry is not None:
                            latest[opws] = otf - pws_offset(entry, lh)
                if len(latest) < MIN_PWS:
                    continue
                c = median(latest.values())
                if runmax is None or c > runmax:
                    runmax = c
                if lh < MIN_HOUR:
                    continue
                rn = runmax if unit == "F" else (runmax - 32.0) * 5.0 / 9.0
                # as-of forecast: latest issued before this bin
                det = None
                for ft, m in flist:
                    if ft.timestamp() <= ep:
                        det = m
                if det is None or det - rn > erem_bar:
                    continue
                r = round(rn)
                for i, b in enumerate(buckets):
                    if i in taken:
                        continue
                    hit = (b["typ"] == "range" and b["lo"] <= r <= b["hi"]) or \
                          (b["typ"] == "at_or_higher" and r >= b["lo"])
                    if hit:
                        taken.add(i)
                        day_preds.append(dict(city=city, sid=st.station_id,
                                              unit=unit, won=b["won"],
                                              lh=lh))
            del taken
        if day_preds:
            wins = sum(1 for p in day_preds if p["won"])
            brier = mean((MODEL_PROB - (1.0 if p["won"] else 0.0)) ** 2 for p in day_preds)
            bycity = defaultdict(int)
            for p in day_preds:
                bycity[p["sid"]] += 1
            print(f"  predictions {len(day_preds)} | wins {wins} "
                  f"({wins/len(day_preds):.0%} vs model 0.44) | Brier {brier:.3f}")
            print(f"  per-city: {dict(sorted(bycity.items()))}")
        else:
            print("  predictions 0")
        pooled += day_preds
    if pooled:
        wins = sum(1 for p in pooled if p["won"])
        brier = mean((MODEL_PROB - (1.0 if p["won"] else 0.0)) ** 2 for p in pooled)
        print(f"\nPOOLED RETRO: n={len(pooled)} wins={wins} "
              f"({wins/len(pooled):.0%} vs model 0.44) Brier={brier:.3f}")
        print("CAVEATS: retro-flagged set (research ledger only); det-high = stored")
        print("deterministic_high (median fallback); 07-17 table = 07-16 US-only; no price")
        print("gate (grades the SIGNAL, matching the live shadow set's semantics).")


if __name__ == "__main__":
    main()
