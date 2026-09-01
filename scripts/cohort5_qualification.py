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
import band_tracker as bt  # noqa: E402  (anytime-valid e-process, C1 group)
import mb_canon as mc  # noqa: E402  (canonical estimand, 2026-08-25)
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

# C1 AMENDMENT (2026-08-25, operator: "proceed with your rec"; registered
# BEFORE any C1 look was consumed - only count-only ACCRUING 0/30 lines had
# printed): the C1 group is graded ANYTIME-VALID (docs/
# COHORT1_UNTESTED_AMENDMENT.md), mirroring the band design, because the
# single-look bar has 7-8% one-shot power at realistic edges (08-19 study).
# QUALIFY: e >= 20 AND pooled canon edge >= +0.02 AND OK-rate >= 0.75.
# FUTILITY: 300 resolved markets with e < 20 -> NOT DEMONSTRATED.
# Fees: VENUE FORMULA via fee_rate_map (canon; post-08-19 registration rule,
# analyze_shadow fee precedence). The ORIGINAL 20 keep their 07-30 charter
# scoring (flat 2%, single look) untouched - divergence disclosed per run.
C1_E_REJECT = 20.0
C1_FUTILITY_N = 300

# RE-REGISTRATION (2026-08-25, operator: "go with rec 4"): the 15 unconsumed
# ORIGINAL-cohort5 looks move to the SAME anytime-valid e-process. Their
# per-trader diagnostics were visible in daily readouts, so they get a FRESH
# forward epoch (below) rather than keeping 07-30 - stricter than required,
# immune to the peeking objection. The 5 consumed locks stay locked forever.
# Fees: canon venue formula (the new registration supersedes flat-2%).
REREG_EPOCH = datetime(2026, 8, 25, 18, 0, 0,
                       tzinfo=timezone.utc).timestamp()

# INSUFFICIENT-PROBES (2026-08-25, operator: "go with rec 5"): the 12
# never-regraded INSUFFICIENT-EVIDENCE traders join as observation-only
# probes (roster 31->43); forward EV is the only way to apply the operator's
# "positive EV -> add, negative -> remove" rule. Same e-process, same fresh
# epoch. Chain screen NOT re-run - these are probes, not admits; QUALIFIES
# here is a PROPOSAL that would ALSO need the fraud screen before live copy.
INSUFF_PROBES = [
    "0x48185887c8dc95de60ee89722f1d0ee7894cbf0b",
    "0x92672c80d36dcd08172aa1e51dface0f20b70f9a",
    "0x9cb990f1862568a63d8601efeebe0304225c32f2",
    "0xa8c63f775ddbbe66b56614191747def3021444e8",
    "0xc257ea7e3a81ca8e16df8935d44d513959fa358e",
    "0xcd9bc2939f0dac121f6ccde59cca5e0b6a91414d",
    "0xe40ea00e74059c76c0035c919ef6b99c3e25a94d",
    "0xea8ee311382139d952087a669252252625663de0",
    "0xed107a85a4585a381e48c7f7ca4144909e7dd2e5",
    "0xed88d69d689f3e2f6d1f77b2e35d089c581df3c4",
    "0xf5198df69e13937a40d1c76d6f72d9aa067d906b",
    "0xfbf3d501e88815464642d0e913f15379c3eeb218",
]

# SWEEP2-ADMITS (2026-08-30, operator "go with recs 1-5" rec 1): the 16
# chain-ADMITs from scout sweep #2 (the first human-scale admits; filter
# fixed 08-24) join the sluice as watch trials. ADMIT = integrity screen
# passed, NOT profitability - the e-process decides that. Fresh epoch
# 2026-08-30T20:30:00Z (post-decision; no forward edge of theirs was ever
# computed before this registration).
SWEEP2_EPOCH = datetime(2026, 8, 30, 20, 30, 0,
                        tzinfo=timezone.utc).timestamp()
SWEEP2_ADMITS = [
    "0x0063b23cdeb43166d6c0246c05baaf9b9bd72dd2",
    "0x122b758a408246a180efeb5ba654e21b553fac59",
    "0x30b9c9d6670c66936550e3af670c12b90db7214c",
    "0x35bbbad2415fe5e39b12da9a316cdc80b022009b",
    "0x3e73934b881659aa25a4f08bc8ab9067295bc4ec",
    "0x401ee31e9ebf9ab9f6315cd95faca5f950436fc9",
    "0x5e04e12c3376a6a68f8cdffc8b972df3bd9e08a2",
    "0x60a92c8620846d81f5ea17b0564e0d4b7c545a71",
    "0x6918ea182d963b1fb7888860b8a8b8bcfba5782b",
    "0x6e2c3937e6dd094a3a9f814ecbdc289d3fd5b7f8",
    "0x731a241767938bb23d1b2fac4c9cd2f3cea9033f",
    "0x91667e40b80c447050904b042f3b85d22fc6b479",
    "0xa7614974faca5be9a1b809c978d0a8fc532a866b",
    "0xc96aeabae8c81faf8d803201da1d2461cefc396a",
    "0xd703c88c0b726ae01ee9602a422013ca8d4171bf",
    "0xdf804b17329a461425116c9e0f599e248b443259",
]

# CRACK-ADMITS (2026-08-30, operator "2 ok" on the crack proposals): the 10
# reviewed-but-never-tracked addresses found by the crack census - latest
# verdict INSUFFICIENT-EVIDENCE (9 in deep_dive + 1 in deep_dive_scout),
# neither on roster nor locked. INSUFFICIENT under the voided metrics is a
# process crack, not evidence against them. REJECTs are deliberate
# exclusions and stay out. Fresh post-EXECUTION epoch (ruling 08-30,
# executed 09-01; epoch 2026-09-01T13:00Z, never back-dated).
CRACK_EPOCH = datetime(2026, 9, 1, 13, 0, 0,
                       tzinfo=timezone.utc).timestamp()
CRACK_ADMITS = [
    "0x0c0e270cf879583d6a0142fc817e05b768d0434e",
    "0x44c1dfe43260c94ed4f1d00de2e1f80fb113ebc1",
    "0x70d94a4ff67ed919a8480885cf0808afefe7a684",
    "0xa16a1302ca05463f30faebeb5c045767fde233a1",
    "0xabb89972b21b304c1bed2bf26f35c8741ac9bba3",
    "0xcc500cbcc8b7cf5bd21975ebbea34f21b5644c82",
    "0xd189664c5308903476f9f079820431e4fd7d06f4",
    "0xdbade4c82fb72780a0db9a38f821d8671aba9c95",
    "0xdf17f4a8dd01a4cfa6fc3da323a2baee5f8697d1",
    "0xe613b515bd46b1585a8b137a4d291d9b80bd540e",
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
    # VETO fix (2026-08-25, operator-approved): the re-review dir is the
    # AUTHORITY - its verdict (ADMIT or not) overrides the base dir for any
    # address it covers. The old union let a stale base-dir ADMIT survive a
    # re-review REJECT (re-grading on complete labels is the re-review's
    # whole purpose). A corrupt verdict file is now LOUD, not skipped.
    verdicts: dict = {}
    for d in (deep_dive_dir, rereview_dir):        # rereview LAST = wins
        for f in glob.glob(os.path.join(d, "0x*.json")):
            try:
                blob = json.load(open(f))
            except (OSError, ValueError) as e:
                print(f"  [eligibility] WARNING unreadable verdict file "
                      f"{os.path.basename(f)}: {e!r} - NOT silently skipped")
                continue
            a = str(blob.get("address", "")).lower()
            if a:
                verdicts[a] = str(blob.get("verdict", ""))
    out = {a for a, v in verdicts.items() if v.startswith("ADMIT")}
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


def write_heartbeat(path: str, groups_graded: int, locks_written: int) -> None:
    """Monitoring channel ONLY (2026-09-01 grader alarm, operator 'build it').
    Written at the END of a clean run() — a crash leaves no fresh heartbeat,
    so the scoreboard [grader] line goes STALE (fail-toward-alarm; born from
    the frm NameError that killed 7/7 daily runs 08-26..09-01 unnoticed).
    A write failure must NOT fail the grading run: grading and locks are
    already committed, and swallowing here degrades to the same STALE alarm,
    which is the safe direction."""
    try:
        rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "groups_graded": groups_graded,
               "locks_written": locks_written}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"  [heartbeat] WARN: could not write {path}: {e!r} - the "
              f"scoreboard [grader] line will go STALE (alarm direction)")


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
        write_heartbeat(args.heartbeat, 0, 0)
        return 0
    db = await sr.fresh_outcomes(tokens)
    supp = sr.supplement_outcomes(args.supplement, tokens) if tokens else {}
    outcomes = sr.merge_outcomes(db, supp)
    fee_map = None
    if args.fee_map and os.path.exists(args.fee_map):
        try:
            fee_map = json.load(open(args.fee_map))
        except ValueError as e:
            raise SystemExit(f"FATAL: fee_map corrupt ({e!r}) - refusing to "
                             f"grade under a silently-changed fee equation")
        if not isinstance(fee_map, dict) or not fee_map:
            raise SystemExit("FATAL: fee_map empty/malformed - refusing")
    # fee RATE map (per-token taker rate) — same canon input the funnel
    # passes to mc.per_market_edges; was never bound here (NameError on
    # every eproc_grade run 08-26..09-01, fixed 2026-09-01)
    frm = None
    if args.fee_rate_map and os.path.exists(args.fee_rate_map):
        try:
            frm = json.load(open(args.fee_rate_map))
        except ValueError as e:
            raise SystemExit(f"FATAL: fee_rate_map corrupt ({e!r}) - refusing "
                             f"to grade under a silently-changed fee equation")
        if not isinstance(frm, dict) or not frm:
            raise SystemExit("FATAL: fee_rate_map empty/malformed - refusing")
    cfg = NS(max_chase=0.02, max_spread=0.05, fee=0.02, econ_floor=EDGE_BAR,
             p_min=P_BAR, min_markets=N_BAR, fee_map_data=fee_map)
    locks = sr.load_locks(args.locks)
    n_locks_start = len(locks)
    graded_groups = 0
    proposals = []

    def eproc_grade(group, epoch, lock_source):
        nonlocal locks, graded_groups
        graded_groups += 1
        gfwd = forward_records(recs, epoch)
        for a in group:
            if a in locks:
                lk = locks[a]
                print(f"  {a[:12]}..  LOCKED {lk['locked_at']}: "
                      f"{lk['verdict']} (consumed)")
                continue
            t_recs = [r for r in gfwd
                      if str(r.get("trader", "")).lower() == a]
            seq = mc.per_market_edges(t_recs, outcomes, frm or {},
                                      fee_map or {}, epoch=epoch)
            edges = [e for _, _, e in seq]
            n = len(edges)
            res = sr.cohort_readout(gfwd, outcomes, epoch, a, cfg)
            okr = res.get("ok_rate")
            if n == 0:
                print(f"  {a[:12]}..  ACCRUING (0 resolved, e=n/a)")
                continue
            ev = bt.e_value(edges)
            pooled = mc.pooled_edge(seq)
            line = (f"  {a[:12]}..  n={n} e={ev:.3f} pooled={pooled:+.4f} "
                    f"ok_rate={okr if okr is None else round(okr, 2)}")
            if ev >= C1_E_REJECT:
                econ_ok = pooled is not None and pooled >= EDGE_BAR
                ok_ok = isinstance(okr, float) and okr >= OKRATE_BAR
                verdict = ("QUALIFIES" if (econ_ok and ok_ok)
                           else "E-PASS BUT GATE FAIL "
                                f"(econ_ok={econ_ok} ok_rate_ok={ok_ok})")
                locks = sr.write_lock(args.locks, locks, a, {
                    "locked_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%MZ"),
                    "resolved": n, "edge": pooled, "p": ev,
                    "verdict": verdict, "source": lock_source})
                print(line + f"  <== {verdict} [LOCKED THIS RUN]")
                if verdict == "QUALIFIES":
                    proposals.append(a)
            elif n >= C1_FUTILITY_N:
                locks = sr.write_lock(args.locks, locks, a, {
                    "locked_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%MZ"),
                    "resolved": n, "edge": pooled, "p": ev,
                    "verdict": "NOT DEMONSTRATED (futility)",
                    "source": lock_source})
                print(line + "  <== NOT DEMONSTRATED (futility) [LOCKED]")
            else:
                print(line + "  ACCRUING")

    print(f"  [amendment 2026-08-25] ALL unconsumed looks are ANYTIME-VALID "
          f"e-process (reject e>={C1_E_REJECT:.0f}, futility {C1_FUTILITY_N},"
          f" canon venue fees); the 5 consumed single-looks stay locked")
    print(f"original-20 unconsumed - re-registered epoch "
          f"{datetime.fromtimestamp(REREG_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ}"
          f" (fresh: prior diagnostics were visible):")
    eproc_grade(cands, REREG_EPOCH,
                "cohort5 re-registered e-process (2026-08-25)")
    print(f"cohort1-untested ({len(C1_UNTESTED)}) - epoch "
          f"{datetime.fromtimestamp(C1_FWD_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ}:")
    eproc_grade(C1_UNTESTED, C1_FWD_EPOCH,
                "cohort1_untested e-process (amendment 2026-08-25)")
    print(f"insufficient-probes ({len(INSUFF_PROBES)}) - epoch "
          f"{datetime.fromtimestamp(REREG_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ}"
          f" (observation-only; QUALIFIES = proposal + fraud screen still "
          f"required):")
    eproc_grade(INSUFF_PROBES, REREG_EPOCH,
                "insufficient_probe e-process (2026-08-25)")
    print(f"sweep2-admits ({len(SWEEP2_ADMITS)}) - epoch "
          f"{datetime.fromtimestamp(SWEEP2_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ}"
          f" (integrity-screened; profit undecided - the e-process rules):")
    eproc_grade(SWEEP2_ADMITS, SWEEP2_EPOCH,
                "sweep2_admit e-process (2026-08-30)")
    print(f"crack-admits ({len(CRACK_ADMITS)}) - epoch "
          f"{datetime.fromtimestamp(CRACK_EPOCH, timezone.utc):%Y-%m-%dT%H:%MZ}"
          f" (census re-admits; INSUFFICIENT under voided metrics = process "
          f"crack, not evidence):")
    eproc_grade(CRACK_ADMITS, CRACK_EPOCH,
                "crack_admit e-process (2026-08-30)")
    if proposals:
        print(chr(10) + "PROPOSALS (operator go required for composition): "
              + ", ".join(a[:12] + ".." for a in proposals))
    write_heartbeat(args.heartbeat, graded_groups, len(locks) - n_locks_start)
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
    ok3d = (REREG_EPOCH == datetime(2026, 8, 25, 18, 0, 0,
                                    tzinfo=timezone.utc).timestamp()
            and REREG_EPOCH > C1_FWD_EPOCH)
    print(f"  [epoch3] re-registration fixed at 2026-08-25T18:00:00Z : {ok3d}")
    ok &= ok3d
    ok3f = (SWEEP2_EPOCH == datetime(2026, 8, 30, 20, 30, 0,
                                     tzinfo=timezone.utc).timestamp()
            and len(SWEEP2_ADMITS) == 16 and len(set(SWEEP2_ADMITS)) == 16
            and all(a == a.lower() and a.startswith("0x") and len(a) == 42
                    for a in SWEEP2_ADMITS)
            and not (set(SWEEP2_ADMITS) & (set(C1_UNTESTED) | set(INSUFF_PROBES))))
    print(f"  [group3] 16 unique sweep2 addresses, disjoint : {ok3f}")
    ok3g = (CRACK_EPOCH == datetime(2026, 9, 1, 13, 0, 0,
                                    tzinfo=timezone.utc).timestamp()
            and len(CRACK_ADMITS) == 10 and len(set(CRACK_ADMITS)) == 10
            and all(a == a.lower() and a.startswith("0x") and len(a) == 42
                    for a in CRACK_ADMITS)
            and not (set(CRACK_ADMITS) & (set(C1_UNTESTED)
                                          | set(INSUFF_PROBES)
                                          | set(SWEEP2_ADMITS))))
    print(f"  [group3] 10 unique crack addresses, disjoint : {ok3g}")
    ok &= ok3f
    ok &= ok3g
    ok3e = (len(INSUFF_PROBES) == 12 and len(set(INSUFF_PROBES)) == 12
            and all(a == a.lower() and a.startswith("0x") and len(a) == 42
                    for a in INSUFF_PROBES)
            and not (set(INSUFF_PROBES) & set(C1_UNTESTED)))
    print(f"  [group2] 12 unique probe addresses, disjoint from C1 : {ok3e}")
    ok &= ok3e
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
    # regression (2026-09-01): frm must be BOUND in run() — unbound, the
    # eproc_grade closure compiles it as a global load and every grading
    # run dies with NameError (fired daily 08-26..09-01, unnoticed)
    ok5 = "frm" in (run.__code__.co_varnames + run.__code__.co_cellvars)
    print(f"  [names] fee-rate map 'frm' bound in run() grading path : {ok5}")
    ok &= ok5
    # grader heartbeat (2026-09-01 alarm build, operator 'build it')
    with tempfile.TemporaryDirectory() as d:
        hbp = os.path.join(d, "hb.json")
        write_heartbeat(hbp, 5, 2)
        try:
            hb = json.load(open(hbp))
        except (ValueError, OSError):
            hb = {}
        ok6 = (set(hb) == {"ts", "groups_graded", "locks_written"}
               and hb.get("groups_graded") == 5 and hb.get("locks_written") == 2
               and not os.path.exists(hbp + ".tmp"))
        try:
            datetime.strptime(hb.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            ok6 = False
    print(f"  [heartbeat] atomic write, full schema, scoreboard-parseable ts"
          f" : {ok6}")
    ok &= ok6
    import inspect
    ok7 = ("write_heartbeat" in run.__code__.co_names
           and inspect.getsource(run).count("write_heartbeat(") == 2)
    print(f"  [heartbeat] run() writes at BOTH clean exits (early no-tokens"
          f" + full grade) : {ok7}")
    ok &= ok7
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
    ap.add_argument("--fee-rate-map", dest="fee_rate_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_rate_map.json")
    ap.add_argument("--fee-map", dest="fee_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_map.json")
    ap.add_argument("--locks",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "cohort5_qual_locks.json")
    ap.add_argument("--heartbeat",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "cohort5_grader_heartbeat.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else asyncio.run(run(a)))
