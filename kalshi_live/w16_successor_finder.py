#!/usr/bin/env python3
"""W16 — ALLOWLIST HEALTH + SUCCESSOR/CLONE FINDER (operator-named 2026-08-06).

The receipts allowlist matches by SERIES TICKER PREFIX, but the venue rotates
franchise names across periods/question-forms (found live 08-06: proven payer
KXSENATEADJOURN "When will the Senate leave for August recess?" went programless
while KXADJOURNRECESS "Will the Senate hold a roll call vote on its August
recess?" runs a $285/day program — same franchise, new ticker). Every rotation
silently shrinks the earning surface: the successor enters as a 5ct probe
instead of ramping as a payer.

READ-ONLY. Two outputs:
  1. HEALTH: which allowlist series currently carry active liquidity programs
     (count + pool $/day), and which are programless right now.
  2. SUCCESSORS: for each PROGRAMLESS allowlist series, the active-program
     series ranked by title-token overlap (+ same-category boost) — candidate
     clones under new names. Candidates are DECISIONS for the operator; nothing
     here writes config.

Run (VPS): sudo ./venv/bin/python w16_successor_finder.py [--min-score 0.25]
"""
import argparse
import collections
import json
import re
import time
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
STOP = {"the", "a", "an", "of", "on", "in", "for", "will", "be", "by", "at",
        "to", "this", "week", "today", "daily", "what", "when", "how", "its"}


def _get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=30))


def _tokens(title):
    return {w for w in re.findall(r"[a-z]+", (title or "").lower())
            if w not in STOP and len(w) > 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=0.25)
    ap.add_argument("--allow", default=None, help="comma list; default = live.env")
    a = ap.parse_args()
    if a.allow:
        allow = [s for s in a.allow.split(",") if s.strip()]
    else:
        import os
        env = {}
        with open("/opt/pa2-maker-kalshi-live/live.env") as fh:
            for line in fh:
                if "=" in line:
                    k, _, v = line.strip().partition("=")
                    env[k] = v
        allow = [s for s in env.get("KALSHI_SERIES_ALLOW", "").split(",") if s.strip()]
    progs, cursor = [], ""
    for _ in range(5):
        d = _get("/incentive_programs?status=active&limit=10000"
                 + (f"&cursor={cursor}" if cursor else ""))
        progs += d.get("incentive_programs", [])
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
    by_series = collections.defaultdict(lambda: {"n": 0, "pool": 0.0})
    for p in progs:
        t = p.get("market_ticker") or ""
        if not t or (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        s = t.split("-")[0]
        by_series[s]["n"] += 1
        by_series[s]["pool"] += (p.get("period_reward") or 0) / 10000

    print(f"# W16 allowlist health — {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"active program series: {len(by_series)}; allowlist: {len(allow)}")
    live_allow = [s for s in allow if s in by_series]
    dead_allow = [s for s in allow if s not in by_series]
    print("\n## allowlist WITH live programs")
    for s in sorted(live_allow):
        r = by_series[s]
        print(f"  {s:26s} markets={r['n']:3d} pool=${r['pool']:.0f}/day")
    print("\n## allowlist with NO live program right now")
    for s in sorted(dead_allow):
        print(f"  {s}")

    # series metadata (title/category) — one read per distinct series, cached in-run
    meta = {}

    def series_meta(s):
        if s not in meta:
            try:
                meta[s] = _get(f"/series/{s}").get("series", {}) or {}
            except Exception:
                meta[s] = {}
            time.sleep(0.25)
        return meta[s]

    print("\n## successor candidates (programless payer -> active series, ranked)")
    active_nonallow = [s for s in by_series if s not in set(allow)]
    for dead in sorted(dead_allow):
        dm = series_meta(dead)
        dt_, dc = _tokens(dm.get("title")), (dm.get("category") or "")
        if not dt_:
            print(f"  {dead}: (no title metadata)")
            continue
        scored = []
        for cand in active_nonallow:
            cm = series_meta(cand)
            ct = _tokens(cm.get("title"))
            if not ct:
                continue
            j = len(dt_ & ct) / max(len(dt_ | ct), 1)
            if (cm.get("category") or "") == dc and dc:
                j += 0.15
            if j >= a.min_score:
                scored.append((round(j, 3), cand, cm.get("title", "")[:70]))
        scored.sort(reverse=True)
        if scored:
            print(f"  {dead} ({dm.get('title', '')[:60]}):")
            for sc, cand, title in scored[:3]:
                r = by_series[cand]
                print(f"    {sc:5.3f}  {cand:26s} ${r['pool']:.0f}/day n={r['n']}  | {title}")


if __name__ == "__main__":
    main()
