#!/usr/bin/env python3
"""QUALIFY THE HORIZON-RATIO SURVIVORS — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

`kalshi_horizon_census.py` screened 162 series and `kalshi_horizon_deepdive.py` re-measured the
ratio<=2.0 shortlist over 4 instants. Neither is diligence. This is the diligence pass, and it
differs on four points that each changed an answer in a prior run:

1. STRUCTURE IS AN EXECUTABLE QUESTION, NOT A LABEL. The census classified structure from
   `strike_type` + `mutually_exclusive` on ONE sampled event per series. That is better than the
   ticker-string heuristic it replaced, but it still answers the wrong question. The question is
   not "what shape is this series" — it is "WOULD OUR CODE NET THESE TICKERS?". So this runs the
   LIVE quoter's own `_strike_of` / `_is_ladder_event` / `ladder_pairing` over EVERY event's REAL
   ticker set and reports what those functions actually return. A series is DANGEROUS iff the code
   nets tickers that are not additively correlated; it is SAFE-ABSTAIN if the code declines to net
   (conservative); it is SAFE-LADDER if the code nets a provable monotone threshold ladder.
   ⚠ The census's LADDER_STRIKES tuple includes `between`. A `between` series is a BUCKET series:
   its strikes are ANTI-correlated, so netting them is wrong in the dangerous direction (canon
   §T / `_is_ladder_event` docstring). Bucket series are called out separately here.

2. MUTUAL EXCLUSIVITY IS CHECKED ON EVERY EVENT, not one. A series can mix.

3. THE GATE STACK IS APPLIED. A contract that is R3-two-sided still earns nothing if our own
   selection gate skips it. `two_sided_pct` is the venue's answer; `admit_pct` is ours. The
   binding gate today is MIN_DEPTH_SYM=0.25 (measured, `kalshi_depth_capacity_study.py`), so a
   series can be 100% two-sided and 0% admissible.

4. CAPITAL IS THE DENOMINATOR. MAX_TOTAL_CAPITAL=85 binds at K~7 concurrent markets, so a new
   series is not additive — it must DISPLACE an existing quote. The decision number is therefore
   $/day PER DOLLAR COMMITTED at the deployed shape, benchmarked against KXAAAGASD/W, not the
   series' headline pool.

WHAT THIS STILL CANNOT SEE — and it remains most of the decision:
  * FILL RATE, QUEUE POSITION, ADVERSE SELECTION. Reward-side only. Canon §M8: KXTEMP* earned 91%
    of all reward income and was 100% of the loss. A perfect score here is not a green light.
  * SETTLEMENT TOXICITY (the FIGHTMENTION shape: +745 in-window / -1338 settled). Measuring it
    needs settled positions we do not have on a series we have never traded. Reported UNMEASURED.
  * Capture figures are UPPER BOUNDS (canon §M7d: the model over-predicts ~2-6x).
  * The time sample is the run window only. It does NOT cover the overnight drought (canon §M6,
    allowlist two-sidedness fell to 20.5%).

Run:  python kalshi_survivor_qualify.py [instants] [gap_seconds]
Out:  survivor_qualify.json  (+ stdout report)
"""
import importlib.util
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import kalshi_horizon_census as C

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "survivor_qualify.json")
CENSUS = os.path.join(HERE, "horizon_census.json")

RATIO_CUT = float(os.environ.get("QUAL_RATIO_CUT", 2.0))

# ---- DEPLOYED SHAPE (live.env, handoff 2026-07-23 §1 + quoter defaults). Every one of these is
# a config value, not a guess; the source is named so a reader can re-check it.
JOIN_SIZE = 20.0            # briefing: deployed join shape, 20 ct/side
MAX_MARKET_CAPITAL = 15.0   # briefing: deployed $/contract cap (both sides)
MAX_TOTAL_CAPITAL = 85.0    # live.env KALSHI_MAX_TOTAL_CAPITAL
MIN_DEPTH_SYM = 0.25        # quoter default (:242) — measured as THE binding gate today
MAX_SPREAD_TICKS = 8.0      # quoter default (:241) — measured NOT binding (2/353)
MIN_PRICE = 0.01            # quoter default (:101)
MAX_PRICE = 0.97            # quoter default (:100)
MAX_ACTIVATE_CAPITAL = 150.0  # quoter default (:97) — not in the handoff's live.env block
TICK = 0.01
BENCH = ("KXAAAGASD", "KXAAAGASW")


def _load_quoter():
    """Load the LIVE quoter module to use ITS ladder logic. Re-implementing the netting test here
    would be a second copy free to drift from the one that actually holds the risk."""
    spec = importlib.util.spec_from_file_location(
        "_q", os.path.join(HERE, "maker_kalshi_quoter.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


Q = _load_quoter()

BUCKET_STRIKES = ("between",)
MONOTONE_STRIKES = ("greater", "greater_or_equal", "less", "less_or_equal",
                    "greater_than", "less_than")


def shortlist():
    d = json.load(open(CENSUS))
    return [r["series"] for r in d["rows"]
            if r["median_ratio"] is not None and r["median_ratio"] <= RATIO_CUT]


# ---------------------------------------------------------------- phase 1: structure + fee
def structure_pass(sel, meta):
    """Per series: strike_type over EVERY contract, mutually_exclusive over EVERY event, and what
    the LIVE quoter's ladder logic actually DOES with the real ticker sets."""
    out = {}
    ev_cache = {}
    for s, ps in sel.items():
        tickers = [p["market_ticker"] for p in ps]
        strikes, ev_of = [], defaultdict(list)
        for t in tickers:
            m = meta.get(t)
            if not m:
                continue
            strikes.append(m.get("strike_type") or "")
            ev_of[m.get("event_ticker") or Q._event_key(t)].append(t)
        mut = {}
        cats = set()
        for ev in ev_of:
            if ev not in ev_cache:
                try:
                    d = C.get(f"/events/{ev}")
                    d = d.get("event") or d
                    ev_cache[ev] = (d.get("mutually_exclusive"), d.get("category"))
                except Exception:
                    ev_cache[ev] = (None, None)
            me, cat = ev_cache[ev]
            mut[ev] = me
            if cat:
                cats.add(cat)

        # ---- WHAT THE CODE DOES. This is the load-bearing test.
        nets, abstains, parse_ok, parse_fail = [], [], 0, 0
        for ev, ts in ev_of.items():
            for t in ts:
                if Q._strike_of(t) is None:
                    parse_fail += 1
                else:
                    parse_ok += 1
            (nets if Q._is_ladder_event(ts) else abstains).append(ev)

        st = sorted({x for x in strikes if x})
        me_vals = sorted({v for v in mut.values()}, key=lambda x: str(x))
        any_me = any(v is True for v in mut.values())
        bucket = any(x in BUCKET_STRIKES for x in st)
        monotone = bool(st) and all(x in MONOTONE_STRIKES for x in st)

        # DANGEROUS = the code NETS an event whose strikes are not additively correlated.
        if nets and (any_me or bucket):
            verdict = "DANGEROUS-NETS-NONADDITIVE"
        elif nets and monotone and not any_me:
            verdict = "SAFE-LADDER"
        elif nets and not monotone:
            verdict = "NEEDS-PROBE-NETS-UNTYPED"
        elif not nets:
            verdict = "SAFE-ABSTAIN"
        else:
            verdict = "NEEDS-PROBE"

        fee, fee_type = C.fee_status(s, fetch=True)
        out[s] = {
            "contracts": len(tickers), "events": len(ev_of),
            "strike_types": st, "mutually_exclusive_vals": [str(v) for v in me_vals],
            "any_mutually_exclusive": any_me, "bucket_strikes": bucket,
            "monotone_strikes": monotone, "categories": sorted(cats),
            "events_code_nets": len(nets), "events_code_abstains": len(abstains),
            "strike_parse_ok": parse_ok, "strike_parse_fail": parse_fail,
            "structure_verdict": verdict,
            "maker_fee": fee, "fee_type": fee_type,
            "example_event_netted": nets[0] if nets else None,
            "example_event_abstained": abstains[0] if abstains else None,
        }
    return out


# ---------------------------------------------------------------- phase 2: books
def gate(yl, nl, target):
    """Replicate the FLAT-book path of the live quoter's quote gate, in its own order.
    Returns (admitted, first_gate_that_dropped_it, best_y, best_n, ext_y, ext_n)."""
    by = max((p for p, _ in yl), default=None)
    bn = max((p for p, _ in nl), default=None)
    if by is None or bn is None:
        return False, "one_side_no_bids", by, bn, 0.0, 0.0
    ey = sum(s for _, s in yl)
    en = sum(s for _, s in nl)
    if not (MIN_PRICE < by <= MAX_PRICE) or not (MIN_PRICE < bn <= MAX_PRICE):
        return False, "price_bounds", by, bn, ey, en
    if by + bn >= 1.0:
        return False, "crossed_book", by, bn, ey, en
    addable = MAX_ACTIVATE_CAPITAL / max(by, bn, 0.01)
    if not ((ey + addable >= target) and (en + addable >= target)):
        return False, "unqualifiable", by, bn, ey, en
    void = ey < target or en < target
    if not void:
        spread_ticks = (1.0 - bn - by) / TICK
        sym = min(ey, en) / max(ey, en, 1e-9)
        if spread_ticks > MAX_SPREAD_TICKS:
            return False, "sel_spread", by, bn, ey, en
        if sym < MIN_DEPTH_SYM:
            return False, "sel_sym", by, bn, ey, en
        return True, "pass_join", by, bn, ey, en
    # ACTIVATE path: we would have to supply the depth ourselves
    add_y, add_n = max(JOIN_SIZE, target - ey), max(JOIN_SIZE, target - en)
    if by * add_y + bn * add_n > MAX_ACTIVATE_CAPITAL:
        return False, "activate_too_expensive", by, bn, ey, en
    return True, "pass_activate", by, bn, ey, en


def our_capital(by, bn):
    half = MAX_MARKET_CAPITAL / 2.0
    cy = min(JOIN_SIZE, half / by) if by > 0 else 0.0
    cn = min(JOIN_SIZE, half / bn) if bn > 0 else 0.0
    return cy * by + cn * bn


def main(instants=6, gap=300):
    series = shortlist()
    progs = C.fetch_programs()
    by_series = defaultdict(list)
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if not C.days_of(p) or not p.get("market_ticker"):
            continue
        by_series[p["market_ticker"].split("-")[0]].append(p)
    for b in BENCH:
        if b not in series and b in by_series:
            series.append(b)

    sel = {s: by_series.get(s, []) for s in series if by_series.get(s)}
    tickers = [p["market_ticker"] for ps in sel.values() for p in ps]
    print(f"qualify: {len(sel)} series / {len(tickers)} contracts / "
          f"{instants} instants @ {gap}s")
    meta = C.fetch_markets_batch(tickers)
    print(f"metadata {len(meta)}/{len(tickers)}")

    print("\n--- phase 1: structure + fee (every contract, every event)")
    struct = structure_pass(sel, meta)
    for s in sorted(struct, key=lambda x: struct[x]["structure_verdict"]):
        d = struct[s]
        print(f"  {s:26s} {d['structure_verdict']:26s} st={','.join(d['strike_types']) or '-':22s}"
              f" me={d['any_mutually_exclusive']!s:5s} nets={d['events_code_nets']}/"
              f"{d['events_code_nets']+d['events_code_abstains']} "
              f"parse {d['strike_parse_ok']}ok/{d['strike_parse_fail']}fail {d['maker_fee']}")

    acc = {s: {"n": 0, "two": 0, "adm": 0, "cap_admit": 0.0, "cap_all": 0.0,
               "capital": 0.0, "gates": defaultdict(int), "per_ct": defaultdict(
                   lambda: {"n": 0, "two": 0, "adm": 0, "cap": 0.0, "capital": 0.0})}
           for s in sel}
    stamps = []
    for k in range(instants):
        t0 = time.time()
        stamps.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        print(f"\n--- instant {k+1}/{instants} @ {stamps[-1]}")
        for s, ps in sel.items():
            for p in ps:
                t = p["market_ticker"]
                try:
                    ob = C.get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
                except Exception:
                    continue
                yl, nl = C.levels(ob.get("yes_dollars")), C.levels(ob.get("no_dollars"))
                tgt = float(p.get("target_size_fp") or 0)
                df = float(p.get("discount_factor_bps") or 0) / 10000.0
                if tgt <= 0 or df <= 0:
                    continue
                pool = (p.get("period_reward") or 0) / 10000.0
                days = C.days_of(p)
                cd, sh, two = C.score(yl, nl, tgt, df, pool, days)
                adm, g, by, bn, ey, en = gate(yl, nl, tgt)
                a = acc[s]
                a["n"] += 1
                a["two"] += 1 if two else 0
                a["cap_all"] += cd
                a["gates"][g] += 1
                pc = a["per_ct"][t]
                pc["n"] += 1
                pc["two"] += 1 if two else 0
                if adm:
                    a["adm"] += 1
                    pc["adm"] += 1
                    # R3 first: an admitted quote on an excluded snapshot still pays $0.
                    a["cap_admit"] += cd
                    pc["cap"] += cd
                    cap = our_capital(by, bn)
                    a["capital"] += cap
                    pc["capital"] += cap
        el = time.time() - t0
        print(f"    instant {el:.0f}s  "
              + "  ".join(f"{s}:{acc[s]['adm']}/{acc[s]['n']}" for s in list(sel)[:6]))
        if k < instants - 1 and gap > el:
            time.sleep(gap - el)

    cen = {r["series"]: r for r in json.load(open(CENSUS))["rows"]}
    rows = []
    for s, a in acc.items():
        if not a["n"]:
            continue
        c = cen.get(s, {})
        d = struct[s]
        # per-contract economics, admitted-only, so the ranking is what we could DEPLOY
        pc = [(t, v) for t, v in a["per_ct"].items() if v["adm"]]
        pc.sort(key=lambda kv: -kv[1]["cap"] / max(kv[1]["adm"], 1))
        best = [{"ticker": t,
                 "cap_day": v["cap"] / v["adm"],
                 "capital": v["capital"] / v["adm"],
                 "adm_pct": 100.0 * v["adm"] / v["n"]} for t, v in pc[:10]]
        rows.append({
            "series": s,
            "ours": s in BENCH,
            "median_ratio": c.get("median_ratio"),
            "pool_day": c.get("pool_day"),
            "programs": a["n"] // max(instants, 1),
            "obs": a["n"], "instants": instants,
            "two_sided_pct": 100.0 * a["two"] / a["n"],
            "admit_pct": 100.0 * a["adm"] / a["n"],
            "gate_hist": dict(a["gates"]),
            "cap_day_all_per_instant": a["cap_all"] / instants,
            "cap_day_admitted_per_instant": a["cap_admit"] / instants,
            "capital_per_instant": a["capital"] / instants,
            "cap_per_dollar": (a["cap_admit"] / a["capital"]) if a["capital"] > 0 else None,
            "top_contracts": best,
            **{k: d[k] for k in ("structure_verdict", "strike_types", "any_mutually_exclusive",
                                 "bucket_strikes", "monotone_strikes", "events",
                                 "events_code_nets", "events_code_abstains",
                                 "strike_parse_ok", "strike_parse_fail",
                                 "maker_fee", "fee_type", "categories")},
        })
    rows.sort(key=lambda r: -(r["cap_per_dollar"] or 0))
    json.dump({"generated_utc": datetime.now(timezone.utc).isoformat(),
               "instants": instants, "gap_s": gap, "instant_stamps": stamps,
               "ratio_cut": RATIO_CUT,
               "shape": {"JOIN_SIZE": JOIN_SIZE, "MAX_MARKET_CAPITAL": MAX_MARKET_CAPITAL,
                         "MIN_DEPTH_SYM": MIN_DEPTH_SYM, "MAX_TOTAL_CAPITAL": MAX_TOTAL_CAPITAL},
               "rows": rows}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}\n")
    print(f"{'series':26s} {'ratio':>5} {'2s%':>6} {'adm%':>6} {'$cap/d':>8} {'$cmt':>7} "
          f"{'$/d/$':>7}  fee    structure")
    for r in rows:
        mr = f"{r['median_ratio']:5.2f}" if r["median_ratio"] is not None else "  n/a"
        cpd = f"{r['cap_per_dollar']:7.3f}" if r["cap_per_dollar"] is not None else "      -"
        print(f"{r['series']:26s} {mr} {r['two_sided_pct']:5.1f}% {r['admit_pct']:5.1f}% "
              f"{r['cap_day_admitted_per_instant']:8.2f} {r['capital_per_instant']:7.2f} {cpd}  "
              f"{r['maker_fee']:6s} {r['structure_verdict']}")
    return rows


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x.isdigit()]
    main(int(a[0]) if a else 6, int(a[1]) if len(a) > 1 else 300)
