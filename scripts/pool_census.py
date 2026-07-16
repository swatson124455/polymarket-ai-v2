#!/usr/bin/env python3
"""Hourly Polymarket reward-pool census. READ-ONLY, stdlib-only, paper-lane.

Answers H3 of docs/MAKER_V4_LANE_TEST_PLAN.md with real data:
  * how do total/sector reward pools move intraday (live-game concentration)?
  * what steps down after the World Cup incentive ends Jul 19 (changelog-verified)?
  * are target-market spreads compressing toward 1 cent (kill-trigger trend)?

Method (canon, 2026-07-15): gamma caps offset paging at ~2,100 per ordering
(HTTP 422). One ordering is therefore a truncated view; we UNION three
orderings (volume24hr, liquidity, startDate — all desc) and record the walls.
Totals are LOWER BOUNDS by construction and labeled as such.

Output: <base>/census-YYYYMMDD.jsonl
  one "run" summary line + one compact "mkt" line per rewarded market per run.

Guard rails: GET-only, no keys, no DATABASE_URL; STOP sentinel; <=80 HTTP
requests/run; disk cap 300MB (script exits; timer unit keeps schedule).

Usage:  pool_census.py --once [--base /opt/pa2-maker-census]
"""
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "pa2-maker-pool-census/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
ORDERINGS = ("volume24hr", "liquidity", "startDate")
MAX_PAGES_PER_ORDERING = 25
MAX_REQUESTS = 80
MAX_DISK_MB = 300

KW = [
    (r"nba|nfl|mlb|nhl|ncaa|premier|epl|serie-a|la-liga|bundesliga|ligue|ufc|atp|wta|pga|f1-|grand-prix|world-cup|fifa|uefa|copa|boxing|tennis|-vs-|derby|open-", "sports"),
    (r"lol-|league-of-legends|cs2|csgo|counter-strike|dota|valorant|esports|lck|lpl|lec|ewc", "esports"),
    (r"bitcoin|btc|ethereum|eth-|solana|xrp|doge|crypto", "crypto"),
    (r"trump|election|president|senate|congress|mayor|governor|primary|nominee|supreme-court|minister|parliament", "politics"),
    (r"temperature|highest-temp|rainfall|hurricane|snow|heat-|weather", "weather"),
    (r"fed-|interest-rate|cpi|inflation|gdp|recession|s-p-500|spx|nasdaq|spy|wti|crude|tariff|treasury", "finance"),
    (r"israel|gaza|ukraine|russia|iran|nato|ceasefire|hormuz|houthi|war-", "geopolitical"),
    (r"oscar|grammy|emmy|box-office|album|movie|netflix|spotify", "entertainment"),
]


def sector_of(m):
    c = (m.get("category") or "").strip().lower()
    if c:
        return c
    text = ((m.get("slug") or "") + " " + (m.get("question") or "")).lower()
    for pat, lab in KW:
        if re.search(pat, text):
            return lab
    return "unknown"


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def census(base):
    if os.path.exists(os.path.join(base, "STOP")):
        print("STOP sentinel — skipping run")
        return 0
    size_mb = sum(os.path.getsize(os.path.join(r, f))
                  for r, _, fs in os.walk(base) for f in fs) / 1e6
    if size_mb > MAX_DISK_MB:
        print(f"disk cap exceeded ({size_mb:.0f}MB) — skipping run")
        return 1

    t0 = time.time()
    seen = {}
    walls = {}
    nreq = 0
    for order in ORDERINGS:
        for page in range(MAX_PAGES_PER_ORDERING):
            if nreq >= MAX_REQUESTS:
                walls[order] = f"pg{page}:req_budget"
                break
            q = urllib.parse.urlencode({"active": "true", "closed": "false",
                                        "limit": 100, "offset": page * 100,
                                        "order": order, "ascending": "false"})
            nreq += 1
            try:
                req = urllib.request.Request(f"{GAMMA}?{q}", headers=UA)
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.load(r)
            except Exception as e:
                walls[order] = f"pg{page}:{str(e)[:40]}"
                break
            if not isinstance(data, list) or not data:
                walls[order] = f"pg{page}:empty"
                break
            for m in data:
                mid = m.get("id")
                if mid in seen:
                    continue
                seen[mid] = m
        else:
            walls[order] = "pg_cap"

    now = time.time()
    rows, per_sector = [], {}
    total = 0.0
    for mid, m in seen.items():
        pool = 0.0
        for r in (m.get("clobRewards") or []):
            p = fnum(r.get("rewardsDailyRate"))
            if p:
                pool += p
        if pool <= 0:
            continue
        sec = sector_of(m)
        total += pool
        per_sector[sec] = round(per_sector.get(sec, 0.0) + pool, 2)
        ev = m.get("events") or []
        ev_title = (ev[0].get("title") if ev and isinstance(ev[0], dict) else "") or ""
        bb, ba = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
        rows.append({"k": "mkt", "t": round(now), "id": mid,
                     "pool": round(pool, 2), "sec": sec,
                     "ev": ev_title[:40], "q": (m.get("question") or "")[:50],
                     "bb": bb, "ba": ba,
                     "spr": round(ba - bb, 4) if bb is not None and ba is not None else None,
                     "gs": (m.get("gameStartTime") or "")[:16],
                     "end": (m.get("endDate") or "")[:10]})
    summary = {"k": "run", "t": round(now),
               "utc": time.strftime("%Y-%m-%dT%H:%M", time.gmtime(now)),
               "seen": len(seen), "rewarded": len(rows),
               "total_pool": round(total, 2), "per_sector": per_sector,
               "walls": walls, "requests": nreq,
               "elapsed_s": round(now - t0, 1),
               "note": "lower_bound_union_3_orderings"}
    day = time.strftime("%Y%m%d", time.gmtime(now))
    with open(os.path.join(base, f"census-{day}.jsonl"), "a") as f:
        f.write(json.dumps(summary) + "\n")
        for r in sorted(rows, key=lambda x: -x["pool"]):
            f.write(json.dumps(r) + "\n")
    print(f"census ok: {len(seen)} seen, {len(rows)} rewarded, "
          f"total=${total:,.0f}/day, {nreq} reqs, {round(now - t0, 1)}s")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/opt/pa2-maker-census")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.base, exist_ok=True)
    if a.once:
        raise SystemExit(census(a.base))
    ap.error("pass --once")
