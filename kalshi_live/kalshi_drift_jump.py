#!/usr/bin/env python3
"""KALSHI DRIFT DETECTION — is the loss a DRIFT or a JUMP?

NEW FILE. Edits nothing.

Motivation, from the real price path of the single largest run-over
(KXTEMPLAXH-26JUL2212-T71.99, printed by kalshi_drift_selftest.py):

    minute   our value
        0      0.485      <- entry
        1..5   0.485      flat
        6      0.465
        7      0.085      <- 0.46 -> 0.085 IN ONE MINUTE
        8..28  0.155      sits
       29      0.015
       36      0.000      dead

That is not a market trending through a resting quote. It is a RE-PRICING GAP.
If the losses are gaps, then no detector sampling at 2-minute cycles can ever
have lead time, and the whole detection programme is misconceived -- the answer
would be market selection / sizing, not detection.

So: decompose each position's adverse move into the single worst minute vs the
rest, and measure how much warning existed BEFORE the worst minute.

  jump_share = (worst single-minute adverse move) / (total adverse move)
               1.00 = the entire loss happened in one minute (undetectable)
               0.10 = smooth bleed across many minutes (detectable)

  pre_warning(k) = how far our value had ALREADY fallen in the k minutes
               immediately before the killer minute. This is exactly what a
               trailing detector would have had to work with.
"""
import json
import os
import statistics

import kalshi_drift_detect as D
from kalshi_drift_labels import load_positions

HERE = os.path.dirname(os.path.abspath(__file__))


def decompose(st):
    tl = st["tl"]
    idx = [i for i, r in enumerate(tl)
           if st["win_start"] <= r["ts"] <= st["win_end"]]
    if len(idx) < 3:
        return None
    v0 = tl[idx[0]]["value"]
    vend = tl[idx[-1]]["value"]
    total = v0 - vend
    # worst single-minute adverse step inside the window
    worst, worst_i = 0.0, None
    for i in idx[1:]:
        step = tl[i - 1]["value"] - tl[i]["value"]
        if step > worst:
            worst, worst_i = step, i
    pre = {}
    for k in (2, 4, 6):
        if worst_i is not None and worst_i - 1 - k >= 0:
            pre[k] = tl[worst_i - 1 - k]["value"] - tl[worst_i - 1]["value"]
        else:
            pre[k] = None
    return {
        "ticker": st["p"]["ticker"], "side": st["p"]["side"],
        "label": st["p"]["label"], "entry": v0, "end": vend,
        "total_drop": total, "worst_min_drop": worst,
        "jump_share": (worst / total) if total > 0.02 else None,
        "pre2": pre[2], "pre4": pre[4], "pre6": pre[6],
        "n_min": len(idx), "pnl": st["p"]["pnl"],
        "vwap": st["p"]["vwap_entry"],
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
    POS_L = [s for s in POS if s["p"]["vwap_entry"] <= D.TAIL_CUT]
    TCTL = [s for s in states.values() if s["p"]["label"] == "temp_control"]
    GAS = [s for s in states.values()
           if s["p"]["family"] == "GAS" and s["p"]["label"] != "positive"]

    print("=" * 104)
    print("1. THE 6 RUNOVER POSITIVES, MINUTE BY MINUTE")
    print("   entry VWAP > 0.20, i.e. the ones a drift detector could physically catch")
    print("=" * 104)
    hdr = (f"{'contract':<30}{'entry':>7}{'total':>8}{'worst 1m':>10}{'jump':>7}"
           f"{'pre2':>7}{'pre4':>7}{'pre6':>7}{'mins':>6}{'pnl':>7}")
    print(hdr)
    print("-" * len(hdr))
    rows_r = []
    for st in sorted(POS_R, key=lambda s: s["p"]["pnl"]):
        d = decompose(st)
        if not d:
            continue
        rows_r.append(d)
        js = f"{d['jump_share']:.2f}" if d["jump_share"] is not None else "  --"
        pv = lambda x: f"{x:+.3f}" if x is not None else "   --"
        print(f"{d['ticker'][-28:]:<30}{d['entry']:>7.3f}{d['total_drop']:>8.3f}"
              f"{d['worst_min_drop']:>10.3f}{js:>7}"
              f"{pv(d['pre2']):>7}{pv(d['pre4']):>7}{pv(d['pre6']):>7}"
              f"{d['n_min']:>6}{d['pnl']:>7.2f}")

    js = [d["jump_share"] for d in rows_r if d["jump_share"] is not None]
    print(f"\n  median jump_share = {statistics.median(js):.2f}  "
          f"(1.00 = the whole loss arrived in a single minute)")
    print(f"  positions where ONE minute carried >=50% of the move: "
          f"{sum(1 for x in js if x >= 0.5)}/{len(js)}")
    print(f"  positions where ONE minute carried >=80% of the move: "
          f"{sum(1 for x in js if x >= 0.8)}/{len(js)}")

    pre6 = [d["pre6"] for d in rows_r if d["pre6"] is not None]
    if pre6:
        print(f"\n  WARNING AVAILABLE BEFORE THE KILLER MINUTE (6-min trailing move):")
        print(f"    median {statistics.median(pre6):+.3f}   range "
              f"[{min(pre6):+.3f}, {max(pre6):+.3f}]")
        print(f"    D1's firing threshold is -0.080. Positions whose pre-killer 6-min")
        print(f"    move already exceeded it: "
              f"{sum(1 for x in pre6 if x >= 0.08)}/{len(pre6)}")

    print("\n" + "=" * 104)
    print("2. JUMP SHARE BY GROUP — is gappiness what separates TEMP from GAS?")
    print("=" * 104)
    print(f"{'group':<22}{'n':>4}{'med jump_share':>16}{'med total drop':>16}"
          f"{'med worst 1m':>14}")
    print("-" * 72)
    for nm, g in (("POS RUNOVER", POS_R), ("POS TAIL", POS_L),
                  ("TEMP control", TCTL), ("GAS (all)", GAS)):
        ds = [x for x in (decompose(s) for s in g) if x]
        j = [d["jump_share"] for d in ds if d["jump_share"] is not None]
        t = [d["total_drop"] for d in ds]
        w = [d["worst_min_drop"] for d in ds]
        if not ds:
            continue
        jm = f"{statistics.median(j):.2f}" if j else "  -- (no move)"
        print(f"{nm:<22}{len(ds):>4}{jm:>16}"
              f"{statistics.median(t):>16.3f}{statistics.median(w):>14.3f}")

    print("\n" + "=" * 104)
    print("3. THE TAIL POSITIVES — was there anything to detect at all?")
    print("   entry VWAP <= 0.20. These are cheap out-of-the-money tickets we were")
    print("   sold. If total_drop is tiny, the position never moved: it just expired.")
    print("=" * 104)
    hdr = f"{'contract':<30}{'entry':>7}{'end':>7}{'total drop':>12}{'mins':>6}{'pnl':>8}"
    print(hdr)
    print("-" * len(hdr))
    small = 0
    for st in sorted(POS_L, key=lambda s: s["p"]["pnl"]):
        d = decompose(st)
        if not d:
            continue
        if d["total_drop"] < 0.05:
            small += 1
        print(f"{d['ticker'][-28:]:<30}{d['entry']:>7.3f}{d['end']:>7.3f}"
              f"{d['total_drop']:>12.3f}{d['n_min']:>6}{d['pnl']:>8.2f}")
    print(f"\n  TAIL positives whose price never moved more than 0.05: "
          f"{small}/{len(POS_L)}")
    print("  For those, there is NO adverse move in existence to detect. They are a")
    print("  PRICING problem (we bought a 7c ticket worth less than 7c), not a")
    print("  detection problem.")

    # -------------------------------------------------- gas 07-23 pair ----
    print("\n" + "=" * 104)
    print("4. THE 07-23 GAS PAIR — both legs died. Different failure mode again.")
    print("=" * 104)
    print("  NOTE: these settlements post-date the 07-23 receipt CSV, so the lots are")
    print("  NOT in the labelled test set and contribute to NO confusion matrix here.")
    for tk in ("KXAAAGASD-26JUL23-4.090", "KXAAAGASD-26JUL23-4.105"):
        m = cache[tk]["market"]
        print(f"  {tk}  strike {m.get('floor_strike')}  result {m.get('result')}  "
              f"settled at {m.get('expiration_value')}")
    print("  Settlement 4.091 landed BETWEEN strikes 4.090 and 4.105.")
    print("  A long-4.105 leg and a short-4.090 leg BOTH expire worthless only when the")
    print("  underlying pins between them. That is not adverse drift through a quote --")
    print("  no directional detector can fire, because the underlying did not go")
    print("  anywhere. It is a STRIKE-PAIRING geometry, addressed by strike selection")
    print("  and by not holding both wings into settlement, not by drift detection.")

    json.dump({"runover": rows_r}, open(os.path.join(HERE, "kalshi_drift_jump.json"),
                                        "w"), indent=1, default=str)
    print(f"\nwrote {os.path.join(HERE, 'kalshi_drift_jump.json')}")


if __name__ == "__main__":
    main()
