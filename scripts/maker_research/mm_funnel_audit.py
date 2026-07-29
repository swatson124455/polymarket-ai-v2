#!/usr/bin/env python3
"""MAKER SELECTION FUNNEL AUDIT — per-market "why is this market out?"
(session-8 E-D; the Kalshi funnel_audit shape re-derived Poly-native).

The engine's deny counters are CUMULATIVE AGGREGATES (deny={'market_gross_cap':
5901, ...}) — they say how often gates fired, never WHICH market fell where.
This tool re-runs the DISCOVERY/SELECTION waterfall with the ENGINE'S OWN
functions (imported, never copied — the cancel-shape-probe rule) against live
gamma and classifies every rewarded market by the FIRST gate that excludes it:

    fetched -> no-pool -> bad-fields -> excluded-sector -> allowlist ->
    clock-veto -> sector-rank cut -> max-markets cut -> PICKED

READ-ONLY: public gamma only; writes nothing (the engine's discover() writes
universe.json, so this reimplements only the walk, calling the engine's
sector_of / clock_vetoed / parse_iso / load_config for every DECISION).

Usage (from /opt/pa2-maker-live so the SIBLING engine is audited — same
runbook rule as maker_preflight):
    python mm_funnel_audit.py [--engine PATH] [--verbose SECTOR|all]
Env: the same MAKER_* selection knobs the engine reads.
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.parse


def load_engine(path):
    spec = importlib.util.spec_from_file_location("maker_live_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "maker_live_engine.py"))
    ap.add_argument("--verbose", default="",
                    help="print every excluded market in this sector ('all' = every sector)")
    args = ap.parse_args()
    eng = load_engine(os.path.abspath(args.engine))
    cfg = eng.load_config()
    now = time.time()
    print(f"# funnel audit — engine={os.path.abspath(args.engine)}")
    print(f"# knobs: max_markets={cfg['max_markets']} per_sector={cfg['max_per_sector']} "
          f"excluded={sorted(cfg['excluded_sectors'])} "
          f"allowlist={sorted(cfg['sector_allowlist']) or 'off'} "
          f"clock=[{cfg['min_hours_to_end']:.0f}h,{cfg['max_days_to_end']:.0f}d]")

    allow = cfg.get("sector_allowlist") or set()
    stages = ["no-pool", "bad-fields", "excluded-sector", "allowlist",
              "clock-veto", "sector-rank-cut", "max-markets-cut", "PICKED"]
    fell = {s: [] for s in stages}
    survivors, seen, fetched = [], set(), 0
    for page in range(21):
        q = urllib.parse.urlencode({"active": "true", "closed": "false",
                                    "limit": 100, "offset": page * 100,
                                    "order": "volume24hr", "ascending": "false"})
        data = eng.get(f"{eng.GAMMA}?{q}", timeout=15)
        if not data:
            break
        new = 0
        for m in data:
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            new += 1
            fetched += 1
            pool = 0.0
            for r in (m.get("clobRewards") or []):
                try:
                    pool += float(r.get("rewardsDailyRate") or 0)
                except Exception:  # noqa: BLE001 — mirror of the engine walk
                    pass
            row = {"id": m.get("id"), "q": (m.get("question") or "")[:60],
                   "sector": "?", "pool": pool, "msz": 0.0,
                   "end": (m.get("endDate") or "")[:10]}
            if pool <= 0:
                fell["no-pool"].append(row)
                continue
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                v = float(m.get("rewardsMaxSpread")) / 100.0
                msz = float(m.get("rewardsMinSize"))
            except Exception:  # noqa: BLE001
                fell["bad-fields"].append(row)
                continue
            if len(toks) < 2 or v <= 0 or msz <= 0:
                fell["bad-fields"].append(row)
                continue
            row["msz"] = msz
            sec = eng.sector_of(m)
            row["sector"] = sec
            if sec in cfg["excluded_sectors"]:
                fell["excluded-sector"].append(row)
                continue
            if allow and sec not in allow:
                fell["allowlist"].append(row)
                continue
            if eng.clock_vetoed(eng.parse_iso(m.get("endDate")), now,
                                cfg["min_hours_to_end"], cfg["max_days_to_end"]):
                fell["clock-veto"].append(row)
                continue
            survivors.append(row)
        if new == 0 or len(data) < 100:
            break

    # ranking cuts, mirroring discover(): per-sector top-N by pool, then global
    by = {}
    for r in survivors:
        by.setdefault(r["sector"], []).append(r)
    ranked = []
    for sec, ms in by.items():
        ms.sort(key=lambda x: -x["pool"])
        ranked.extend(ms[:cfg["max_per_sector"]])
        fell["sector-rank-cut"].extend(ms[cfg["max_per_sector"]:])
    ranked.sort(key=lambda x: -x["pool"])
    fell["PICKED"] = ranked[:cfg["max_markets"]]
    fell["max-markets-cut"] = ranked[cfg["max_markets"]:]

    print(f"\n# WATERFALL ({fetched} gamma markets fetched):")
    for s in stages:
        print(f"  {s:<17} {len(fell[s]):>5}")
    print("\n# PICKED:")
    for r in fell["PICKED"]:
        print(f"  {r['id']:<9} {r['sector']:<13} pool=${r['pool']:<6.0f} "
              f"msz=${r['msz']:<4.0f} end={r['end']}  {r['q']}")
    if args.verbose:
        for s in stages[:-1]:
            rows = [r for r in fell[s]
                    if args.verbose == "all" or r["sector"] == args.verbose]
            if rows:
                print(f"\n# {s} ({len(rows)} shown):")
                for r in rows:
                    print(f"  {r['id']:<9} {r['sector']:<13} "
                          f"pool=${r['pool']:<6.0f} end={r['end']}  {r['q']}")
    print("\n# NOTE: this is the SELECTION funnel only. Runtime capital-gate")
    print("# denials (market_gross_cap etc.) act on the picked set and are")
    print("# visible in the heartbeat deny counters, not here.")


if __name__ == "__main__":
    main()
