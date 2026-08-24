#!/usr/bin/env python3
"""Cohort5 qualification tracker — forward copy-edge vs the APPROVED bars.

Criteria (docs/COHORT5_PREREGISTRATION.md, operator-ratified 2026-07-30):
eligible = chain-screen ADMIT (complete labels); qualify when the FORWARD
window (detect_ts >= 2026-07-30T17:00:00Z) shows copy edge >= +0.02 on >= 30
resolved markets with per-trader P(>0) >= 0.95 (SINGLE look — the trader's
test is consumed at first crossing of 30, verdict-locked) and OK-rate >= 0.75.
Concentration (<= 50% of projected cohort flow) is checked at COMPOSITION.

This tracker is read-only vs trading state; it appends nothing to the daily
readout. It writes ONLY its own per-trader verdict locks (same immutable-lock
helpers as the cohort stopping rule). Qualifying traders are PROPOSALS —
composition is a separate operator go.

    DATABASE_URL=... PYTHONPATH=<mb_readout> python scripts/cohort5_qualification.py
    ... --self-test   # offline: window filter + bar logic + lock reuse
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

# The approved forward-window epoch — 2026-07-30T17:00:00Z, fixed by the
# ratified pre-registration. NEVER move it: records before it were visible
# when the criteria were designed, so counting them would select and verify
# on the same data.
QUAL_EPOCH = datetime(2026, 7, 30, 17, 0, 0,
                      tzinfo=timezone.utc).timestamp()

# COHORT1-UNTESTED group (operator go 2026-08-24: "do it if they pass the
# test"): the 9 cohort1 CLEAN traders never chain-ADMITted and so never
# eligible above. Same bars, same single-look locks file, but their OWN
# forward epoch - 2026-08-24T17:00:00Z (records before it were visible when
# this extension was decided; counting them would select and verify on the
# same data). Passing = a PROPOSAL, exactly like cohort5.
C1_FWD_EPOCH = datetime(2026, 8, 24, 17, 0, 0,
                        tzinfo=timezone.utc).timestamp()
C1_UNTESTED = [
    "0x000d257d2dc7616feaef4ae0f14600fdf50a758e",
    "0x14964aefa2cd7caff7878b3820a690a03c5aa429",
    "0x32cf8efc13583788ed0bbaeb4dccaccaa846b8d3",
    "0x7fb7ad0d194d7123e711e7db6c9d418fac14e33d",
    "0x9b979a065641e8cfde3022a30ed2d9415cf55e12",
    "0x9c16127eccf031df45461ef1e04b52ea286a09cb",
    "0xa9b44dca52ed35e59ac2a6f49d1203b8155464ed",
    "0xafbacaeeda63f31202759eff7f8126e49adfe61b",
    "0xecdbd79566a25693b9971c48d7de84bc05f7da79",
]

EDGE_BAR = 0.02
P_BAR = 0.95
N_BAR = 30
OKRATE_BAR = 0.75


def eligible_admits(deep_dive_dir: str, rereview_dir: str) -> list[str]:
    """Chain-screen ADMITs on complete labels: the re-review out-dir is the
    authority (20/20 graded on supplemented labels); the base dir fills in
    any ADMIT the re-review did not cover. Empty result is a hard error."""
    import glob
    out: set[str] = set()
    for d in (rereview_dir, deep_dive_dir):
        for f in glob.glob(os.path.join(d, "0x*.json")):
            try:
                blob = json.load(open(f))
            except (OSError, ValueError):
                continue
            if str(blob.get("verdict", "")).startswith("ADMIT"):
                out.add(str(blob.get("address", "")).lower())
    if not out:
        raise ValueError("0 chain-ADMITs found — eligibility set empty; "
                         "refusing to report 'no candidates' on missing input")
    return sorted(out)


def forward_records(recs: list[dict], epoch: float) -> list[dict]:
    """The REAL detect_ts cutoff (trust_after is not a time filter)."""
    return [r for r in recs if float(r.get("detect_ts") or 0) >= epoch]


def bar_status(res: dict) -> tuple[bool, str]:
    """(qualifies_now, human status) vs the approved bars. Only meaningful at
    the single look (first crossing of N_BAR) — the caller enforces that."""
    n = res.get("resolved_mkts") or 0
    edge = res.get("shadow_edge")
    p = res.get("shadow_edge_p")
    okr = res.get("ok_rate")
    if n < N_BAR:
        return False, f"ACCRUING ({n}/{N_BAR} resolved)"
    parts = []
    edge_ok = isinstance(edge, float) and edge == edge and edge >= EDGE_BAR
    p_ok = isinstance(p, float) and p == p and p >= P_BAR
    ok_ok = isinstance(okr, float) and okr == okr and okr >= OKRATE_BAR
    parts.append(f"edge {'PASS' if edge_ok else 'FAIL'} ({edge:+.4f} vs +{EDGE_BAR:.02f})")
    parts.append(f"P {'PASS' if p_ok else 'FAIL'} ({p:.3f} vs {P_BAR:.02f})")
    parts.append(f"OK-rate {'PASS' if ok_ok else 'FAIL'} ({okr:.2f} vs {OKRATE_BAR:.02f})")
    return (edge_ok and p_ok and ok_ok), "; ".join(parts)


async def run(args) -> int:
    from types import SimpleNamespace as NS
    cands = eligible_admits(args.deep_dive, args.rereview)
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    fwd = forward_records(recs, QUAL_EPOCH)
    tokens = sorted({str(r["token_id"]) for r in fwd if r.get("token_id")})
    print(f"cohort5 qualification — window since "
          f"{datetime.fromtimestamp(QUAL_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ} | "
          f"eligible chain-ADMITs: {len(cands)} | forward records: {len(fwd)}")
    if not tokens:
        print("no forward tokens yet — window just opened; nothing to grade "
              "(NOT a failure; re-run after fills accrue)")
        return 0
    db = await sr.fresh_outcomes(tokens)
    supp = sr.supplement_outcomes(args.supplement, tokens) if tokens else {}
    outcomes = sr.merge_outcomes(db, supp)
    fee_map = None
    if args.fee_map and os.path.exists(args.fee_map):
        fee_map = json.load(open(args.fee_map))
    cfg = NS(max_chase=0.02, max_spread=0.05, fee=0.02, econ_floor=EDGE_BAR,
             p_min=P_BAR, min_markets=N_BAR, fee_map_data=fee_map)
    locks = sr.load_locks(args.locks)
    proposals = []

    def grade(group, group_fwd, epoch, lock_source):
        nonlocal locks
        for a in group:
            res = sr.cohort_readout(group_fwd, outcomes, epoch, a, cfg)
            n = res.get("resolved_mkts") or 0
            if a in locks:
                lk = locks[a]
                print(f"  {a[:12]}..  LOCKED {lk['locked_at']}: {lk['verdict']} "
                      f"(single look consumed)")
                continue
            qual, status = bar_status(res)
            marker = ""
            if n >= N_BAR:
                verdict = "QUALIFIES" if qual else "DOES NOT QUALIFY"
                locks = sr.write_lock(args.locks, locks, a, {
                    "locked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                    "resolved": n, "edge": res.get("shadow_edge"),
                    "p": res.get("shadow_edge_p"), "verdict": verdict,
                    "source": lock_source})
                marker = f"  <== {verdict} [LOCKED THIS RUN]"
                if qual:
                    proposals.append(a)
            print(f"  {a[:12]}..  {status}{marker}")

    grade(cands, fwd, QUAL_EPOCH, "cohort5_qualification single-look")
    c1fwd = forward_records(recs, C1_FWD_EPOCH)
    print(f"cohort1-untested ({len(C1_UNTESTED)}) - window since "
          f"{datetime.fromtimestamp(C1_FWD_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ} | "
          f"forward records: {len(c1fwd)}")
    grade(C1_UNTESTED, c1fwd, C1_FWD_EPOCH, "cohort1_untested single-look")
    if proposals:
        print(chr(10) + "PROPOSALS (operator go required for composition): "
              + ", ".join(a[:12] + ".." for a in proposals))
    return 0


def _self_test() -> int:
    print("SELF-TEST — cohort5_qualification (offline)\n")
    ok = True
    e = QUAL_EPOCH
    recs = [{"detect_ts": e - 1, "token_id": "old"},
            {"detect_ts": e, "token_id": "edge"},
            {"detect_ts": e + 1, "token_id": "new"},
            {"token_id": "no_ts"}]
    fwd = forward_records(recs, e)
    ok1 = [r["token_id"] for r in fwd] == ["edge", "new"]
    print(f"  [window] pre-epoch + missing detect_ts excluded : {ok1}")
    ok &= ok1
    mk = lambda n, edge, p, okr: {"resolved_mkts": n, "shadow_edge": edge,
                                  "shadow_edge_p": p, "ok_rate": okr}
    q, s = bar_status(mk(29, 0.05, 0.99, 0.9))
    ok2 = (not q) and "ACCRUING (29/30" in s
    q3, _ = bar_status(mk(30, 0.021, 0.96, 0.80))
    q4, _ = bar_status(mk(30, 0.021, 0.94, 0.80))   # P fails
    q5, _ = bar_status(mk(30, 0.019, 0.99, 0.80))   # edge fails
    q6, _ = bar_status(mk(30, 0.021, 0.96, 0.70))   # OK-rate fails
    ok2 = ok2 and q3 and not q4 and not q5 and not q6
    print(f"  [bars] all three must pass, underpowered never passes : {ok2}")
    ok &= ok2
    ok3 = (QUAL_EPOCH == datetime(2026, 7, 30, 17, 0, 0,
                                  tzinfo=timezone.utc).timestamp())
    print(f"  [epoch] fixed at 2026-07-30T17:00:00Z, never derived : {ok3}")
    ok &= ok3
    ok3b = (C1_FWD_EPOCH == datetime(2026, 8, 24, 17, 0, 0,
                                     tzinfo=timezone.utc).timestamp()
            and C1_FWD_EPOCH > QUAL_EPOCH)
    print(f"  [epoch2] cohort1-untested fixed at 2026-08-24T17:00:00Z : {ok3b}")
    ok &= ok3b
    ok3c = (len(C1_UNTESTED) == 9 and len(set(C1_UNTESTED)) == 9
            and all(a == a.lower() and a.startswith("0x") and len(a) == 42
                    for a in C1_UNTESTED))
    print(f"  [group] 9 unique lowercase full addresses : {ok3c}")
    ok &= ok3c
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        try:
            eligible_admits(d, d)
            ok4 = False
        except ValueError:
            ok4 = True
        print(f"  [guard] empty eligibility raises, never 'no candidates' : {ok4}")
        ok &= ok4
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="cohort5 forward qualification "
                                             "tracker (single-look, locked)")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
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
    ap.add_argument("--locks",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "cohort5_qual_locks.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else asyncio.run(run(a)))
