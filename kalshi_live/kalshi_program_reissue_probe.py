#!/usr/bin/env python3
"""PROGRAM RE-ISSUE PROBE — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

THE CLAIM UNDER TEST: the horizon RATIO

    ratio = (market close_time - now) / (program end_date - now)      (kalshi_horizon_census.py:8)

is read as "reward STOPS days-to-months before the contract closes". That reading assumes the
CURRENTLY-ACTIVE program's end_date is the TERMINAL end of reward on that market.

Canon R1 says period_reward is the total for a TIME PERIOD, not a rate — i.e. programs are
PERIODIC objects. If Kalshi re-issues a program for the next Time Period on the same market,
then `end_date` is the end of the CURRENT PERIOD, not the end of reward, and a high ratio does
not mean "uncompensated tail" — it means "short period on a long market".

The public API exposes no program history (`status=` is ignored: active/settled/finished/
inactive/all all return identical rows, measured), so re-issue cannot be observed directly.
The OBSERVABLE PROXY is mid-life issuance:

    if program.start_date  >>  market.open_time
    then this program was issued LONG AFTER the market was listed, which is only possible if
    programs are issued per-period rather than once at listing.

Any material mid-life issuance is sufficient to show the ratio's denominator is the wrong
object. It does NOT prove a given program WILL be re-issued — that needs a longitudinal
re-measurement (stated below).

DOES NOT COVER: fill rate, queue position, adverse selection, settlement toxicity. One instant.

Run: python kalshi_program_reissue_probe.py   Out: program_reissue_probe.json
"""
import json
import os
import statistics
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(HERE, "program_reissue_probe.json")
SPACING_S = 0.32
PAGES = 8
_last = [0.0]

# survivors + incumbents + the series the proposal REJECTED on a high ratio
SERIES = ["KXAAAGASD", "KXAAAGASW", "KXNETFLIXTOPVIEWSMOVIE", "KXMUSKNW", "KXACTBLUETOP",
          "KXNHSALES", "KXFEDMENTION", "KXAMSAVO", "KXB200MON", "KXEOWEEK",
          "KXRT", "KXH200MS", "KXFUNDRAISING", "KXDPZ", "KXVOTEPRIMARY"]


def get(path):
    w = SPACING_S - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-reissue/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main():
    now = datetime.now(timezone.utc)
    progs, cur, seen = [], "", set()
    for _ in range(PAGES):
        d = get("/incentive_programs?status=active&limit=10000"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    by_s = defaultdict(list)
    for p in progs:
        by_s[(p.get("market_ticker") or "").split("-")[0]].append(p)
    print(f"active programs venue-wide: {len(progs)}\n{'='*104}")
    print(f"{'series':26s} {'n':>3} {'progH':>7} {'openAge_h':>10} {'issuedAfterOpen_h':>18} "
          f"{'closeH':>8} {'ratio':>7}")
    rows = []
    for s in SERIES:
        ps = by_s.get(s) or []
        if not ps:
            print(f"  {s:24s}  no active program")
            continue
        ds = []
        for p in ps[:3]:                       # up to 3 markets per series
            t = p["market_ticker"]
            try:
                m = get(f"/markets/{t}").get("market") or {}
                op = m.get("open_time")
                ct = m.get("close_time")
                if not op or not ct:
                    continue
                op, ct = parse_iso(op), parse_iso(ct)
                a, b = parse_iso(p["start_date"]), parse_iso(p["end_date"])
                ds.append({
                    "ticker": t,
                    "program_h": (b - a).total_seconds() / 3600.0,
                    "market_open_age_h": (now - op).total_seconds() / 3600.0,
                    "issued_after_open_h": (a - op).total_seconds() / 3600.0,
                    "close_in_h": (ct - now).total_seconds() / 3600.0,
                    "ratio": (((ct - now).total_seconds() / (b - now).total_seconds())
                              if (b - now).total_seconds() > 0 else None),
                    "market_life_h": (ct - op).total_seconds() / 3600.0,
                })
            except Exception:
                continue
        if not ds:
            continue
        med = lambda k: statistics.median([x[k] for x in ds if x[k] is not None]) \
            if [x[k] for x in ds if x[k] is not None] else None
        r = {"series": s, "n": len(ds), "program_h": med("program_h"),
             "market_open_age_h": med("market_open_age_h"),
             "issued_after_open_h": med("issued_after_open_h"),
             "close_in_h": med("close_in_h"), "ratio": med("ratio"),
             "market_life_h": med("market_life_h"), "samples": ds}
        rows.append(r)
        print(f"  {s:24s} {len(ds):3d} {r['program_h']:7.1f} {r['market_open_age_h']:10.1f} "
              f"{r['issued_after_open_h']:18.1f} {r['close_in_h']:8.1f} "
              f"{(f'{r[chr(114)+chr(97)+chr(116)+chr(105)+chr(111)]:7.2f}' if r['ratio'] is not None else '    n/a')}")

    mid = [r for r in rows if r["issued_after_open_h"] is not None
           and r["issued_after_open_h"] > 1.0]
    print(f"\n  programs issued MID-LIFE (>1h after the market opened): {len(mid)}/{len(rows)} series")
    for r in mid:
        frac = 100.0 * r["program_h"] / r["market_life_h"] if r["market_life_h"] else None
        print(f"    {r['series']:24s} issued {r['issued_after_open_h']:8.1f}h after open; "
              f"program window covers {frac:5.1f}% of total market life")
    json.dump({"generated": now.isoformat(), "rows": rows}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
