#!/usr/bin/env python3
"""Band 0.65-0.85 forward test — anytime-valid e-process tracker.

Pre-registration: docs/BAND_PREREGISTRATION.md (operator-ratified 2026-08-19).
H0: edge <= 0; mixture betting e-process; REJECT at e >= 20; economic gate
(pooled band edge >= +0.02) applied only after rejection; futility at 600.
Forward window ONLY: detect_ts >= 2026-08-19T18:00:00Z. Verdict locks via the
shared immutable-lock helpers. READ-ONLY vs trading state; writes only its
lock file. Every stage asserts non-empty inputs where emptiness would be
ambiguous (an empty FORWARD window right after registration is expected and
reported as accruing, never as evidence).

    DATABASE_URL=... PYTHONPATH=<mb_readout> python scripts/band_tracker.py
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
import mb_canon as mc  # noqa: E402  (ROI basis, conversion 2026-09-06)
import shadow_readout as sr  # noqa: E402

EPOCH = datetime(2026, 8, 19, 18, 0, 0, tzinfo=timezone.utc).timestamp()
BAND_LO, BAND_HI = 0.65, 0.85
LAMBDAS = (0.05, 0.1, 0.2, 0.4, 0.6, 0.8)
E_REJECT = 20.0
ECON_FLOOR = 0.02
N_FUT = 600
Y_MIN = -1.02  # per-market edge lower bound (fee-inclusive)

# ── BASIS CONVERSION 2026-09-06 (operator "band tracker convert go") ────
# The band test re-registers on the ruled basis: atoms = per-WAGER ROI of
# band fills (BAND_LO <= fill < BAND_HI, ladder-aware), e = mc.roi_e_value,
# reject at E_REJECT, PASS gate = LCB net winnings >= $100/wk @ $100/wager,
# futility = 1 week time-based. Fresh epoch = the conversion epoch
# (= cohort5_qualification.BASIS_EPOCH; duplicated here because cohort5
# imports this module — the equality is PINNED by cohort5's self-test).
# The OLD registration retires UNLOCKED at conversion (final read
# 2026-09-06T11:42Z: n=488, e=0.415, pooled +0.0089 — recorded, no
# verdict); its constants and helper stay above for the historical record.
ROI_EPOCH = datetime(2026, 9, 6, 22, 30, 0, tzinfo=timezone.utc).timestamp()
ROI_LOCK_KEY = "band_0.65_0.85_roi"
ROI_FLOOR_WK = 100.0   # = cohort5 WEEKLY_FLOOR_USD (pinned by its self-test)
ROI_FUTILITY_DAYS = 7.0


def e_value(edges_in_order: list) -> float:
    """Uniform-mixture betting e-process for H0: mean <= 0.
    K_t = mean_j prod_i (1 + lambda_j * y_i); wealth stays positive because
    1 + lambda*y >= 1 - 0.8*1.02 > 0 for y >= Y_MIN."""
    assert all(y >= Y_MIN - 1e-9 for y in edges_in_order), "edge below bound"
    wealth = [1.0] * len(LAMBDAS)
    for y in edges_in_order:
        for j, lam in enumerate(LAMBDAS):
            wealth[j] *= (1.0 + lam * y)
    return sum(wealth) / len(wealth)


def band_market_edges(recs: list, outcomes: dict, fee_rate_map: dict,
                      fee_map: dict) -> list:
    """[(order_key, per-market mean edge)] for resolved band markets in the
    FORWARD window, venue fees. order_key = the market's first detect_ts
    (fixed, pre-registered ordering)."""
    fwd = [r for r in recs if float(r.get("detect_ts") or 0) >= EPOCH]
    per_tok: dict = {}
    first_ts: dict = {}
    for r in fwd:
        if not (r.get("first_buy") and r.get("verdict") == "OK"):
            continue
        f = r.get("shadow_fill")
        if not isinstance(f, (int, float)) or not (BAND_LO <= f < BAND_HI):
            continue
        tok = str(r.get("token_id"))
        o = outcomes.get(tok)
        if o is None:
            continue
        rate = fee_rate_map.get(tok)
        if rate is not None:
            fee_d = float(rate) * f * (1.0 - f)
        elif fee_map.get(tok) == 0:
            fee_d = 0.0
        else:
            fee_d = 0.02 * f
        per_tok.setdefault(tok, []).append(o - f - fee_d)
        ts = float(r.get("detect_ts") or 0)
        first_ts[tok] = min(first_ts.get(tok, ts), ts)
    return sorted((first_ts[t], sum(v) / len(v)) for t, v in per_tok.items())


async def run(args) -> int:
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    recs, _ = az.repair_records(recs, 0.02, 0.05, sr.TRUST1)
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    db = await sr.fresh_outcomes(tokens)
    supp = sr.supplement_outcomes(args.supplement, tokens)
    outcomes = sr.merge_outcomes(db, supp)
    fee_map = (json.load(open(args.fee_map))
               if os.path.exists(args.fee_map) else {})
    frm = (json.load(open(args.fee_rate_map))
           if os.path.exists(args.fee_rate_map) else {})
    locks = sr.load_locks(args.locks)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    if ROI_LOCK_KEY in locks:
        lk = locks[ROI_LOCK_KEY]
        print(f"[band] VERDICT LOCKED {lk['locked_at']}: {lk['verdict']} "
              f"(e={lk.get('p')}, n={lk.get('resolved')})")
        return 0
    # BASIS CONVERSION 2026-09-06: per-WAGER ROI of band fills from the
    # fresh conversion epoch; the old registration retired unlocked (see
    # the constants block). Band filter mirrors the registered [lo, hi).
    band_recs = [r for r in recs
                 if isinstance(r.get("shadow_fill"), (int, float))
                 and BAND_LO <= r["shadow_fill"] < BAND_HI]
    seq = mc.wager_rois(band_recs, outcomes, frm, fee_map, epoch=ROI_EPOCH)
    rois = [x for _, _, x in seq]
    n = len(rois)
    el_days = max((datetime.now(timezone.utc).timestamp() - ROI_EPOCH)
                  / 86400.0, 1e-9)
    if n == 0:
        if el_days >= ROI_FUTILITY_DAYS:
            sr.write_lock(args.locks, locks, ROI_LOCK_KEY, {
                "locked_at": stamp, "resolved": 0, "roi": None, "p": None,
                "verdict": "NOT DEMONSTRATED (futility 1wk)",
                "basis": "roi-netwin-20260906",
                "source": "band_tracker ROI e-process"})
            print(f"[band] {stamp} *** VERDICT LOCKED: NOT DEMONSTRATED "
                  f"(futility 1wk, 0 resolved) ***")
        else:
            print(f"[band] {stamp} ROI-basis window open, 0 resolved band "
                  f"wagers yet (epoch 2026-09-06T22:30Z) - accruing")
        return 0
    ev = mc.roi_e_value(rois, 0.0)
    mean_roi = sum(rois) / n
    print(f"[band] {stamp} n={n} resolved band WAGERS | mean roi "
          f"{mean_roi:+.4f} | e-value {ev:.3f} (reject at {E_REJECT:.0f}) | "
          f"futility 1wk [ROI basis, conv 2026-09-06]")
    verdict = None
    lcb = None
    if ev >= E_REJECT:
        lcb = mc.roi_lcb(rois, e_bar=E_REJECT)
        wk = (lcb * 100.0 * (n / el_days) * 7.0 if lcb is not None else None)
        verdict = (f"PASS - LCB net winnings ${wk:.0f}/wk >= "
                   f"${ROI_FLOOR_WK:.0f}/wk floor"
                   if wk is not None and wk >= ROI_FLOOR_WK else
                   f"SIGNIFICANT but BELOW MONEY FLOOR "
                   f"(${0 if wk is None else wk:.0f}/wk)")
    elif el_days >= ROI_FUTILITY_DAYS:
        verdict = "NOT DEMONSTRATED (futility 1wk)"
    if verdict:
        sr.write_lock(args.locks, locks, ROI_LOCK_KEY, {
            "locked_at": stamp, "resolved": n, "roi": round(mean_roi, 6),
            "p": ev, "verdict": verdict, "basis": "roi-netwin-20260906",
            "source": "band_tracker ROI e-process first crossing"})
        print(f"[band] *** VERDICT LOCKED: {verdict} ***")
    return 0


def _self_test() -> int:
    print("SELF-TEST - band_tracker (offline)")
    ok = True
    ok1 = e_value([]) == 1.0
    ok2 = e_value([0.5] * 30) > E_REJECT      # strong positive run rejects
    ok3 = e_value([-0.5] * 30) < 1.0          # negative run shrinks wealth
    ok4 = e_value([0.0] * 100) == 1.0         # null edges never move it
    print(f"  [e] empty=1, strong+ rejects, neg shrinks, zero flat : "
          f"{ok1 and ok2 and ok3 and ok4}")
    ok &= ok1 and ok2 and ok3 and ok4
    recs = [
        {"detect_ts": EPOCH + 1, "first_buy": True, "verdict": "OK",
         "shadow_fill": 0.70, "token_id": "t1"},
        {"detect_ts": EPOCH - 1, "first_buy": True, "verdict": "OK",
         "shadow_fill": 0.70, "token_id": "t_pre"},    # pre-epoch: excluded
        {"detect_ts": EPOCH + 2, "first_buy": True, "verdict": "OK",
         "shadow_fill": 0.90, "token_id": "t_out"},    # out of band
        {"detect_ts": EPOCH + 3, "first_buy": True, "verdict": "OK",
         "shadow_fill": 0.70, "token_id": "t_unres"},  # unresolved
    ]
    seq = band_market_edges(recs, {"t1": 1, "t_pre": 1, "t_out": 1},
                            {"t1": 0.05}, {})
    ok5 = (len(seq) == 1
           and abs(seq[0][1] - (1 - 0.70 - 0.05 * 0.7 * 0.3)) < 1e-12)
    print(f"  [window] pre-epoch/out-of-band/unresolved excluded; venue fee "
          f"exact : {ok5}")
    ok &= ok5
    ok6 = EPOCH == datetime(2026, 8, 19, 18, 0, 0,
                            tzinfo=timezone.utc).timestamp()
    print(f"  [epoch] fixed constant, never derived : {ok6}")
    ok &= ok6
    # BASIS-CONVERSION pins (2026-09-06): run() must score ROI wagers from
    # the conversion epoch under the new lock key; regression turns RED.
    import inspect as _i
    rsrc = _i.getsource(run)
    ok7 = ("wager_rois" in rsrc and "roi_e_value" in rsrc
           and "roi_lcb" in rsrc and "ROI_LOCK_KEY" in rsrc
           and "band_market_edges(" not in rsrc
           and ROI_EPOCH == datetime(2026, 9, 6, 22, 30, 0,
                                     tzinfo=timezone.utc).timestamp()
           and ROI_FUTILITY_DAYS == 7.0)
    print(f"  [basis] ROI wagers + conversion epoch + new lock key pinned "
          f": {ok7}")
    ok &= ok7
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
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
                            "band_lock.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else asyncio.run(run(a)))
