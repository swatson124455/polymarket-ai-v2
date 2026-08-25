#!/usr/bin/env python3
"""TRADER FUNNEL - the one-table trader review (operator "build it",
2026-08-25). Every roster trader on one line, every line in one of four
states, sorted so the top of the table is the decision:

    TRIAL   accruing under the anytime-valid e-process (sorted by e desc)
    PASSED  locked QUALIFIES (a proposal - composition still operator-gated)
    FAILED  locked (DOES NOT QUALIFY / futility / NOT DEMONSTRATED)
    OBS     watched with NO registered per-trader test (diagnostic only)

READ-ONLY: this is a VIEW. It writes no locks and changes no test - the
verdict-writer remains cohort5_qualification.py, and this script IMPORTS
that module's own groups, epochs and primitives so every number here is
the grader's number, never a re-implementation (MEASUREMENT_CANON
consumption rule).

    DATABASE_URL=... PYTHONPATH=<mb_readout> python scripts/trader_funnel.py
    ... --self-test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402
import band_tracker as bt  # noqa: E402
import cohort5_qualification as cq  # noqa: E402  (groups/epochs/bars: theirs)
import mb_canon as mc  # noqa: E402
import shadow_readout as sr  # noqa: E402

AUDIT = "/opt/pa2-shared/mb_copyable_data/chain_audit.json"


def days_since(epoch: float) -> int:
    return int((datetime.now(timezone.utc).timestamp() - epoch) / 86400)


def trader_row(a: str, epoch: float, recs: list, outcomes: dict,
               frm: dict, fee_map: dict, cfg) -> dict:
    """One trader's canon numbers in their forward window - the SAME
    primitives and arguments the grader uses."""
    gfwd = cq.forward_records(recs, epoch)
    t_recs = [r for r in gfwd if str(r.get("trader", "")).lower() == a]
    seq = mc.per_market_edges(t_recs, outcomes, frm or {}, fee_map or {},
                              epoch=epoch)
    edges = [e for _, _, e in seq]
    res = sr.cohort_readout(gfwd, outcomes, epoch, a, cfg)
    return {
        "n": len(edges),
        "e": bt.e_value(edges) if edges else None,
        "edge": mc.pooled_edge(seq),
        "ok": res.get("ok_rate"),
        "first_buys": res.get("first_buys"),
    }


def fmt(v, spec, dash="   -"):
    if v is None:
        return dash
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return dash


async def run(args) -> int:
    audit = json.load(open(args.audit))
    clean = sorted({str(a).lower() for a in audit.get("clean", [])})
    if not clean:
        print("[funnel] FATAL: empty roster - refusing to print an empty "
              "table as 'no traders'")
        return 2
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    db = await sr.fresh_outcomes(tokens)
    supp = sr.supplement_outcomes(args.supplement, tokens)
    outcomes = sr.merge_outcomes(db, supp)
    frm = {}
    if os.path.exists(args.fee_rate_map):
        frm = json.load(open(args.fee_rate_map))
    fee_map = {}
    if os.path.exists(args.fee_map):
        fee_map = json.load(open(args.fee_map))
    from types import SimpleNamespace as NS
    cfg = NS(max_chase=0.02, max_spread=0.05, fee=0.02,
             econ_floor=cq.EDGE_BAR, p_min=cq.P_BAR,
             min_markets=cq.N_BAR, fee_map_data=fee_map)
    locks = sr.load_locks(args.locks)

    # group + epoch per trader, straight from the grader's module
    originals = set(cq.eligible_admits(args.deep_dive, args.rereview))
    c1 = set(cq.C1_UNTESTED)
    probes12 = set(cq.INSUFF_PROBES)
    rows = []
    for a in clean:
        if a in locks:
            lk = locks[a]
            v = str(lk.get("verdict", ""))
            state = "PASSED" if v == "QUALIFIES" else "FAILED"
            rows.append({"a": a, "state": state, "n": lk.get("resolved"),
                         "e": None, "edge": lk.get("edge"), "ok": None,
                         "days": None,
                         "note": f"locked {lk.get('locked_at')}: {v[:34]}"})
            continue
        if a in c1:
            epoch, grp = cq.C1_FWD_EPOCH, "c1-untested"
        elif a in probes12:
            epoch, grp = cq.REREG_EPOCH, "insuff-probe"
        elif a in originals:
            epoch, grp = cq.REREG_EPOCH, "orig-rereg"
        else:
            # watched, no registered per-trader test (e.g. cohort4, fbfd
            # probe) - diagnostic only, honestly labeled
            r = trader_row(a, 0.0, recs, outcomes, frm, fee_map, cfg)
            rows.append({"a": a, "state": "OBS", "n": r["n"], "e": None,
                         "edge": r["edge"], "ok": r["ok"], "days": None,
                         "note": "no per-trader test registered - "
                                 "diagnostic only"})
            continue
        r = trader_row(a, epoch, recs, outcomes, frm, fee_map, cfg)
        rows.append({"a": a, "state": "TRIAL", "n": r["n"], "e": r["e"],
                     "edge": r["edge"], "ok": r["ok"],
                     "days": days_since(epoch), "note": grp})

    order = {"TRIAL": 0, "PASSED": 1, "OBS": 2, "FAILED": 3}
    rows.sort(key=lambda x: (order[x["state"]],
                             -(x["e"] if x["e"] is not None else -1)))
    now = datetime.now(timezone.utc)
    n_trial = sum(1 for x in rows if x["state"] == "TRIAL")
    n_pass = sum(1 for x in rows if x["state"] == "PASSED")
    n_fail = sum(1 for x in rows if x["state"] == "FAILED")
    print(f"===== {now:%Y-%m-%dT%H:%MZ} TRADER FUNNEL - roster {len(clean)} "
          f"| TRIAL {n_trial} | PASSED {n_pass} | FAILED {n_fail} "
          f"(bar: e>={cq.C1_E_REJECT:.0f} + edge>=+{cq.EDGE_BAR:.02f} + "
          f"ok>={cq.OKRATE_BAR:.02f}; futility {cq.C1_FUTILITY_N}) =====")
    print(f"{'TRADER':<14} {'STATE':<7} {'n':>4} {'e':>7} {'edge':>8} "
          f"{'ok%':>4} {'days':>4}  note")
    for x in rows:
        print(f"{x['a'][:12]+'..':<14} {x['state']:<7} "
              f"{fmt(x['n'], 'd'):>4} {fmt(x['e'], '.2f'):>7} "
              f"{fmt(x['edge'], '+.4f'):>8} "
              f"{fmt(None if x['ok'] is None else x['ok']*100, '.0f'):>4} "
              f"{fmt(x['days'], 'd'):>4}  {x['note']}")
    if n_pass:
        print(f"  >> {n_pass} PASSED = PROPOSAL(S) - composition is an "
              f"operator gate")
    return 0


def _self_test() -> int:
    print("SELF-TEST - trader funnel (offline)\n")
    ok = True
    ok1 = not (set(cq.C1_UNTESTED) & set(cq.INSUFF_PROBES))
    print(f"  [groups] C1 and probes disjoint : {ok1}"); ok &= ok1
    ok2 = fmt(None, ".2f") == "   -" and fmt(1.5, ".2f") == "1.50"
    print(f"  [fmt] None renders as dash, never fake zero : {ok2}"); ok &= ok2
    order = {"TRIAL": 0, "PASSED": 1, "OBS": 2, "FAILED": 3}
    rows = [{"state": "FAILED", "e": None}, {"state": "TRIAL", "e": 2.0},
            {"state": "TRIAL", "e": 9.0}, {"state": "PASSED", "e": None}]
    rows.sort(key=lambda x: (order[x["state"]],
                             -(x["e"] if x["e"] is not None else -1)))
    ok3 = [r["state"] for r in rows] == ["TRIAL", "TRIAL", "PASSED", "FAILED"] \
        and rows[0]["e"] == 9.0
    print(f"  [sort] highest-e TRIAL first, FAILED last : {ok3}"); ok &= ok3
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    # the probe must not match its own definition line: count call sites of
    # the lock-writer pattern; exactly zero may exist outside this check
    ok4 = src.count("sr." + "write_lock(") == 0
    print(f"  [read-only] funnel never writes locks : {ok4}"); ok &= ok4
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="one-table trader review (view)")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--audit", default=AUDIT)
    ap.add_argument("--deep-dive", dest="deep_dive",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive")
    ap.add_argument("--rereview",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive_rereview")
    ap.add_argument("--supplement",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "gamma_resolutions.json")
    ap.add_argument("--fee-map", dest="fee_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_map.json")
    ap.add_argument("--fee-rate-map", dest="fee_rate_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_rate_map.json")
    ap.add_argument("--locks",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "cohort5_qual_locks.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else asyncio.run(run(a)))
