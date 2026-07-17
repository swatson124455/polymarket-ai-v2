#!/usr/bin/env python3
"""Owls-Insight historical 1xbet-benchmark backtest FEASIBILITY census (EB, 2026-07-17).

VERDICT (reproduced by this script): the historical backtest is IMPOSSIBLE — the
two required data legs have NON-OVERLAPPING quality windows:

  1. PM intraday price (needed to price the bet 1-2h pre-start): CLOB
     /prices-history retains HOURLY resolution only ~30 days, so only markets
     from ~2026-06-16 onward return usable hourly PM history.
  2. Sharp (1xbet) closing line at usable precision: the Owls /history/odds
     endpoint stores esports h2h prices as PROPER AMERICAN odds only through
     ~Feb 2026; from March it FLOORS decimal odds to integers (price=1,2,3…),
     destroying implied-probability precision (home=1 -> P(home) anywhere in
     [0.50,0.99]) — unusable for a 2-point-edge rule.

Feb (precise odds) is BEFORE the PM-price window; Jun-16+ (has PM price) is in
the floored-odds era. Zero overlap -> zero valid backtest rows. Running the
audited edge_backtest on floored odds would fabricate edge numbers, so this
census is the correct-or-absent stopping point.

Inputs (VPS /home/ubuntu/eb-odds/): owls_pm_prices.jsonl, owls_cs2_event_odds.jsonl,
owls_pm_meta.json. Read-only; prints the census that supports the verdict.
"""
import json
import os
from collections import Counter

BASE = os.environ.get("EB_OWLS_DATA_DIR", "/home/ubuntu/eb-odds")


def _valid_american(p) -> bool:
    try:
        return abs(float(p)) >= 100.0
    except (TypeError, ValueError):
        return False


def main() -> int:
    prices = {}
    with open(os.path.join(BASE, "owls_pm_prices.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            prices[r["cid"]] = r["points"]
    meta_path = os.path.join(BASE, "owls_pm_meta.json")
    meta = json.load(open(meta_path, encoding="utf-8"))

    pm_ok = {c for c, n in prices.items() if n >= 12}   # >=12 hourly pts locatable
    print(f"PM markets with >=12 hourly price points (Jun-16+ retention): {len(pm_ok)}")

    # h2h odds precision by market month, and overlap with pm_ok
    by_month = {}
    overlap_precise = 0
    overlap_floored = 0
    with open(os.path.join(BASE, "owls_cs2_event_odds.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r["body"]:
                continue
            mo = str(meta.get(r["cid"], {}).get("gameStartTime") or "")[:7]
            d = json.loads(r["body"]).get("data", {})
            h2h = [s for s in d.get("snapshots", [])
                   if s.get("market") == "h2h" and s.get("book") == "1xbet"]
            if not h2h:
                continue
            precise = any(_valid_american(s.get("price")) for s in h2h)
            by_month.setdefault(mo, Counter())["precise" if precise else "floored"] += 1
            if r["cid"] in pm_ok:
                if precise:
                    overlap_precise += 1
                else:
                    overlap_floored += 1

    print("\nh2h odds precision by market month:")
    for mo in sorted(m for m in by_month if m):
        c = by_month[mo]
        tot = sum(c.values())
        if tot < 20:
            continue
        print(f"  {mo}: precise={c.get('precise',0):5d} floored={c.get('floored',0):5d} "
              f"({100*c.get('precise',0)/tot:.0f}% precise)")

    print("\nJOINABLE (has usable PM price AND h2h odds):")
    print(f"  with PRECISE odds  : {overlap_precise}  <- the only usable rows")
    print(f"  with FLOORED odds  : {overlap_floored}  <- unusable (precision destroyed)")
    print(f"\nVERDICT: usable historical backtest rows = {overlap_precise} "
          f"(non-overlapping quality windows). Forward PinnOdds stays primary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
