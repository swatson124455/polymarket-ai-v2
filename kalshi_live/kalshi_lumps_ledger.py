#!/usr/bin/env python3
"""LUMPS LEDGER — per-family realized damage vs (eventually) rewards, at receipt cadence.

Operator-named 2026-07-30: "how do we review toxicity more now" — REVIEW, never prediction
(the pre-entry toxicity gate was refuted: jump-rate persistence 0.15-0.19). Sources are the
already-validated feeds ONLY — this tool deliberately does NOT recompute P&L from raw fills
(the fill-direction sign trap sank two ad-hoc attempts on 2026-07-30; see the allocation-key
audit session):
  - kalshi_fill_costs.json  : per-market realized pnl/fees/cost-rate (validated feed, live)
  - quoter_state.json       : today's loss-governor latches (mkt_loss_tripped)
  - REWARDS column          : manual UI pull (M2b) until credits are machine-readable; edit
                              REWARDS_BY_FAMILY below per receipt cycle, source-stamped.
Read-only. Run: python3 kalshi_lumps_ledger.py  (from the live dir).
"""
import json
import os
from collections import defaultdict

# Receipt credits per family — OPERATOR-SOURCED (Kalshi UI itemization), update each cycle.
# Format: family -> (usd, "source stamp"). Empty until the first receipt lands.
REWARDS_BY_FAMILY = {}

DATA = os.path.dirname(os.path.abspath(__file__))


def main():
    try:
        fc = json.load(open(os.path.join(DATA, "kalshi_fill_costs.json"))).get("markets", {})
    except Exception as e:
        print("fill_costs feed unreadable:", e)
        return 1
    try:
        tripped = set(json.load(open(os.path.join(DATA, "quoter_state.json"))
                                ).get("mkt_loss_tripped") or [])
    except Exception:
        tripped = set()
    fam = defaultdict(lambda: {"pnl": 0.0, "fees": 0.0, "mkts": 0, "trips": 0,
                               "worst_mkt": None, "worst_pnl": 0.0})
    for t, r in fc.items():
        f = fam[t.split("-")[0]]
        pnl = float(r.get("realized_pnl_usd") or 0.0)
        f["pnl"] += pnl
        f["fees"] += float(r.get("fees_usd") or 0.0)
        f["mkts"] += 1
        if t in tripped:
            f["trips"] += 1
        if pnl < f["worst_pnl"]:
            f["worst_pnl"], f["worst_mkt"] = pnl, t
    print("%-24s %8s %6s %5s %6s  %-28s %8s  %s" % (
        "family", "realized", "fees", "mkts", "trips", "worst market", "worst$",
        "rewards(UI)"))
    for name, f in sorted(fam.items(), key=lambda x: x[1]["pnl"]):
        rw = REWARDS_BY_FAMILY.get(name)
        print("%-24s %8.2f %6.2f %5d %6d  %-28s %8.2f  %s" % (
            name, f["pnl"], f["fees"], f["mkts"], f["trips"],
            f["worst_mkt"] or "-", f["worst_pnl"],
            ("%.2f (%s)" % rw) if rw else "pending"))
    tot = sum(f["pnl"] for f in fam.values())
    print("TOTAL realized (fill-costs feed window): %.2f | governor trips today: %d | "
          "rewards column: %s" % (tot, len(tripped),
                                  "populated" if REWARDS_BY_FAMILY else
                                  "PENDING first receipt (UI pull, M2b)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
