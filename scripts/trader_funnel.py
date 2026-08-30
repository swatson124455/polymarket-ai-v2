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
import mb_sizer as msz  # noqa: E402  (pre-registered sizing rule, read-only)
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
    # median OK first-buy fill (+ its token) = the display reference point
    # for the sizer column; trade-time sizing uses the live quote instead
    fills = sorted((float(r["shadow_fill"]), str(r.get("token_id")))
                   for r in t_recs
                   if r.get("first_buy") and r.get("verdict") == "OK"
                   and isinstance(r.get("shadow_fill"), (int, float))
                   and 0 < r["shadow_fill"] < 1)
    med = fills[len(fills) // 2] if fills else None
    return {
        "n": len(edges),
        "e": bt.e_value(edges) if edges else None,
        "edge": mc.pooled_edge(seq),
        "ok": res.get("ok_rate"),
        "first_buys": res.get("first_buys"),
        "lcb": msz.lcb_edge(edges, bt.e_value, bt.Y_MIN) if edges else None,
        "med_fill": med,
    }


def sizer_params_from_env():
    """All four operator parameters or None - the sizer has NO defaults and
    the funnel does not invent them (zero-base rule)."""
    names = ("MB_SIZER_BANKROLL", "MB_SIZER_KELLY_MULT",
             "MB_SIZER_CONCURRENCY", "MB_SIZER_MIN_VIABLE")
    vals = [os.environ.get(n) for n in names]
    if any(v is None for v in vals):
        return None
    return {"bankroll": float(vals[0]), "kelly_mult": float(vals[1]),
            "concurrency": int(vals[2]), "min_viable": float(vals[3])}


def display_stake(r: dict, params, frm: dict, fee_map: dict):
    """Read-only display stake at the trader's median recorded fill.
    Book depth is a TRADE-TIME input, not knowable per-trader here, so the
    display passes a non-binding depth and says so in the header."""
    if params is None or not r.get("med_fill") or r.get("lcb") is None:
        return None
    fill, tok = r["med_fill"]
    fee, _src = mc.canon_fee(tok, fill, frm or {}, fee_map or {})
    return msz.recommend_stake_from_lcb(
        r["lcb"], fill, fee, book_depth_usd=1e12, **params)


def fmt(v, spec, dash="   -"):
    if v is None or (isinstance(v, float) and v != v):   # None or NaN
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
    sz = sizer_params_from_env()

    # group + epoch per trader, straight from the grader's module
    originals = set(cq.eligible_admits(args.deep_dive, args.rereview))
    c1 = set(cq.C1_UNTESTED)
    probes12 = set(cq.INSUFF_PROBES)
    sweep2 = set(cq.SWEEP2_ADMITS)
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
        elif a in sweep2:
            epoch, grp = cq.SWEEP2_EPOCH, "sweep2-admit"
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
        srec = display_stake(r, sz, frm, fee_map)
        rows.append({"a": a, "state": "TRIAL", "n": r["n"], "e": r["e"],
                     "edge": r["edge"], "ok": r["ok"], "lcb": r["lcb"],
                     "stake": None if srec is None else srec["stake"],
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
    if sz is None:
        print("[sizer] stakes unset - set MB_SIZER_BANKROLL / "
              "MB_SIZER_KELLY_MULT / MB_SIZER_CONCURRENCY / "
              "MB_SIZER_MIN_VIABLE (operator values; sizer has no defaults)")
    else:
        print(f"[sizer] bankroll ${sz['bankroll']:.0f} x mult "
              f"{sz['kelly_mult']} / conc {sz['concurrency']} @ each "
              f"trader's median recorded fill; book depth is trade-time, "
              f"not applied here")
    print(f"{'TRADER':<14} {'STATE':<7} {'n':>4} {'e':>7} {'edge':>8} "
          f"{'lcb':>8} {'$stake':>7} {'ok%':>4} {'days':>4}  note")
    for x in rows:
        print(f"{x['a'][:12]+'..':<14} {x['state']:<7} "
              f"{fmt(x['n'], 'd'):>4} {fmt(x['e'], '.2f'):>7} "
              f"{fmt(x['edge'], '+.4f'):>8} "
              f"{fmt(x.get('lcb'), '+.4f'):>8} "
              f"{fmt(x.get('stake'), '.2f'):>7} "
              f"{fmt(None if x['ok'] is None or x['ok'] != x['ok'] else x['ok']*100, '.0f'):>4} "
              f"{fmt(x['days'], 'd'):>4}  {x['note']}")
    if n_pass:
        print(f"  >> {n_pass} PASSED = PROPOSAL(S) - composition is an "
              f"operator gate")
    return 0


def _self_test() -> int:
    print("SELF-TEST - trader funnel (offline)\n")
    ok = True
    ok1 = not (set(cq.C1_UNTESTED) & set(cq.INSUFF_PROBES)) \
        and not (set(cq.SWEEP2_ADMITS) & (set(cq.C1_UNTESTED)
                                          | set(cq.INSUFF_PROBES)))
    print(f"  [groups] registered groups disjoint : {ok1}"); ok &= ok1
    # pattern-completeness (2026-08-30 defect: sweep2 admits fell to OBS
    # as "no test registered"): EVERY address-list group the grader
    # defines must be consulted by name somewhere in this file
    import inspect as _i
    src0 = _i.getsource(sys.modules[__name__])
    groups = [n for n, v in vars(cq).items()
              if isinstance(v, (list, tuple, set)) and v
              and all(isinstance(x, str) and x.startswith("0x") for x in v)]
    missing = [n for n in groups if f"cq.{n}" not in src0]
    ok1b = bool(groups) and not missing
    print(f"  [groups] funnel consults every grader group "
          f"({len(groups)} found{', MISSING: ' + str(missing) if missing else ''}) : {ok1b}")
    ok &= ok1b
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
    saved = {n: os.environ.pop(n, None) for n in
             ("MB_SIZER_BANKROLL", "MB_SIZER_KELLY_MULT",
              "MB_SIZER_CONCURRENCY", "MB_SIZER_MIN_VIABLE")}
    try:
        ok5 = sizer_params_from_env() is None
        os.environ.update({"MB_SIZER_BANKROLL": "500",
                           "MB_SIZER_KELLY_MULT": "0.25",
                           "MB_SIZER_CONCURRENCY": "20",
                           "MB_SIZER_MIN_VIABLE": "1"})
        p = sizer_params_from_env()
        ok5 = ok5 and p == {"bankroll": 500.0, "kelly_mult": 0.25,
                            "concurrency": 20, "min_viable": 1.0}
    finally:
        for n, v in saved.items():
            os.environ.pop(n, None)
            if v is not None:
                os.environ[n] = v
    print(f"  [sizer] env foursome all-or-nothing, no defaults : {ok5}")
    ok &= ok5
    r_neg = {"med_fill": (0.50, "tok_x"), "lcb": -0.10}
    p = {"bankroll": 500.0, "kelly_mult": 0.25, "concurrency": 20,
         "min_viable": 1.0}
    s_neg = display_stake(r_neg, p, {}, {})
    ok6 = (display_stake(r_neg, None, {}, {}) is None
           and s_neg is not None and s_neg["stake"] == 0.0
           and display_stake({"med_fill": None, "lcb": 0.1}, p, {}, {})
           is None)
    print(f"  [sizer] unproven lcb -> $0; no params -> no column : {ok6}")
    ok &= ok6
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
