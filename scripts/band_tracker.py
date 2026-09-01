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
import shadow_readout as sr  # noqa: E402

EPOCH = datetime(2026, 8, 19, 18, 0, 0, tzinfo=timezone.utc).timestamp()
BAND_LO, BAND_HI = 0.65, 0.85
LAMBDAS = (0.05, 0.1, 0.2, 0.4, 0.6, 0.8)
E_REJECT = 20.0
ECON_FLOOR = 0.02
N_FUT = 600
Y_MIN = -1.02  # per-market edge lower bound (fee-inclusive)


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
    if "band_0.65_0.85" in locks:
        lk = locks["band_0.65_0.85"]
        print(f"[band] VERDICT LOCKED {lk['locked_at']}: {lk['verdict']} "
              f"(e={lk.get('p')}, n={lk.get('resolved')})")
        return 0
    seq = band_market_edges(recs, outcomes, frm, fee_map)
    n = len(seq)
    if n == 0:
        print(f"[band] {stamp} forward window open, 0 resolved band markets "
              f"yet (epoch 2026-08-19T18:00Z) - accruing")
        return 0
    edges = [e for _, e in seq]
    ev = e_value(edges)
    pooled = sum(edges) / n
    print(f"[band] {stamp} n={n} resolved band mkts | pooled edge "
          f"{pooled:+.4f} | e-value {ev:.3f} (reject at {E_REJECT:.0f}) | "
          f"futility at {N_FUT}")
    verdict = None
    if ev >= E_REJECT:
        econ = pooled >= ECON_FLOOR
        verdict = ("PASS - edge>0 PROVEN + economic floor met" if econ else
                   f"SIGNIFICANT but BELOW ECON FLOOR ({pooled:+.4f} < "
                   f"+{ECON_FLOOR:.02f})")
    elif n >= N_FUT:
        verdict = "NOT DEMONSTRATED (futility bound reached)"
    if verdict:
        sr.write_lock(args.locks, locks, "band_0.65_0.85", {
            "locked_at": stamp, "resolved": n, "edge": pooled,
            "p": ev, "verdict": verdict,
            "source": "band_tracker e-process first crossing"})
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
