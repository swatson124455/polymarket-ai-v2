#!/usr/bin/env python3
"""CADENCE-AND-REALISM REFUTATION of the drift proposal.  NEW FILE, edits nothing.

The proposal's load-bearing cadence claim (kalshi_drift_jump.py docstring + section 6
of the return):

    "TEMP markets don't trend through us -- they gap ... Nothing sampling at a
     2-minute cycle can have lead time on that."

That claim is built on WORST-SINGLE-MINUTE share (jump_share) computed on 1-minute
candles.  But our real decision grid is 2 minutes (CYCLE_S=120).  This script asks
the cadence question at OUR cadence, not the candle's:

  Q1  Resampled to the 2-min grid we actually observe, is the adverse move contained
      in ONE 2-min cycle (uncatchable -> proposal's claim holds), or does it develop
      over >=2 of our cycles (a cycle of warning existed)?

  Q2  What is the per-2-min-cycle adverse-move distribution in GAS (the profitable,
      RETAINED series)?  Any 2-min rate trigger must be priced against its FALSE
      POSITIVE rate on gas, not just its hit rate on temp.  If gas moves as much per
      cycle as temp does, no 2-min rate trigger can separate them -> confirms the
      proposal.  If gas is quiet per-cycle while temp lurches, the "uncatchable"
      framing is wrong and a cheap 2-min rate control was dismissed prematurely.

Reuses the proposal's OWN timeline/state construction (import) so any difference is
cadence, not methodology.
"""
import json
import os
import statistics as S

import kalshi_drift_detect as D
from kalshi_drift_labels import load_positions

CYCLE_S = 120  # our real cadence


def resample_2min(tl, win_start, win_end):
    """Take one timeline row per 2-min cycle within [win_start, win_end], exactly the
    grid decision_points() uses. Returns list of (ts, value, exitable)."""
    out = []
    nxt = win_start
    for r in tl:
        if r["ts"] < win_start or r["ts"] > win_end:
            continue
        if r["ts"] >= nxt:
            out.append((r["ts"], r["value"], r["exitable"]))
            nxt = r["ts"] + CYCLE_S
    return out


def cycle_analysis(grid):
    """Given the 2-min grid, return per-cycle adverse steps and the jump decomposition
    AT OUR CADENCE."""
    if len(grid) < 3:
        return None
    vals = [g[1] for g in grid]
    v0, vend = vals[0], vals[-1]
    total = v0 - vend
    steps = [vals[i - 1] - vals[i] for i in range(1, len(vals))]  # +ve = adverse
    worst = max(steps)
    worst_i = steps.index(worst)  # index into steps; the cycle FROM grid[worst_i]->[worst_i+1]
    # how much adverse move had ALREADY accumulated in the cycle BEFORE the killer cycle
    pre_cycle_move = steps[worst_i - 1] if worst_i - 1 >= 0 else None
    return {
        "n_cyc": len(grid), "v0": v0, "vend": vend, "total": total,
        "worst_cyc": worst, "worst_i": worst_i,
        "jump_share_2min": (worst / total) if total > 0.02 else None,
        "pre_killer_cycle_move": pre_cycle_move,
        "steps": steps,
    }


def main():
    cache = json.load(open(D.CACHE))
    positions = load_positions()
    states = {}
    for p in positions:
        st = D.make_state(p, cache, positions)
        if st:
            states[(p["ticker"], p["side"])] = st

    POS = [s for s in states.values() if s["p"]["label"] == "positive"]
    POS_R = [s for s in POS if s["p"]["vwap_entry"] > D.TAIL_CUT]
    TCTL = [s for s in states.values() if s["p"]["label"] == "temp_control"]
    GAS = [s for s in states.values()
           if s["p"]["family"] == "GAS" and s["p"]["label"] != "positive"]

    print("=" * 100)
    print("Q1  THE 6 RUN-OVERS AT OUR REAL 2-MINUTE CADENCE")
    print("    worst_cyc = largest adverse move in a single 2-min cycle")
    print("    jump_share_2min = worst_cyc / total adverse move  (1.0 = one cycle carried it all)")
    print("    pre_killer = adverse move in the cycle IMMEDIATELY BEFORE the killer cycle")
    print("               (this is the warning a 2-min rate trigger would have had)")
    print("=" * 100)
    hdr = f"{'contract':<30}{'ncyc':>5}{'total':>8}{'worst_cyc':>11}{'js_2min':>9}{'pre_killer':>12}"
    print(hdr); print("-" * len(hdr))
    js2 = []; pre = []; single_cycle = 0
    for st in sorted(POS_R, key=lambda s: s["p"]["pnl"]):
        g = resample_2min(st["tl"], st["win_start"], st["win_end"])
        ca = cycle_analysis(g)
        if not ca:
            continue
        if ca["jump_share_2min"] is not None:
            js2.append(ca["jump_share_2min"])
            if ca["jump_share_2min"] >= 0.80:
                single_cycle += 1
        pk = ca["pre_killer_cycle_move"]
        if pk is not None:
            pre.append(pk)
        pks = f"{pk:+.3f}" if pk is not None else "   --"
        jss = f"{ca['jump_share_2min']:.2f}" if ca["jump_share_2min"] is not None else "  --"
        print(f"{st['p']['ticker'][-28:]:<30}{ca['n_cyc']:>5}{ca['total']:>8.3f}"
              f"{ca['worst_cyc']:>11.3f}{jss:>9}{pks:>12}")
    print("-" * len(hdr))
    if js2:
        print(f"median jump_share_2min = {S.median(js2):.2f}   "
              f"(run-overs where ONE 2-min cycle carried >=80% of the loss: {single_cycle}/{len(js2)})")
    if pre:
        print(f"pre-killer-cycle adverse move: median {S.median(pre):+.3f}, "
              f"max {max(pre):+.3f}  -> warning a 2-min trigger had BEFORE the killer cycle")
        # how many had a MATERIAL (>=0.08, D1 threshold) warning cycle before the kill
        warned = sum(1 for x in pre if x >= 0.08)
        print(f"run-overs with a >=0.08 adverse warning cycle BEFORE the killer cycle: {warned}/{len(pre)}")

    print("\n" + "=" * 100)
    print("Q2  PER-2-MIN-CYCLE ADVERSE-MOVE DISTRIBUTION BY FAMILY")
    print("    A 2-min rate trigger fires when a single cycle's adverse move exceeds X.")
    print("    Report the cycle-level move distribution so we can price the FALSE POSITIVE")
    print("    rate on GAS (retained/profitable) at any threshold X.")
    print("=" * 100)

    def all_cycle_steps(group):
        steps = []
        for st in group:
            g = resample_2min(st["tl"], st["win_start"], st["win_end"])
            ca = cycle_analysis(g)
            if ca:
                steps.extend(ca["steps"])  # signed: +ve adverse
        return steps

    groups = {"RUNOVER(temp)": POS_R, "TEMP control": TCTL, "GAS(all)": GAS}
    dist = {}
    for nm, g in groups.items():
        st_ = all_cycle_steps(g)
        adv = [x for x in st_ if x > 0]
        dist[nm] = st_
        n = len(st_)
        p90 = S.quantiles(st_, n=10)[-1] if n >= 10 else max(st_)
        p95 = sorted(st_)[int(0.95 * (n - 1))] if n else 0
        pmax = max(st_) if st_ else 0
        frac_ge08 = sum(1 for x in st_ if x >= 0.08) / n if n else 0
        frac_ge15 = sum(1 for x in st_ if x >= 0.15) / n if n else 0
        print(f"{nm:<16} cycles={n:>4}  median_step={S.median(st_):+.4f}  "
              f"p90={p90:+.3f} p95={p95:+.3f} max={pmax:+.3f}  "
              f"frac>=0.08={frac_ge08:.3f} frac>=0.15={frac_ge15:.3f}")

    print("\n  FALSE-POSITIVE COST OF A 2-MIN RATE TRIGGER ON GAS:")
    gas = dist["GAS(all)"]; run = dist["RUNOVER(temp)"]
    for X in (0.05, 0.08, 0.12, 0.15, 0.20):
        gas_fp = sum(1 for x in gas if x >= X) / len(gas) if gas else 0
        run_hit = sum(1 for x in run if x >= X) / len(run) if run else 0
        print(f"    X={X:.2f}:  fires on {run_hit:.1%} of run-over cycles,  "
              f"and on {gas_fp:.1%} of GAS cycles (false positive)")

    print("\n" + "=" * 100)
    print("Q3  CATCHABILITY: on the killer cycle, was our side still EXITABLE at the")
    print("    START of that cycle (so a same-cycle pull/shrink of the OTHER side would")
    print("    have helped), or already dead?")
    print("=" * 100)
    for st in sorted(POS_R, key=lambda s: s["p"]["pnl"]):
        g = resample_2min(st["tl"], st["win_start"], st["win_end"])
        ca = cycle_analysis(g)
        if not ca:
            continue
        wi = ca["worst_i"]
        start_exit = g[wi][2]      # exitable at start of killer cycle
        end_exit = g[wi + 1][2]    # exitable at end of killer cycle
        print(f"{st['p']['ticker'][-28:]:<30} killer cycle {wi}: exitable "
              f"{start_exit:.3f} -> {end_exit:.3f}   (started {'ALIVE' if start_exit>0.01 else 'DEAD'})")


if __name__ == "__main__":
    main()
