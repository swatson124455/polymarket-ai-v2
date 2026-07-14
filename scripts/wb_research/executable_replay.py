#!/usr/bin/env python3
"""S230 executable replay (read-only): leader-following at the LOGGED ASK.

The race study (race_study.py) grades "buy the leader bucket at local hour H"
at MID prices. This replays the same strategy against the shadow-book logger's
captured books (shadow_books_*.jsonl): fill at the logged best ask, grade
against markets.resolution (CLOB-verified). Same EV units as race_study
(profit per share: outcome - price) so the two readouts are directly
comparable; the difference is the executable-vs-mid capture.

Usage (on the VPS, where psql has the resolutions):
    python3 executable_replay.py [shadow_books_*.jsonl ...]
        default input glob: /home/ubuntu/wb_research/shadow_books_*.jsonl
    --res FILE.json   offline mode: {"<market_id>": "YES"|"NO", ...}
                      (skips psql; for local testing)

Per (family-day, hour H) exactly ONE entry is simulated: the earliest tick in
that local hour. A family-day is (city, month, day) parsed from the question
tail; every book in a tick belongs to that tick's local day by construction
(shadow_book.py only logs same-local-day families).
"""
import glob
import json
import re
import subprocess
import sys
from collections import defaultdict
from statistics import median

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}

HOURS = list(range(10, 20))
PRICED_IN = 0.98          # race_study comparability filter: 0.02 < p < 0.98


def parse_tail(q):
    """Parse bucket range + target (month, day) from the 42-char question tail."""
    md = re.search(r"on (\w+) (\d+)\?$", q)
    if not md or md.group(1) not in MONTHS:
        return None
    mon, day = MONTHS[md.group(1)], int(md.group(2))
    m = re.search(r"between (-?\d+)-(-?\d+)", q)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
    else:
        m = re.search(r"(-?\d+)\D{0,4}or (?:above|higher)", q)
        if m:
            lo, hi = float(m.group(1)), 1e9
        else:
            m = re.search(r"(-?\d+)\D{0,4}or (?:below|lower)", q)
            if m:
                lo, hi = -1e9, float(m.group(1))
            else:
                return None
    return mon, day, lo, hi


def load_resolutions(market_ids, res_file):
    if res_file:
        return json.load(open(res_file, encoding="utf-8"))
    res = {}
    ids = sorted(market_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        in_list = ",".join("'%s'" % x for x in chunk)
        out = subprocess.run(
            ["sudo", "-u", "postgres", "psql", "polymarket", "-At", "-c",
             "SELECT id, resolution FROM markets WHERE id IN (%s) "
             "AND resolution IS NOT NULL" % in_list],
            capture_output=True, text=True, timeout=120).stdout
        for line in out.strip().splitlines():
            mid, r = line.split("|", 1)
            res[mid] = r
    return res


def main():
    argv = sys.argv[1:]
    res_file = None
    if "--res" in argv:
        i = argv.index("--res")
        res_file = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    paths = args or sorted(glob.glob("/home/ubuntu/wb_research/shadow_books_*.jsonl"))
    if not paths:
        sys.exit("no shadow_books files found")

    # (city, mon, day, H) -> first-tick leader entry
    entries = {}
    market_ids = set()
    ticks = 0
    for path in paths:
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ticks += 1
            rm = r.get("runmax_f")
            if rm is None:
                continue
            h = r["local_hour"]
            if h not in HOURS:
                continue
            for mid, b in (r.get("books") or {}).items():
                if "err" in b:
                    continue
                p = parse_tail(b.get("q", ""))
                if not p:
                    continue
                mon, day, lo, hi = p
                # race_study leader convention: lo-0.5 <= runmax < hi+0.5
                if not (lo - 0.5 <= rm < hi + 0.5):
                    continue
                key = (r["city"], mon, day, h)
                if key in entries and entries[key]["ts"] <= r["ts"]:
                    continue  # keep earliest tick in the hour
                asks = b.get("asks") or []
                entries[key] = {
                    "ts": r["ts"], "market_id": mid, "runmax": rm,
                    "ask": asks[0][0] if asks else None,
                    "ask_sz": asks[0][1] if asks else 0.0,
                    "depth3": sum(s for _, s in asks[:3]),
                }
                market_ids.add(mid)

    res = load_resolutions(market_ids, res_file)

    stats = defaultdict(lambda: {"ev": [], "asks": [], "szs": [], "d3s": [],
                                 "unbuyable": 0, "priced_in": 0, "pending": 0})
    for (city, mon, day, h), e in entries.items():
        s = stats[h]
        r = res.get(e["market_id"])
        if r is None:
            s["pending"] += 1
            continue
        if e["ask"] is None:
            s["unbuyable"] += 1
            continue
        if not (1 - PRICED_IN < e["ask"] < PRICED_IN):
            s["priced_in"] += 1
            continue
        won = (r == "YES")
        s["ev"].append((1.0 if won else 0.0) - e["ask"])
        s["asks"].append(e["ask"])
        s["szs"].append(e["ask_sz"])
        s["d3s"].append(e["depth3"])

    n_files = len(paths)
    n_fam = len({k[:3] for k in entries})
    print(f"files={n_files} ticks={ticks} family-days={n_fam} "
          f"markets_seen={len(market_ids)} resolved={len(res)}")
    print("\nBUY-THE-LEADER at the LOGGED BEST ASK at local hour H, hold to "
          "resolution\n(profit per share = outcome - ask; same units as "
          "race_study mid numbers):")
    print("  H  |  n  | meanEV  | win% | med ask | med ask_sz | med 3lvl "
          "| unbuyable | ask>=0.98 | pending")
    for h in HOURS:
        s = stats.get(h)
        if not s:
            continue
        n = len(s["ev"])
        row = (f"  {h:02d} | {n:3d} | "
               + (f"{sum(s['ev'])/n:+.3f}" if n else "  -   ") + "  | "
               + (f"{sum(1 for x in s['ev'] if x > 0)/n:4.0%}" if n else "  - ")
               + " | "
               + (f"{median(s['asks']):7.3f}" if n else "   -   ") + " | "
               + (f"{median(s['szs']):10.0f}" if n else "     -    ") + " | "
               + (f"{median(s['d3s']):8.0f}" if n else "    -   ")
               + f" | {s['unbuyable']:9d} | {s['priced_in']:9d} "
               f"| {s['pending']:7d}")
        print(row)
    print("\nNOTE: n counts one simulated entry per (family-day, hour). "
          "'unbuyable' = leader had no ask;\n'ask>=0.98' = priced-in filter "
          "(race_study comparability); 'pending' = market not yet resolved.")


if __name__ == "__main__":
    main()
