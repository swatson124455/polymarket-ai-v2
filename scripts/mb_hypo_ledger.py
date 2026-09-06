#!/usr/bin/env python3
"""HYPOTHETICAL forward dollar ledger (operator "both", 2026-09-02).

Answers "how many dollars would we have won?" in the only honest shape:
FORWARD-ONLY accrual from its deploy date. Each run appends one row per
(trader, token) newly RESOLVED in that trader's registered forward window:

    d_ref100 = edge x $100   - the canon per-market edge re-unitized at a
               fixed, disclosed $100/market reference notional (no new
               assumption; it is the funnel's own edge in dollar clothes)
    d_sizer  = edge x stake  - stake = the trader's sizer stake AS OF THIS
               RUN (LCB quarter-Kelly; unproven trader -> $0, so this
               column stays $0 until someone proves - by design)

Rows are append-once (dedup on trader+token); cumulative sums are printed
per trader and total, headline-labeled HYPOTHETICAL per the standing
shadow-lane rule - no real order was placed, fills are paper at recorded
quotes. Estimand/fee/label functions are IMPORTED from canon, never
re-implemented (ZERO_BASED_SIFTER non-negotiables).

    DATABASE_URL=... PYTHONPATH=<mb_readout> python mb_hypo_ledger.py
    ... --self-test   # offline: dedup, math, zero-stake floor
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az            # noqa: E402
import cohort5_qualification as cq     # noqa: E402
import mb_canon as mc                  # noqa: E402
import mb_sizer as msz                 # noqa: E402
import band_tracker as bt              # noqa: E402
import shadow_readout as sr            # noqa: E402
import trader_funnel as tf             # noqa: E402  (sizer env + peak conc)

REF_NOTIONAL = 100.0  # fixed disclosed reference stake, $ per WAGER
# BASIS CONVERSION 2026-09-06 (operator "convert live graders to new
# basis go"): rows are per-WAGER (ladder-aware, mc.wager_rois) and the
# dollars are EXACT per-dollar math: profit = roi x stake. Fresh ledger
# file (old per-market ledger retained as history, never mixed).


def load_ledger(path: str) -> tuple[list, set]:
    rows, seen = [], set()
    if os.path.exists(path):
        for ln in open(path, errors="replace"):
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            rows.append(r)
            seen.add((r["trader"], r["token"], r.get("wts")))
    return rows, seen


def append_rows(path: str, new_rows: list) -> None:
    with open(path, "a") as f:
        for r in new_rows:
            f.write(json.dumps(r) + "\n")


def ledger_math(roi: float, stake: float) -> tuple[float, float]:
    """d_ref100 and d_sizer for one resolved WAGER — exact per-dollar
    math on the ROI basis (profit = roi x stake). Stake below zero is
    impossible by sizer contract; guard anyway (never a negative stake)."""
    return roi * REF_NOTIONAL, roi * max(stake, 0.0)


async def run(args) -> int:
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    groups = [
        (cq.C1_UNTESTED, cq.C1_FWD_EPOCH),
        (cq.INSUFF_PROBES, cq.REREG_EPOCH),
        (cq.SWEEP2_ADMITS, cq.SWEEP2_EPOCH),
        (cq.CRACK_ADMITS, cq.CRACK_EPOCH),
        (cq.SWEEP2_INSUFF, cq.INSUFF57_EPOCH),
        (cq.eligible_admits(args.deep_dive, args.rereview), cq.REREG_EPOCH),
    ]
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    db = await sr.fresh_outcomes(tokens)
    supp = sr.supplement_outcomes(args.supplement, tokens) if tokens else {}
    outcomes = sr.merge_outcomes(db, supp)
    frm = json.load(open(args.fee_rate_map)) if os.path.exists(args.fee_rate_map) else {}
    fee_map = json.load(open(args.fee_map)) if os.path.exists(args.fee_map) else {}
    params = tf.sizer_params_from_env()
    _, seen = load_ledger(args.ledger)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows = []
    for group, epoch in groups:
        # BASIS CONVERSION 2026-09-06: one fresh epoch for all groups
        epoch = cq.BASIS_EPOCH
        gfwd = cq.forward_records(recs, epoch)
        for a in group:
            t_recs = [r for r in gfwd
                      if str(r.get("trader", "")).lower() == a]
            if not t_recs:
                continue
            seq = mc.wager_rois(t_recs, outcomes, frm or {},
                                fee_map or {}, epoch=epoch)
            rois = [x for _, _, x in seq]
            stake = 0.0
            if params is not None and rois:
                lcb = mc.roi_lcb(rois)
                if lcb is not None and lcb > 0:
                    # median recorded OK first-buy fill = same display
                    # reference the funnel uses; depth is trade-time only
                    from types import SimpleNamespace as NS
                    cfg = NS(max_chase=0.02, max_spread=0.05, fee=0.02,
                             econ_floor=cq.EDGE_BAR, p_min=cq.P_BAR,
                             min_markets=cq.N_BAR, fee_map_data=fee_map)
                    row = tf.trader_row(a, epoch, recs, outcomes, frm,
                                        fee_map, cfg, {})
                    srec = tf.display_stake(row, params, frm, fee_map)
                    stake = 0.0 if srec is None else float(srec["stake"])
            for wts, tok, roi in seq:  # canon tuple = (ts, token, roi)
                key = (a, str(tok), wts)   # per-WAGER: ladders repeat tokens
                if key in seen:
                    continue
                seen.add(key)
                d100, dsz = ledger_math(roi, stake)
                new_rows.append({"ts": now, "trader": a, "token": str(tok),
                                 "wts": wts, "roi": round(roi, 6),
                                 "stake": round(stake, 2),
                                 "d_ref100": round(d100, 4),
                                 "d_sizer": round(dsz, 4)})
    append_rows(args.ledger, new_rows)
    rows, _ = load_ledger(args.ledger)
    per = {}
    for r in rows:
        p = per.setdefault(r["trader"], [0, 0.0, 0.0])
        p[0] += 1
        p[1] += r["d_ref100"]
        p[2] += r["d_sizer"]
    print(f"===== {now} HYPOTHETICAL $ LEDGER (paper; no orders placed; "
          f"$ref100 = wager ROI x $100/wager [basis conv 2026-09-06]; $sizer = roi x "
          f"sizer stake, $0 until proven) =====")
    print(f"[hypo] appended {len(new_rows)} newly-resolved rows this run | "
          f"ledger total rows {len(rows)}")
    tot100 = sum(p[1] for p in per.values())
    totsz = sum(p[2] for p in per.values())
    for a, (n, d100, dsz) in sorted(per.items(), key=lambda x: -x[1][1])[:15]:
        print(f"  {a[:12]}..  n={n:<4} $ref100={d100:+9.2f}  $sizer={dsz:+8.2f}")
    if len(per) > 15:
        print(f"  ... {len(per) - 15} more traders in the ledger file")
    print(f"[hypo] CUMULATIVE since ledger start: $ref100={tot100:+.2f} "
          f"$sizer={totsz:+.2f} across {len(per)} traders "
          f"(HYPOTHETICAL - label travels with every quote of these)")
    return 0


def _self_test() -> int:
    print("SELF-TEST - mb_hypo_ledger (offline)\n")
    ok = True
    d100, dsz = ledger_math(0.05, 20.0)
    ok1 = abs(d100 - 5.0) < 1e-9 and abs(dsz - 1.0) < 1e-9
    print(f"  [math] edge .05 -> $5 per $100 ref, $1 at $20 stake : {ok1}")
    ok &= ok1
    d100n, dszn = ledger_math(-0.10, 0.0)
    ok2 = abs(d100n + 10.0) < 1e-9 and dszn == 0.0
    print(f"  [math] negative edge counts against ref; $0 stake -> $0 : {ok2}")
    ok &= ok2
    _, dneg = ledger_math(0.5, -5.0)
    ok3 = dneg == 0.0
    print(f"  [guard] negative stake floored to $0, never inverts sign : {ok3}")
    ok &= ok3
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lp = os.path.join(d, "ledger.jsonl")
        append_rows(lp, [{"trader": "a", "token": "t1", "wts": 1.0,
                          "d_ref100": 1.0, "d_sizer": 0.0}])
        rows, seen = load_ledger(lp)
        ok4 = (len(rows) == 1 and ("a", "t1", 1.0) in seen
               and ("a", "t1", 2.0) not in seen)   # ladder wager = new row
        print(f"  [dedup] append-once keyed on trader+token+wager-ts : {ok4}")
        ok &= ok4
    import inspect
    src = inspect.getsource(run)
    ok5 = "HYPOTHETICAL" in src and "no orders placed" in src
    print(f"  [label] HYPOTHETICAL + no-orders-placed baked into header : {ok5}")
    ok &= ok5
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--deep-dive", dest="deep_dive",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive")
    ap.add_argument("--rereview",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive_rereview")
    ap.add_argument("--supplement",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "gamma_resolutions.json")
    ap.add_argument("--fee-rate-map", dest="fee_rate_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_rate_map.json")
    ap.add_argument("--fee-map", dest="fee_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_map.json")
    ap.add_argument("--ledger",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "hypo_ledger_roi.jsonl")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()
    sys.exit(_self_test() if a.self_test else asyncio.run(run(a)))
