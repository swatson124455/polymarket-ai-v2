#!/usr/bin/env python3
"""DEPTH-CAPACITY STUDY — read-only. "Can we earn more from what we already have?"

Replays the LIVE gate stack (select_footprint -> desired_quotes) against the CURRENT public
book, from a COUNTERFACTUAL FLAT position (inv=0, own=0, no breaker), and attributes every
footprint market to the FIRST gate that drops it. That decomposition does not exist in
plans-*.jsonl — `gated_out` there is a single aggregate number.

Read-only: public endpoints only (no keys, no orders, no writes). Imports the deployed gate
constants and pure planners from maker_kalshi_quoter; touches nothing.

WHAT THIS CANNOT SEE (state it with every number):
  * fill rate / queue position / adverse selection — reward-side only.
  * one instant of the book. Run --repeat N to get a distribution.
  * our real inventory: the counterfactual is "if we were flat". Held markets in the live bot
    take the unwind branches instead, which is a DIFFERENT (and correct) behaviour.

Usage:
  python kalshi_depth_capacity_study.py                 # one snapshot
  python kalshi_depth_capacity_study.py --repeat 3 --gap 120
  python kalshi_depth_capacity_study.py --json out.jsonl
"""
import argparse
import json
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Live config (live.env, read 2026-07-23) — set BEFORE import: the module reads env at import.
LIVE_ENV = {
    "KALSHI_FOOTPRINT_TOP": "40", "KALSHI_PER_SERIES_CAP": "10", "KALSHI_JOIN_SIZE": "20",
    "KALSHI_MAX_MARKET_CAPITAL": "15", "KALSHI_MAX_ACTIVATE_CAPITAL": "15",
    "KALSHI_MAX_TOTAL_CAPITAL": "85", "KALSHI_WIND_DOWN_MIN": "20",
    "KALSHI_MAX_PRICE_DOLLARS": "0.96", "KALSHI_MIN_PRICE_DOLLARS": "0.04",
    "KALSHI_WRITE_BUDGET": "60",
    "KALSHI_SERIES_ALLOW": "KXTEMPDCH,KXTEMPAUSH,KXTEMPLAXH,KXTEMPNYCH,KXTEMPCHIH,"
                           "KXAAAGASD,KXAAAGASW",
    "KALSHI_INV_SOFT_CT": "15", "KALSHI_INV_HARD_CT": "60", "KALSHI_INV_TOLERANCE": "1",
    "KALSHI_SETTLE_UNWIND_MIN": "20", "KALSHI_TAKER_MAX_MKTS": "8",
    "KALSHI_MAX_SPREAD_TICKS": "8", "KALSHI_MIN_DEPTH_SYM": "0.25",
    "KALSHI_MAX_UNWIND_LOSS": "0.02", "KALSHI_HELD_MAX_USD": "20",
    "KALSHI_DAILY_LOSS_HALT_USD": "40", "KALSHI_THROTTLE_STEP_TICKS": "1",
    "KALSHI_REDUCE_ONLY_KEEP_BOTH": "1",
    "KALSHI_TAKER_FLATTEN": "0",       # study never trades; kept for constant parity
    "KALSHI_TRADING_MODE": "dry_run",  # never construct a live client
}
for _k, _v in LIVE_ENV.items():
    os.environ[_k] = _v

import maker_kalshi_quoter as Q  # noqa: E402


def fetch_programs():
    progs, cursor = [], ""
    for _ in range(5):
        d = Q.public_get("/trade-api/v2/incentive_programs?status=active&limit=10000"
                         + (f"&cursor={cursor}" if cursor else ""))
        progs.extend(d.get("incentive_programs", []))
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
    return progs


def classify(m, ob, now):
    """First gate that drops market m from a FLAT book. Mirrors desired_quotes' own order.
    Returns (reason, detail dict). reason 'pass_join' / 'pass_activate' == would be quoted."""
    yl, _ = Q._levels(ob.get("yes_dollars") or [])
    nl, _ = Q._levels(ob.get("no_dollars") or [])
    by = max((p for p, _ in yl), default=None)
    bn = max((p for p, _ in nl), default=None)
    d = {"best_yes": by, "best_no": bn,
         "depth_yes": sum(s for _, s in yl), "depth_no": sum(s for _, s in nl),
         "target": m["target"], "usd_day": round(m["usd_day"], 3),
         "mins_left": round((Q.parse_iso(m["end"]) - now).total_seconds() / 60.0, 1)}
    if not (ob.get("yes_dollars") or ob.get("no_dollars")):
        return "empty_book", d
    if by is None or bn is None:
        return "one_side_no_bids", d
    if not (Q.MIN_PRICE_DOLLARS < by <= Q.MAX_PRICE_DOLLARS) or \
       not (Q.MIN_PRICE_DOLLARS < bn <= Q.MAX_PRICE_DOLLARS):
        return "price_bounds", d
    if by + bn >= 1.0:
        return "crossed_book", d
    ext_y, ext_n = d["depth_yes"], d["depth_no"]
    target = m["target"]
    void = ext_y < target or ext_n < target
    d["void"] = void
    addable = Q.MAX_ACTIVATE_CAPITAL / max(by, bn, 0.01)
    d["addable_ct"] = round(addable, 1)
    if not ((ext_y + addable >= target) and (ext_n + addable >= target)):
        return "unqualifiable_R3", d
    spread_ticks = (1.0 - bn - by) / Q.TICK
    sym = min(ext_y, ext_n) / max(ext_y, ext_n, 1e-9)
    d["spread_ticks"] = round(spread_ticks, 1)
    d["sym"] = round(sym, 3)
    if not void:
        if spread_ticks > Q.MAX_SPREAD_TICKS:
            return "sel_spread", d
        if sym < Q.MIN_DEPTH_SYM:
            return "sel_sym", d
    if void:
        add_y = max(Q.JOIN_SIZE, target - ext_y)
        add_n = max(Q.JOIN_SIZE, target - ext_n)
        cap = by * add_y + bn * add_n
        d["activate_cost"] = round(cap, 2)
        if cap > Q.MAX_ACTIVATE_CAPITAL:
            return "activate_too_expensive", d
        d["mkt_capital"] = round(cap, 2)
        return "pass_activate", d
    q = Q.desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [], now,
                         own=None, inv=0.0, event_delta=0.0, stats=None, cost=0.0)
    if not q:
        return "other_drop", d
    d["mkt_capital"] = round(Q._mkt_capital(q), 2)
    d["quotes"] = [(x["side"], x["price_dollars"], x["count"]) for x in q]
    return "pass_join", d


def r3_earnable(d):
    """R3 (canon): a snapshot pays NOBODY unless BOTH sides of the BOOK meet Target Size.
    Judged on the EXTERNAL book as-is — no credit for depth we would have to add ourselves."""
    return (d.get("depth_yes", 0) >= d.get("target", 1e18)
            and d.get("depth_no", 0) >= d.get("target", 1e18))


def snapshot():
    now = Q.utcnow()
    Q._reads[0] = 0
    progs = fetch_programs()
    fp = Q.select_footprint(progs, now)
    fp_drops = dict(Q.FP_DROPS)
    rows, books = [], {}
    for m in fp:
        try:
            ob = Q.public_get(f"/trade-api/v2/markets/{m['ticker']}/orderbook") \
                  .get("orderbook_fp") or {}
        except Exception as e:
            rows.append({"ticker": m["ticker"], "reason": "fetch_error", "err": repr(e)[:80]})
            continue
        books[m["ticker"]] = ob
        reason, d = classify(m, ob, now)
        rows.append(dict({"ticker": m["ticker"], "series": m["ticker"].split("-")[0],
                          "reason": reason}, **d))
    return {"ts": now.isoformat(), "programs_seen": len(progs), "fp_drops": fp_drops,
            "footprint": len(fp), "rows": rows, "reads": Q._reads[0],
            "_fp": fp, "_books": books, "_now": now}


def relax_scenario(snap, sym, spread):
    """Re-run the SAME cached books with a relaxed selection gate, then apply the real
    cap_desired (MAX_TOTAL_CAPITAL, usd_day priority) to see what actually survives."""
    old_s, old_p = Q.MIN_DEPTH_SYM, Q.MAX_SPREAD_TICKS
    Q.MIN_DEPTH_SYM, Q.MAX_SPREAD_TICKS = sym, spread
    try:
        desired, usd_day, detail = {}, {}, []
        for m in snap["_fp"]:
            ob = snap["_books"].get(m["ticker"])
            if ob is None:
                continue
            reason, d = classify(m, ob, snap["_now"])
            if not reason.startswith("pass"):
                continue
            q = Q.desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [],
                                 snap["_now"], own=None, inv=0.0, event_delta=0.0, cost=0.0)
            if not q:
                continue
            desired[m["ticker"]] = q
            usd_day[m["ticker"]] = m["usd_day"]
            detail.append((m["ticker"], Q._mkt_capital(q), m["usd_day"]))
    finally:
        Q.MIN_DEPTH_SYM, Q.MAX_SPREAD_TICKS = old_s, old_p
    kept, dropped = Q.cap_desired(desired, usd_day)
    return {"admitted": len(desired), "capital_all": sum(c for _, c, _ in detail),
            "kept_after_total_cap": len(kept), "capped_out": dropped,
            "capital_kept": sum(Q._mkt_capital(v) for v in kept.values()),
            "detail": sorted(detail, key=lambda x: -x[2])}


def report(snap):
    rows = snap["rows"]
    print(f"\n=== SNAPSHOT {snap['ts']}  programs={snap['programs_seen']} "
          f"footprint={snap['footprint']} reads={snap['reads']}")
    print("    fp drops:", {k: v for k, v in snap["fp_drops"].items() if k != "drop_allowlist"},
          f"(allowlist {snap['fp_drops'].get('drop_allowlist', 0)})")
    by_series = {}
    counts = {}
    for r in rows:
        counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        by_series.setdefault(r.get("series", "?"), []).append(r)
    print("    footprint by series:", {s: len(v) for s, v in sorted(by_series.items())})
    print("    gate attribution (counterfactual FLAT):")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"       {v:3d}  {k}")
    passes = [r for r in rows if r["reason"].startswith("pass")]
    print(f"    QUOTABLE-IF-FLAT K = {len(passes)}   capital if all quoted = "
          f"${sum(r.get('mkt_capital', 0) for r in passes):.2f} of ${Q.MAX_TOTAL_CAPITAL:.0f}")
    sel = [r for r in rows if r["reason"] in ("sel_spread", "sel_sym")]
    sel_earn = [r for r in sel if r3_earnable(r)]
    print(f"    selection-gate rejects = {len(sel)}  of which R3-earnable NOW "
          f"(both sides >= Target) = {len(sel_earn)}")
    for r in sel:
        print(f"       {r['ticker']:<28} {r['reason']:<10} spread={r.get('spread_ticks')}t "
              f"sym={r.get('sym')} depth={r.get('depth_yes'):.0f}/{r.get('depth_no'):.0f} "
              f"target={r.get('target'):.0f} R3={'Y' if r3_earnable(r) else 'n'}")
    for r in passes:
        print(f"       PASS {r['ticker']:<28} {r['reason']:<13} ${r.get('mkt_capital', 0):.2f} "
              f"usd_day=${r.get('usd_day')} R3={'Y' if r3_earnable(r) else 'n'}")
    if "_fp" in snap:
        print("    RELAX SCENARIOS (same cached books; cap_desired applied at "
              f"MAX_TOTAL_CAPITAL=${Q.MAX_TOTAL_CAPITAL:.0f}):")
        for label, sym, spr in (("live  sym0.25/spr8", 0.25, 8),
                                ("sym0.20           ", 0.20, 8),
                                ("sym0.15           ", 0.15, 8),
                                ("sym0.10           ", 0.10, 8),
                                ("sym0.05           ", 0.05, 8),
                                ("sym0.00/spr8 (off)", 0.0, 8),
                                ("sym0.00/spr12     ", 0.0, 12)):
            s = relax_scenario(snap, sym, spr)
            print(f"       {label}  admitted={s['admitted']:2d} "
                  f"capital_if_all=${s['capital_all']:6.2f}  ->  after total-cap: "
                  f"kept={s['kept_after_total_cap']:2d} ${s['capital_kept']:6.2f} "
                  f"capped_out={s['capped_out']}")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--gap", type=float, default=90.0, help="seconds between snapshots")
    ap.add_argument("--json", default=None, help="append raw snapshots to this jsonl")
    a = ap.parse_args()
    agg = {}
    for i in range(a.repeat):
        s = snapshot()
        c = report(s)
        for k, v in c.items():
            agg[k] = agg.get(k, 0) + v
        if a.json:
            with open(a.json, "a") as f:
                f.write(json.dumps({k: v for k, v in s.items()
                                    if not k.startswith("_")}) + "\n")
        if i + 1 < a.repeat:
            time.sleep(a.gap)
    if a.repeat > 1:
        print(f"\n=== POOLED over {a.repeat} snapshots (NOT independent — same markets, "
              f"{a.gap:.0f}s apart)")
        for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
            print(f"   {v:4d}  {k}   ({v / a.repeat:.2f}/cycle)")


if __name__ == "__main__":
    main()
