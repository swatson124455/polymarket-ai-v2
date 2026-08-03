#!/usr/bin/env python3
"""THE 2026-08-03 SCOPE RECONCILIATION, kept runnable against its own frozen tape.

Answers "why does the rebuild engine disagree with the CSV canon", the question that blocked
arming KALSHI_NETEV_GATE. Runs offline against netev_tape_2026-08-03T170600Z.json (1234 fills /
143 settlements / 58 credits, read 2026-08-03T17:06:00Z, counts tying exactly to the live
cash-ledger row) so the result is reproducible rather than a one-off measurement.

⚠ TWO THINGS THIS SCRIPT PRINTS THAT NEED READING CORRECTLY:

1. It runs Part A on the UTC reading of the canon window, which is how the disagreement was
   first framed. The canon window is actually INCLUSIVE ET DATES (kalshi_netev_calibrate._date
   slices a -04:00 close_timestamp), so the like-for-like window is
   2026-07-21T04:00Z..07-23T03:59:59Z. On THAT window the finding is much sharper: temp had 0 of
   25 in-window markets unsettled at the CSV export and the two engines agree on temp trading
   P&L to the cent (-$36.1178 both), leaving the whole temp gap in the notional denominator;
   gas had 9 of 10 unsettled, so canon's +$0.2528 is a fragment of a complete -$40.2060.
   The mechanism is EXPORT-TIME COMPLETENESS, not a cash-vs-realized modelling dispute.

2. The credits line now prints DIFFER. That is CORRECT and expected. When this study was run,
   credits were filtered by created_at and reproduced the §M8 screenshot attribution EXACTLY
   ($2.15 gas / $23.06 temp) — that agreement is what validated per-event credit parsing and it
   still stands for the date rule. 844ea16 then put credits on the SAME clock as trading
   (scored by market, not by credit date), which deliberately picks up lagged credits the date
   window cut off. The CANON dict below is the CSV's numbers, so the two legs no longer match by
   construction. The TRADING waterfall is unaffected and still checks EXACT.

Part A: decompose canon -> rebuild into disjoint measured components that sum EXACTLY.

Part A: decompose canon -> rebuild into disjoint measured components that sum EXACTLY.
Part B: build the table on each candidate window and show the gate verdict per family,
        because NETEV_MIN_MARGIN_PCT defaults to 0.0 and a 'receipt' family below it is
        SKIPPED when flat. The window choice therefore decides which families keep quoting.
"""
import csv
import datetime as dt
import json
import os
import sys
from collections import defaultdict

WT = os.path.dirname(os.path.abspath(__file__))
SP = WT
sys.path.insert(0, WT)

import kalshi_netev_rebuild as R
from kalshi_attribution_ledger import replay_fills
from kalshi_netev_calibrate import family_of

D = json.load(open(os.path.join(SP, "netev_tape_2026-08-03T170600Z.json")))
NOW = R._iso(D["read_started_utc"])
LO = dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc)
HI = dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)
events, final_pos = replay_fills(D["fills"])


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------- PART A: the waterfall
rows = list(csv.DictReader(open(os.path.join(WT, "kalshi_transactions_2026-07-23.csv"), newline="")))
CANON = {"gas": {"trading": 0.2528, "notional": 214.8476, "credits": 2.15},
         "temp": {"trading": -36.1178, "notional": 142.6720, "credits": 23.06}}

print("=" * 100)
print("PART A — EXACT WATERFALL: CSV canon  ->  API rebuild   (canon window 07-21..07-22 ET / "
      "07-21T00Z..07-23T00Z UTC)")
print("=" * 100)

for FAM in ("gas", "temp"):
    in_win = {(e["fill"].get("ticker") or e["fill"].get("market_ticker"))
              for e in events if LO <= R._iso(e["fill"]["created_time"]) <= HI}
    rmkts = {t for t in in_win if family_of(t) == FAM}

    cash = defaultdict(lambda: {"before": 0.0, "in": 0.0, "after": 0.0})
    settle = defaultdict(float)
    for e in events:
        t = e["fill"].get("ticker") or e["fill"].get("market_ticker")
        if t not in rmkts:
            continue
        ts = R._iso(e["fill"]["created_time"])
        b = "in" if LO <= ts <= HI else ("before" if ts < LO else "after")
        cash[t][b] += float(e["cash"])
    for s in D["settlements"]:
        if s.get("ticker") in rmkts:
            settle[s["ticker"]] += f(s.get("revenue")) / 100.0

    cmkt = defaultdict(float)
    for r in rows:
        if r.get("type") != "trade" or family_of(r.get("market_ticker", "")) != FAM:
            continue
        if "2026-07-21" <= (r.get("close_timestamp") or "")[:10] <= "2026-07-22":
            cmkt[r["market_ticker"]] += f(r.get("realized_pnl_with_fees_dollars"))

    shared = rmkts & set(cmkt)
    canon_only = set(cmkt) - rmkts
    rebuild_only = rmkts - set(cmkt)

    c1 = -sum(cmkt[t] for t in canon_only)
    c2 = sum(cash[t]["before"] + cash[t]["in"] + cash[t]["after"] + settle[t] for t in rebuild_only)
    s_before = sum(cash[t]["before"] for t in shared)
    s_after = sum(cash[t]["after"] for t in shared)
    s_settle = sum(settle[t] for t in shared)
    s_in_vs_canon = sum(cash[t]["in"] for t in shared) - sum(cmkt[t] for t in shared)

    start = CANON[FAM]["trading"]
    end = start + c1 + c2 + s_before + s_after + s_settle + s_in_vs_canon
    print(f"\n--- {FAM.upper()} trading P&L bridge ---")
    print(f"  canon realized (in-window round trips)                     {start:>10.4f}")
    print(f"  C1  drop markets canon counts that never traded in-window  {c1:>10.4f}   "
          f"({len(canon_only)} mkts: settled in ET window, all fills predate it)")
    print(f"  C2  add markets traded in-window canon never closed        {c2:>10.4f}   "
          f"({len(rebuild_only)} mkts)")
    print(f"  C3a add pre-window cash on shared markets                  {s_before:>10.4f}")
    print(f"  C3b add post-window cash on shared markets                 {s_after:>10.4f}")
    print(f"  C3c add settlement revenue on shared markets               {s_settle:>10.4f}")
    print(f"  C3d in-window CASH vs canon REALIZED on shared markets     {s_in_vs_canon:>10.4f}   "
          f"(the open-at-edge / not-yet-realized term)")
    print(f"  {'':58s} {'='*10}")
    print(f"  rebuild trading (complete lifetime of in-window markets)   {end:>10.4f}")

    doc = R.build_table(D["fills"], D["settlements"], D["credits"], family_of, (LO, HI))
    actual = doc["families"][FAM]["trading_pnl"]
    print(f"  CHECK vs build_table: {actual:.4f}  -> "
          f"{'EXACT' if abs(actual - end) < 5e-4 else 'MISMATCH ' + str(actual - end)}")

    cn, rn = CANON[FAM]["notional"], doc["families"][FAM]["notional"]
    cr = CANON[FAM]["credits"]
    print(f"  credits: canon {cr:.4f} vs rebuild {doc['families'][FAM]['credits']:.4f} -> "
          f"{'IDENTICAL' if abs(cr - doc['families'][FAM]['credits']) < 5e-3 else 'DIFFER'}")
    print(f"  notional: canon {cn:.4f} -> rebuild {rn:.4f}   (canon = ENTRY leg of closed round "
          f"trips only; rebuild = EVERY fill of every in-window market)")
    print(f"  net%: canon {(cr + start) / cn * 100:+.4f}%  ->  rebuild "
          f"{doc['families'][FAM]['net_pct_notional'] * 100:+.4f}%")

# ---------------------------------------------------------------- PART B: window sweep
print("\n" + "=" * 100)
print("PART B — WHAT THE GATE WOULD DO, BY WINDOW   (NETEV_MIN_MARGIN_PCT=0.0; "
      "'receipt' family below 0 => FLAT-SKIP)")
print("=" * 100)

WINDOWS = [
    ("canon 07-21..07-23 (launch/defect era)", LO, HI),
    ("full history 07-20 -> now", dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc), NOW),
    ("post-governor 07-29T18:39Z -> now", dt.datetime(2026, 7, 29, 18, 39, 35, tzinfo=dt.timezone.utc), NOW),
    ("last 7d 07-27 -> now", dt.datetime(2026, 7, 27, tzinfo=dt.timezone.utc), NOW),
    ("08-01 -> now", dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc), NOW),
]

for label, lo, hi in WINDOWS:
    doc = R.build_table(D["fills"], D["settlements"], D["credits"], family_of, (lo, hi), now=NOW)
    fams = doc["families"]
    tot_cred = sum(v["credits"] for v in fams.values())
    print(f"\n### {label}   families={len(fams)}  credits_in_window=${tot_cred:.2f}  "
          f"unattributed=${doc['credits_unattributed']:.2f}")
    print(f"  {'family':22s} {'conf':>9} {'net%':>9} {'net$':>10} {'credits$':>9} "
          f"{'trading$':>10} {'notional$':>10} {'fills':>6}  verdict")
    for fam in sorted(fams, key=lambda k: (fams[k]["net_pct_notional"] is None,
                                           fams[k]["net_pct_notional"] or 0)):
        r = fams[fam]
        np_ = r["net_pct_notional"]
        if r["confidence"] in (None, "unproven"):
            verdict = "MODEL FALLBACK"
        elif np_ is not None and np_ < 0.0:
            verdict = "*** FLAT-SKIP (benched) ***"
        else:
            verdict = "allow"
        print(f"  {fam:22s} {r['confidence']:>9} "
              f"{(f'{np_*100:+.2f}' if np_ is not None else 'n/a'):>9} {r['net']:>10.4f} "
              f"{r['credits']:>9.4f} {r['trading_pnl']:>10.4f} {r['notional']:>10.4f} "
              f"{r['n_fills']:>6d}  {verdict}")
