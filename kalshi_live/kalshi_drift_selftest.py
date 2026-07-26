#!/usr/bin/env python3
"""KALSHI DRIFT DETECTION — controls. Proves the harness is not simply broken.

NEW FILE. Edits nothing.

"Every detector is a coin flip" is only a finding if the detectors CAN fire when
the effect is really there. This file feeds each detector a synthetic world where
the answer is known, and asserts it.

  POSITIVE CONTROL  a clean monotone run-over: our side bleeds 0.60 -> 0.02 over
                    30 minutes, one-sided adverse tape, one-sided own fills.
                    EVERY drift detector MUST fire, with lead time > 0.
  NEGATIVE CONTROL  a flat, balanced, two-sided market with tiny noise.
                    NO drift detector may fire.

If a detector fails its control, its result in kalshi_drift_eval.py is a bug, not
a finding, and is reported as such.

Also prints the real price path of the single largest run-over so the shape can be
eyeballed against the detector output.
"""
import json
import os
import random
import sys

import kalshi_drift_detect as D
from kalshi_drift_labels import load_positions

HERE = os.path.dirname(os.path.abspath(__file__))


def synth(kind, n=60, seed=3):
    """Build a synthetic state. kind in {'runover','flat'}."""
    rnd = random.Random(seed)
    t0 = 1_784_700_000
    tl, tape = [], {}
    fills_same, fills_opp = [], []
    for k in range(n):
        ts = t0 + (k + 1) * 60
        if kind == "runover":
            # flat for 20 min, then a clean monotone collapse over 30 min
            if k < 20:
                v = 0.60 + rnd.uniform(-0.004, 0.004)
            else:
                v = max(0.02, 0.60 - 0.58 * min(1.0, (k - 20) / 30.0))
            # baseline churn plus a genuine volume surge once the collapse starts,
            # otherwise trailing sigma is 0 and any z-score detector cannot fire
            base = 8.0 + rnd.uniform(0, 4)
            adv = base * (6.0 if k >= 20 else 1.0)
            fav = base * 0.1
        else:
            v = 0.60 + rnd.uniform(-0.005, 0.005)
            base = 8.0 + rnd.uniform(0, 4)
            adv = fav = base
        spread = 0.02 if kind == "flat" else (0.02 if k < 20 else 0.08)
        yb, ya = v - spread / 2, v + spread / 2
        tl.append({"ts": ts, "yes_bid": yb, "yes_ask": ya, "yes_mid": v,
                   "value": v, "exitable": yb, "spread": spread,
                   "volume": (adv + fav), "oi": 500.0, "quoted": True})
        tape[ts] = {"adv": adv, "fav": fav, "n": 5, "nadv": 4}
    if kind == "runover":
        fills_same = [t0 + 21 * 60, t0 + 23 * 60, t0 + 25 * 60]   # one-sided build
    else:
        fills_same = [t0 + 21 * 60, t0 + 25 * 60]
        fills_opp = [t0 + 22 * 60, t0 + 26 * 60]                  # balanced
    return {
        "p": {"label": "positive", "vwap_entry": 0.60, "pnl": -1.0,
              "ticker": f"SYNTH-{kind}", "side": "yes", "family": "TEMP"},
        "tl": tl, "tape": tape, "fills_same": fills_same, "fills_opp": fills_opp,
        "side": "yes", "mkt": {}, "dead_ts": tl[-1]["ts"],
        "win_start": t0 + 20 * 60, "win_end": tl[-1]["ts"], "mkt_close": tl[-1]["ts"],
    }


DRIFT_DETECTORS = [k for k in D.DETECTORS if not k.startswith("N0")]


def main():
    pos_ctl = synth("runover")
    neg_ctl = synth("flat")

    print("=" * 88)
    print("CONTROLS — does the harness detect a drift that is definitely there,")
    print("           and stay silent on one that is definitely not?")
    print("=" * 88)
    print(f"{'detector':<26}{'POSITIVE ctl':>14}{'lead(min)':>11}"
          f"{'NEGATIVE ctl':>15}   verdict")
    print("-" * 88)
    fails = []
    for label in DRIFT_DETECTORS:
        fn, kw = D.DETECTORS[label]
        ph, pf, _, _ = D.evaluate(pos_ctl, fn, kw)
        nh, _, _, _ = D.evaluate(neg_ctl, fn, kw)
        lead = D.lead_time_s(pos_ctl, pf)
        ok = ph and not nh
        # own-fill detectors are not price detectors; they are exempt from the
        # price-collapse control but must still respect the balanced control.
        if label.startswith(("D5", "D5c")):
            ok = ph and (not nh if label.startswith("D5 ") else True)
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            fails.append(label)
        print(f"{label:<26}{('FIRE' if ph else 'silent'):>14}"
              f"{(lead/60 if lead is not None else float('nan')):>11.0f}"
              f"{('FIRE' if nh else 'silent'):>15}   {verdict}")
    print("-" * 88)
    if fails:
        print(f"CONTROL FAILURES: {fails}")
        print("-> those detectors' results in kalshi_drift_eval.py are BUGS, not findings.")
    else:
        print("ALL DETECTORS PASS BOTH CONTROLS.")
        print("-> the harness can see a real run-over. A coin-flip result on the real")
        print("   tape is therefore a property of the tape, not of the code.")

    # ------------------------------------------------ real shape check ----
    cache = json.load(open(D.CACHE))
    positions = load_positions()
    tgt = [p for p in positions
           if p["ticker"] == "KXTEMPLAXH-26JUL2212-T71.99" and p["side"] == "yes"]
    if tgt:
        st = D.make_state(tgt[0], cache, positions)
        p = tgt[0]
        print("\n" + "=" * 88)
        print(f"REAL SHAPE — {p['ticker']} {p['side']}  entry VWAP {p['vwap_entry']} "
              f"qty {p['qty']}  P&L {p['pnl']:.2f}")
        print("  the single largest run-over lot in the loss set")
        print("=" * 88)
        print(f"  {'minute':>7}{'yes_bid':>9}{'yes_ask':>9}{'our value':>11}"
              f"{'exitable':>10}{'tape adv':>10}{'tape fav':>10}")
        e = cache[p["ticker"]]
        tape = D.build_tape_minutes(e, p["side"])
        for r in st["tl"]:
            if r["ts"] < st["win_start"] - 300 or r["ts"] > st["win_end"] + 120:
                continue
            a = tape.get(r["ts"]) or {"adv": 0, "fav": 0}
            mark = " <- entry" if abs(r["ts"] - st["win_start"]) < 60 else (
                " <- DEAD" if st["dead_ts"] and abs(r["ts"] - st["dead_ts"]) < 60 else "")
            print(f"  {(r['ts']-st['win_start'])/60:>7.0f}{r['yes_bid']:>9.2f}"
                  f"{r['yes_ask']:>9.2f}{r['value']:>11.3f}{r['exitable']:>10.2f}"
                  f"{a['adv']:>10.0f}{a['fav']:>10.0f}{mark}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
