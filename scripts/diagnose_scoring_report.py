#!/usr/bin/env python3
"""Why did the scoring run admit N traders? Post-mortem on a report JSON.

Reads the latest scoring_reports/mirror_scoring_*.json (or the path passed as
argv[1]) and separates the possible causes of a zero/low admission count:

  1. p-FLOOR x BH DISCRETENESS: the wild bootstrap's minimum p is
     1/(1+N_BOOT) (~0.001 at 999). BH step-up over a pool of size m admits at
     rank k only if p_(k) <= q*k/m — so with everyone floored at 0.001,
     admission needs >= ceil(m * 0.001 / q) traders TIED AT THE FLOOR. A pool
     of 1000+ can mathematically refuse to admit 20 genuinely-stellar traders.
     This is a test-resolution artifact, NOT evidence of no skill.
  2. FAIL-CLOSED HOLDOUTS: p_holdout=1.0 whenever a trader's train or test
     half is empty or train_edge <= 0. If most of the universe resolves on one
     side of the cutoff, the split starves and p=1 dominates — a data-window
     artifact, also not evidence of no skill.
  3. GENUINE NULL: healthy pool, real p spread, still nothing survives — the
     skeptical prior was right; whale wallets show no persistent skill.

Read-only; stdlib only; no DB. Non-finite floats arrive as strings ("nan",
"-inf") from the runner's _json_safe — handled.

VPS:
  sudo -u polymarket /opt/polymarket-ai-v2/venv/bin/python \
      scripts/diagnose_scoring_report.py [report.json]
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys


def _f(x) -> float:
    """Float-or-nan for _json_safe's stringified non-finites."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def main() -> int:
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        candidates = sorted(glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scoring_reports", "mirror_scoring_*.json")))
        if not candidates:
            candidates = sorted(glob.glob("scoring_reports/mirror_scoring_*.json"))
        if not candidates:
            print("no scoring_reports/mirror_scoring_*.json found", file=sys.stderr)
            return 2
        path = candidates[-1]

    r = json.load(open(path))
    scores = r.get("scores", [])
    cfg = r.get("config", {})
    q = _f(cfg.get("BH_Q", 0.10))
    n_boot = int(cfg.get("N_BOOT", 999) or 999)
    min_adverse = int(cfg.get("MIN_ADVERSE_EVENTS", 2) or 2)
    p_floor = 1.0 / (1.0 + n_boot)

    print(f"report: {path}")
    print(f"stage={r.get('stage')} generated={r.get('generated_at')} "
          f"n_scored={r.get('n_scored')} n_admitted={r.get('n_admitted')}")
    if "validation" in r:
        v = r["validation"]
        print(f"validation: passed={v.get('passed')} detail={v.get('detail')!r}")
    print()

    # ── the BH pool (mirrors q_score.select_and_size exactly) ────────────────
    pool = [t for t in scores
            if int(t.get("n_adverse", 0)) >= min_adverse
            and math.isfinite(_f(t.get("edge_se"))) and _f(t.get("edge_se")) > 1e-12]
    m = len(pool)
    print(f"scored={len(scores)}  BH pool={m} "
          f"(excluded: {len(scores) - m} via n_adverse<{min_adverse} or degenerate se)")

    # ── cause 2: fail-closed holdouts ────────────────────────────────────────
    def _is_nan(x):
        return not math.isfinite(_f(x))
    no_train = sum(1 for t in scores if _is_nan(t.get("train_edge")))
    no_test = sum(1 for t in scores if _is_nan(t.get("test_edge")))
    neg_train = sum(1 for t in scores
                    if math.isfinite(_f(t.get("train_edge"))) and _f(t.get("train_edge")) <= 0)
    p_one = sum(1 for t in pool if _f(t.get("p_holdout")) >= 1.0)
    print(f"\nfail-closed holdouts (p forced to 1.0):")
    print(f"  train half empty: {no_train}   test half empty: {no_test}   "
          f"train_edge<=0: {neg_train}")
    print(f"  pool members at p=1.0: {p_one} ({100.0*p_one/m:.1f}% of pool)" if m else "")

    # ── p distribution in the pool ───────────────────────────────────────────
    ps = sorted(_f(t.get("p_holdout")) for t in pool)
    if ps:
        at_floor = sum(1 for x in ps if x <= p_floor + 1e-12)
        print(f"\np_holdout distribution (pool):")
        print(f"  min 5: {[round(x, 4) for x in ps[:5]]}")
        for thr in (p_floor, 0.005, 0.01, 0.05, 0.10):
            print(f"  p <= {thr:.4f}: {sum(1 for x in ps if x <= thr)}")
        print(f"  at the bootstrap floor ({p_floor:.4f}): {at_floor}")

        # ── cause 1: the discreteness arithmetic ─────────────────────────────
        need = math.ceil(m * p_floor / q) if q > 0 else 0
        print(f"\nBH discreteness check (N_BOOT={n_boot} -> p floor {p_floor:.4f}, "
              f"q={q}, pool m={m}):")
        print(f"  admission requires >= {need} traders tied at the floor "
              f"(have {at_floor})")
        if at_floor > 0 and at_floor < need:
            print("  => TEST-RESOLUTION ARTIFACT LIKELY: strong traders exist but "
                  "cannot clear BH at this N_BOOT/pool size. Raise N_BOOT "
                  "(e.g. 9999) and rerun before concluding no-skill.")
        elif at_floor == 0 and p_one >= 0.9 * m:
            print("  => HOLDOUT STARVATION LIKELY: nearly all p=1.0 fail-closed — "
                  "check the cutoff leaves both halves populated.")
        elif at_floor == 0:
            print("  => GENUINE NULL PLAUSIBLE: real p spread, nobody near the "
                  "floor — consistent with no persistent whale skill.")

    # ── top of the ranking for eyeballing ────────────────────────────────────
    top = sorted(pool, key=lambda t: _f(t.get("p_holdout")))[:10]
    print("\ntop 10 pool traders by p_holdout:")
    for t in top:
        print(f"  p={_f(t.get('p_holdout')):.4f} events={t.get('n_events')} "
              f"adverse={t.get('n_adverse')} edge={_f(t.get('edge_mean')):+.4f} "
              f"train={_f(t.get('train_edge')):+.4f} test={_f(t.get('test_edge')):+.4f} "
              f"{str(t.get('trader'))[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
