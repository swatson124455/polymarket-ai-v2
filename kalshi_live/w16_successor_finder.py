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
import os
import re
import sys
import time
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA = "/opt/pa2-maker-kalshi-live"
STOP = {"the", "a", "an", "of", "on", "in", "for", "will", "be", "by", "at",
        "to", "this", "week", "today", "daily", "what", "when", "how", "its"}

# §6.1 (operator "proceed" 2026-08-12): an alarm that exits 0 is machine-invisible — the
# 08-08 fix was WEAKER than the crash it replaced (exit 1 -> systemd `failed`). Every alarm
# now counts, and main exits nonzero when any fired: full output still prints, and systemd
# marks the unit failed so the alarm is visible without reading prose.
_ALARMS = [0]


def _alarm(msg):
    _ALARMS[0] += 1
    print(f"# ALARM {msg}", flush=True)


def _exit_code():
    return 1 if _ALARMS[0] else 0


def _read_json(path, label):
    """§6.3: a side-input that fails to load must ALARM, never silently shrink the scan
    universe (the pre-fix `except: pass` made 'file unreadable' identical to 'no data')."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as exc:
        _alarm(f"{label} unreadable ({exc.__class__.__name__}: {exc}) — this input is "
               f"EMPTY for this run; the sections that consume it cover less than they claim")
        return None


def _get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=30))


def _tokens(title):
    return {w for w in re.findall(r"[a-z]+", (title or "").lower())
            if w not in STOP and len(w) > 2}


def _env():
    """Config for this report — os.environ FIRST, live.env only as a fallback.

    Same mechanism (and same fix) as w17_coverage_ledger._env: the timer unit already carries
    `EnvironmentFile=/opt/pa2-maker-kalshi-live/live.env`, so systemd (root) injects KALSHI_*
    into this process. Opening the 0600 root-owned file directly as `polymarket` raised
    PermissionError and killed every timer run before any output. Fixed 2026-08-08."""
    env = {k: v for k, v in os.environ.items() if k.startswith("KALSHI_")}
    src = "environ"
    if not env:
        src = "live.env"
        try:
            with open(os.path.join(DATA, "live.env")) as fh:
                for line in fh:
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.strip().partition("=")
                        env[k] = v
        except OSError as exc:
            # STANDING DETECTOR: an empty allowlist makes "0 programless series" print as a
            # clean bill of health. Never let that pass silently — counted, and main exits
            # nonzero (§6.1).
            src = f"NONE ({exc.__class__.__name__})"
            _alarm(f"config unreadable: no KALSHI_* in the environment and live.env "
                   f"could not be opened ({exc}) — the allowlist is EMPTY, so the health and "
                   f"successor sections below cover NOTHING")
    print(f"# config source: {src} ({len(env)} KALSHI_* keys)", flush=True)
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=0.25)
    ap.add_argument("--allow", default=None, help="comma list; default = live.env")
    a = ap.parse_args()
    if a.allow:
        allow = [s for s in a.allow.split(",") if s.strip()]
    else:
        allow = [s for s in _env().get("KALSHI_SERIES_ALLOW", "").split(",") if s.strip()]
    if not allow:
        # §6.4: a PARTIAL environ (KALSHI_* present but SERIES_ALLOW missing/empty) used to
        # yield allowlist=[] with no alarm — every section then covered nothing while
        # printing cleanly. Alarm and stop BEFORE spending any venue reads.
        _alarm("allowlist is EMPTY (KALSHI_SERIES_ALLOW missing or blank) — nothing to "
               "cover; refusing to print a vacuous clean bill of health")
        sys.exit(_exit_code())
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

    # C-2 (identity review, operator 'go' 2026-08-06): the INVERSE direction — a
    # convicted / denied / banned franchise re-entering clean under a new name. Scan the
    # same way; matches are operator decisions for CONVICTION transfer (lineage table
    # kalshi_lineage.json holds ruled mappings, both trust and conviction).
    convicted = set()
    fbdoc = _read_json(os.path.join(DATA, "kalshi_credit_feedback.json"),
                       "credit-feedback table (conviction-evasion scan input)")
    if fbdoc:
        convicted |= {s for s, r in (fbdoc.get("series") or {}).items()
                      if r.get("verdict") == "never_paid_due"}
    st = _read_json(os.path.join(DATA, "quoter_state.json"),
                    "quoter state (mkt_out conviction-evasion scan input)")
    if st:
        convicted |= {str(t).split("-")[0] for t in (st.get("mkt_out") or [])}
    scan_sets = [("programless payer", sorted(dead_allow)),
                 ("CONVICTED/BANNED (conviction-evasion watch)",
                  sorted(convicted - set(allow)))]
    print("\n## successor candidates (ranked; both directions)")
    active_nonallow = [s for s in by_series if s not in set(allow)]
    for _label, _dead_list in scan_sets:
        print(f"# direction: {_label}")
        for dead in _dead_list:
            _scan_one(dead, active_nonallow, by_series, a, series_meta)
    sys.exit(_exit_code())


def _scan_one(dead, active_nonallow, by_series, a, series_meta):
        dm = series_meta(dead)
        dt_, dc = _tokens(dm.get("title")), (dm.get("category") or "")
        if not dt_:
            print(f"  {dead}: (no title metadata)")
            return
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
