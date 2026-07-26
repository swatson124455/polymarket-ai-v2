#!/usr/bin/env python3
"""KALSHI DRIFT DETECTION — exposure-matched evaluation, ROC, nulls, lead time.

NEW FILE. Edits nothing. Imports detectors from kalshi_drift_detect.

=============================================================================
WHY THIS FILE EXISTS -- the first-pass result was not interpretable
=============================================================================
Raw "did the detector ever fire during the position" is uninterpretable here
because the exposure windows are wildly unequal (MEASURED):

    group          median window   median 2-min decision points
    POS                 36 min                18
    NEG_strict         434 min               157
    NEG_broad          203 min                56
    TEMP_ctrl           12 min                 6

A detector with ANY per-cycle hazard fires more often on NEG_strict purely
because it gets 157 chances instead of 18 -- that inflates FPR.  Worse in the
other direction: TEMP_ctrl gets 6 chances vs the positives' 18, so the
within-family FPR is biased DOWNWARD, i.e. biased IN FAVOUR of every detector.
The first-pass TEMP_ctrl column therefore flatters the detectors and must not
be quoted.

The window difference is also partly ENDOGENOUS: we held the losers longer
precisely because they became unexitable. So it cannot be argued away.

Two exposure-honest frames are computed here instead:
  (A) POINT-LEVEL HAZARD -- every (contract, 2-min decision point) is one
      sample, labelled by whether the position it belongs to ended worthless.
      Normalised by construction.
  (B) MATCHED-WINDOW -- position-level, but every position is evaluated over
      exactly the first K decision points after entry; positions with fewer
      are dropped. Equal chances for everyone.

Plus: ROC/AUC on continuous scores, and the null baselines any detector must
beat before it is worth a single line of production code.
=============================================================================
"""
import json
import math
import os
import random
import statistics
from collections import defaultdict

import kalshi_drift_detect as D
from kalshi_drift_labels import load_positions

HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------ continuous ---
# Scores are "badness": higher = more adverse. Needed for ROC/AUC.

def s_mid_drift(st, i, W=6):
    v0 = D._val_at(st["tl"], i, W)
    return None if v0 is None else -(st["tl"][i]["value"] - v0)


def s_exit_drift(st, i, W=6):
    j = i - W
    return None if j < 0 else -(st["tl"][i]["exitable"] - st["tl"][j]["exitable"])


def s_tape_share(st, i, W=6, minvol=20.0):
    adv = fav = 0.0
    for k in range(max(0, i - W + 1), i + 1):
        a = st["tape"].get(st["tl"][k]["ts"])
        if a:
            adv += a["adv"]
            fav += a["fav"]
    return None if (adv + fav) < minvol else adv / (adv + fav)


def s_tape_net(st, i, W=6):
    net = 0.0
    for k in range(max(0, i - W + 1), i + 1):
        a = st["tape"].get(st["tl"][k]["ts"])
        if a:
            net += a["adv"] - a["fav"]
    return net


def s_own_onesided(st, i, W=8):
    """One-sided own-fill clustering as a continuous score: same-side entry lots
    minus opposite-side entry lots in the trailing W minutes."""
    t1 = st["tl"][i]["ts"]
    t0 = t1 - W * 60
    same = sum(1 for x in st["fills_same"] if t0 < x <= t1)
    opp = sum(1 for x in st["fills_opp"] if t0 < x <= t1)
    return float(same - opp)


def s_value_floor(st, i):
    return -st["tl"][i]["value"]


def s_spread(st, i, W=10):
    if i < W:
        return None
    base = statistics.median(st["tl"][j]["spread"] for j in range(i - W, i))
    return st["tl"][i]["spread"] - base


SCORES = {
    "S1 mid_drift(6m)":       s_mid_drift,
    "S2 exit_drift(6m)":      s_exit_drift,
    "S3 tape_adv_share(6m)":  s_tape_share,
    "S4 tape_adv_net(6m)":    s_tape_net,
    "S5 own_fill_onesided":   s_own_onesided,
    "S7 value_floor":         s_value_floor,
    "S8 spread_vs_median":    s_spread,
}


def auc(pos, neg):
    """Mann-Whitney AUC with tie correction. 0.5 = coin flip."""
    if not pos or not neg:
        return float("nan")
    allv = sorted(pos + neg)
    rank, i = {}, 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        r = (i + j) / 2.0 + 1
        rank[allv[i]] = r
        i = j + 1
    rs = sum(rank[v] for v in pos)
    n1, n2 = len(pos), len(neg)
    return (rs - n1 * (n1 + 1) / 2.0) / (n1 * n2)


def boot_auc_ci(pos, neg, n=2000, seed=7):
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        p = [rnd.choice(pos) for _ in pos]
        q = [rnd.choice(neg) for _ in neg]
        a = auc(p, q)
        if not math.isnan(a):
            vals.append(a)
    if not vals:
        return (float("nan"), float("nan"))
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))])


# -------------------------------------------------------------- harness ---

def collect(states, groups_pred):
    return [st for st in states.values() if groups_pred(st["p"])]


def point_samples(st, fn, kw=None, score=None, W=None):
    """Yield (value, minutes_to_deadline) at each 2-min decision point."""
    kw = kw or {}
    out = []
    deadline = st["dead_ts"] or st["mkt_close"]
    for i in D.decision_points(st):
        try:
            v = score(st, i) if score else fn(st, i, **kw)
        except Exception:  # noqa: BLE001
            v = None
        if v is None:
            continue
        out.append((v, (deadline - st["tl"][i]["ts"]) / 60.0))
    return out


def matched_window(st, fn, kw, K):
    """Did the detector fire within the FIRST K decision points after entry?
    Returns None if the position has fewer than K points (dropped)."""
    pts = D.decision_points(st)
    if len(pts) < K:
        return None, None
    deadline = st["dead_ts"] or st["mkt_close"]
    for i in pts[:K]:
        try:
            if fn(st, i, **kw):
                return True, (deadline - st["tl"][i]["ts"]) / 60.0
        except Exception:  # noqa: BLE001
            pass
    return False, None


def conf_line(name, tp, np_, fp, nn):
    tpr = tp / np_ if np_ else float("nan")
    fpr = fp / nn if nn else float("nan")
    lift = (tpr / fpr) if fpr else float("inf")
    return (f"{name:<26}{tp:>4}/{np_:<4}{tpr:>7.2f}"
            f"{fp:>6}/{nn:<5}{fpr:>7.2f}{lift:>8.2f}")


def main():
    cache = json.load(open(D.CACHE))
    positions = load_positions()
    states = {}
    for p in positions:
        st = D.make_state(p, cache, positions)
        if st:
            states[(p["ticker"], p["side"])] = st

    POS = collect(states, lambda p: p["label"] == "positive")
    POS_R = [s for s in POS if s["p"]["vwap_entry"] > D.TAIL_CUT]
    POS_L = [s for s in POS if s["p"]["vwap_entry"] <= D.TAIL_CUT]
    TCTL = collect(states, lambda p: p["label"] == "temp_control")
    NEG_S = collect(states, lambda p: p["label"] == "neg_strict")
    NEG_B = collect(states, lambda p: p["label"] in ("neg_strict", "neg_broad"))
    POS_TEMP = [s for s in POS if s["p"]["family"] == "TEMP"]

    out = {}

    # ---------------------------------------------------------- NULLS ----
    print("=" * 100)
    print("0. THE NULLS ANY DETECTOR MUST BEAT  (zero market data used)")
    print("=" * 100)
    allp = [s for s in states.values()]
    n_pos = len(POS)
    print(f"base rate, position level : {n_pos}/{len(allp)} = "
          f"{n_pos/len(allp):.2f} of positions ended worthless")
    lots_lose = sum(1 for s in states.values() if s["p"]["pnl"] < 0)
    print(f"base rate, any loss        : {lots_lose}/{len(allp)} = "
          f"{lots_lose/len(allp):.2f} of positions lost money")

    # N2 family
    tp = sum(1 for s in POS if s["p"]["family"] == "TEMP")
    fp = sum(1 for s in NEG_B if s["p"]["family"] == "TEMP")
    print("\n" + f"{'null':<26}{'TP':>9}{'TPR':>7}{'FP':>12}{'FPR':>7}{'lift':>8}")
    print(conf_line("N2 ticker startswith TEMP", tp, len(POS), fp, len(NEG_B)))
    # within TEMP the family null is useless by construction:
    print(conf_line("N2 (within TEMP)", tp, len(POS), len(TCTL), len(TCTL)))
    # N3 entry price
    tp = sum(1 for s in POS if s["p"]["vwap_entry"] <= 0.20)
    fp = sum(1 for s in TCTL if s["p"]["vwap_entry"] <= 0.20)
    print(conf_line("N3 entry VWAP<=0.20 (TEMP)", tp, len(POS), fp, len(TCTL)))

    # ------------------------------------------- (A) POINT-LEVEL HAZARD ----
    print("\n" + "=" * 100)
    print("A. POINT-LEVEL HAZARD -- exposure-normalised. One sample = one 2-minute")
    print("   decision point. WITHIN TEMP ONLY (the only unconfounded comparison).")
    print("=" * 100)
    print(f"{'detector':<26}{'TP pts':>9}{'TPR':>7}{'FP pts':>12}{'FPR':>7}{'lift':>8}")
    print("-" * 70)
    pt_rows = {}
    for label, (fn, kw) in D.DETECTORS.items():
        tp = np_ = fp = nn = 0
        for st in POS_TEMP:
            for v, _ in point_samples(st, fn, kw):
                np_ += 1
                tp += bool(v)
        for st in TCTL:
            for v, _ in point_samples(st, fn, kw):
                nn += 1
                fp += bool(v)
        pt_rows[label] = (tp, np_, fp, nn)
        print(conf_line(label, tp, np_, fp, nn))
    out["point_level_within_temp"] = pt_rows

    # ------------------------------------------- (B) MATCHED WINDOW ----
    for K in (5, 10):
        print("\n" + "=" * 100)
        print(f"B. MATCHED WINDOW -- first {K} decision points ({2*K} min) after entry, "
              f"equal chances")
        print("=" * 100)
        kept = {}
        for nm, g in (("POS_TEMP", POS_TEMP), ("POS_RUNOVER", POS_R),
                      ("POS_TAIL", POS_L), ("TEMP_ctrl", TCTL),
                      ("NEG_strict", NEG_S), ("NEG_broad", NEG_B)):
            kept[nm] = [s for s in g if len(D.decision_points(s)) >= K]
        print("  kept after dropping short positions: " +
              "  ".join(f"{k}={len(v)}/{len(dict(POS_TEMP=POS_TEMP,POS_RUNOVER=POS_R,POS_TAIL=POS_L,TEMP_ctrl=TCTL,NEG_strict=NEG_S,NEG_broad=NEG_B)[k])}"
                        for k, v in kept.items()))
        hdr = (f"{'detector':<26}{'TPR(temp)':>10}{'FPR(temp)':>10}{'lift':>7}"
               f"{'FPR(gasP)':>10}{'FPR(gasAll)':>12}   lead med[min,max] min")
        print(hdr)
        print("-" * len(hdr))
        for label, (fn, kw) in D.DETECTORS.items():
            res, leads = {}, []
            for nm in kept:
                hits = tot = 0
                for st in kept[nm]:
                    h, lt = matched_window(st, fn, kw, K)
                    if h is None:
                        continue
                    tot += 1
                    hits += bool(h)
                    if h and nm == "POS_RUNOVER" and lt is not None:
                        leads.append(lt)
                res[nm] = (hits, tot)
            def r(nm):
                h, t = res[nm]
                return h / t if t else float("nan")
            tpr, fpr = r("POS_TEMP"), r("TEMP_ctrl")
            lift = tpr / fpr if fpr else float("inf")
            ld = (f"{statistics.median(leads):5.0f} [{min(leads):4.0f},"
                  f"{max(leads):5.0f}]" if leads else "        --      ")
            print(f"{label:<26}{tpr:>10.2f}{fpr:>10.2f}{lift:>7.2f}"
                  f"{r('NEG_strict'):>10.2f}{r('NEG_broad'):>12.2f}   {ld}")

    # ------------------------------------------------------- (C) ROC ----
    print("\n" + "=" * 100)
    print("C. ROC / AUC -- threshold-free separation, WITHIN TEMP.")
    print("   position level: score = worst (max) score over the position's window")
    print("   0.50 = coin flip. CI = 2000-sample bootstrap.")
    print("=" * 100)
    hdr = (f"{'score':<26}{'AUC pos-lvl':>12}{'95% CI':>18}{'nP':>5}{'nN':>5}"
           f"{'AUC pt-lvl':>12}{'nP pts':>8}{'nN pts':>8}")
    print(hdr)
    print("-" * len(hdr))
    auc_out = {}
    for label, sf in SCORES.items():
        pv = [max((v for v, _ in point_samples(s, None, score=sf)), default=None)
              for s in POS_TEMP]
        nv = [max((v for v, _ in point_samples(s, None, score=sf)), default=None)
              for s in TCTL]
        pv = [x for x in pv if x is not None]
        nv = [x for x in nv if x is not None]
        a = auc(pv, nv)
        lo, hi = boot_auc_ci(pv, nv) if pv and nv else (float("nan"),) * 2
        ppts = [v for s in POS_TEMP for v, _ in point_samples(s, None, score=sf)]
        npts = [v for s in TCTL for v, _ in point_samples(s, None, score=sf)]
        apt = auc(ppts, npts)
        auc_out[label] = {"auc_pos": a, "ci": [lo, hi], "auc_pt": apt,
                          "nP": len(pv), "nN": len(nv)}
        print(f"{label:<26}{a:>12.3f}{f'[{lo:.2f},{hi:.2f}]':>18}"
              f"{len(pv):>5}{len(nv):>5}{apt:>12.3f}{len(ppts):>8}{len(npts):>8}")
    out["auc_within_temp"] = auc_out

    # ------------------------------------------ (C2) MATCHED-WINDOW AUC ----
    print("\n" + "=" * 100)
    print("C2. THE SAME AUC, EXPOSURE-MATCHED.  Section C scored each position by its")
    print("    WORST value over its whole window -- and positives have ~3x longer")
    print("    windows (36 vs 12 min), so more draws = a worse max = inflated AUC.")
    print("    Here every position is scored over exactly its FIRST K decision points.")
    print("    THIS IS THE NUMBER THAT COUNTS. Section C is retained to show the bias.")
    print("=" * 100)
    for K in (5, 10):
        print(f"\n  --- K = {K} points ({2*K} min after entry) ---")
        hdr = (f"  {'score':<26}{'AUC':>8}{'95% CI':>16}{'nP':>5}{'nN':>5}"
               f"   verdict")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for label, sf in SCORES.items():
            def mx(s):
                pts = D.decision_points(s)
                if len(pts) < K:
                    return None
                vals = []
                for i in pts[:K]:
                    try:
                        v = sf(s, i)
                    except Exception:  # noqa: BLE001
                        v = None
                    if v is not None:
                        vals.append(v)
                return max(vals) if vals else None
            pv = [x for x in (mx(s) for s in POS_TEMP) if x is not None]
            nv = [x for x in (mx(s) for s in TCTL) if x is not None]
            if not pv or not nv:
                print(f"  {label:<26}{'--':>8}   insufficient")
                continue
            a = auc(pv, nv)
            lo, hi = boot_auc_ci(pv, nv)
            verdict = ("SEPARATES" if lo > 0.5 else
                       "INVERTED" if hi < 0.5 else "coin flip (CI spans 0.5)")
            print(f"  {label:<26}{a:>8.3f}{f'[{lo:.2f},{hi:.2f}]':>16}"
                  f"{len(pv):>5}{len(nv):>5}   {verdict}")

    # ------------------------------------------- (C3) BEST-CASE THRESHOLD ----
    print("\n" + "=" * 100)
    print("C3. THE MOST GENEROUS POSSIBLE READING: the threshold is chosen AFTER")
    print("    seeing the labels, to maximise Youden's J, on the same 19+17 positions")
    print("    it is scored on. This is a deliberately overfit UPPER BOUND -- a real")
    print("    detector can only do worse out of sample. If the upper bound is weak,")
    print("    the honest number is weaker still.")
    print("=" * 100)
    K = 5
    hdr = (f"  {'score':<26}{'best thr':>10}{'TPR':>7}{'FPR':>7}{'lift':>7}"
           f"{'J':>7}   note")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for label, sf in SCORES.items():
        def mx(s):
            pts = D.decision_points(s)
            if len(pts) < K:
                return None
            vals = []
            for i in pts[:K]:
                try:
                    v = sf(s, i)
                except Exception:  # noqa: BLE001
                    v = None
                if v is not None:
                    vals.append(v)
            return max(vals) if vals else None
        pv = [x for x in (mx(s) for s in POS_TEMP) if x is not None]
        nv = [x for x in (mx(s) for s in TCTL) if x is not None]
        if not pv or not nv:
            continue
        best = None
        for thr in sorted(set(pv + nv)):
            tpr = sum(1 for x in pv if x >= thr) / len(pv)
            fpr = sum(1 for x in nv if x >= thr) / len(nv)
            j = tpr - fpr
            if best is None or j > best[0]:
                best = (j, thr, tpr, fpr)
        j, thr, tpr, fpr = best
        lift = tpr / fpr if fpr else float("inf")
        note = ("USELESS: fires on >half the survivors" if fpr > 0.5 else
                "weak" if j < 0.35 else "candidate")
        print(f"  {label:<26}{thr:>10.3f}{tpr:>7.2f}{fpr:>7.2f}{lift:>7.2f}"
              f"{j:>7.2f}   {note}")

    # -------------------------------------------------- (D) LEAD TIME ----
    print("\n" + "=" * 100)
    print("D. LEAD TIME on the 6 RUNOVER positives (entry VWAP > 0.20).")
    print("   deadline = last minute our side was still exitable above $0.01.")
    print("   USABLE requires lead >= 2 min (one cycle). Reported per position.")
    print("=" * 100)
    for label, (fn, kw) in D.DETECTORS.items():
        if label.startswith("N0"):
            continue
        row = []
        for st in POS_R:
            hit, first, n, k = D.evaluate(st, fn, kw)
            lt = D.lead_time_s(st, first)
            row.append("  --" if lt is None else f"{lt/60:4.0f}")
        print(f"{label:<26}" + " ".join(row) +
              f"   |  {sum(1 for x in row if x != '  --')}/{len(POS_R)} fired")
    print(f"{'position':<26}" +
          " ".join(f"{s['p']['ticker'].split('-')[-1][:4]:>4}" for s in POS_R))
    print(f"{'entry vwap':<26}" +
          " ".join(f"{s['p']['vwap_entry']:>4.2f}" for s in POS_R))
    print(f"{'pnl':<26}" + " ".join(f"{s['p']['pnl']:>4.0f}" for s in POS_R))
    print(f"{'window (min)':<26}" +
          " ".join(f"{(s['win_end']-s['win_start'])/60:>4.0f}" for s in POS_R))

    json.dump(out, open(os.path.join(HERE, "kalshi_drift_eval.json"), "w"),
              indent=1, default=str)
    print(f"\nwrote {os.path.join(HERE, 'kalshi_drift_eval.json')}")


if __name__ == "__main__":
    main()
