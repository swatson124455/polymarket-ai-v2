#!/usr/bin/env python3
"""GROUND-TRUTH validation of the taker-side convention.

Two known maker fills tonight (from /portfolio/positions + the session log):
  03:56:53Z KXCLARITYVOTE-26JUL-AUG08  we BOUGHT 15 NO @ 0.49  (position_fp -15)
  04:02:16Z KXMUSKNW-26JUL31-T700      we BOUGHT 10 YES @ 0.72 (position_fp +10)

We were the MAKER (resting bid) in both. So:
  - our NO bid filled  -> the TAKER bought YES -> tape taker_side should be 'yes',
    yes_price = 1 - 0.49 = 0.51
  - our YES bid filled -> the TAKER bought NO  -> tape taker_side should be 'no',
    yes_price = 0.72

If the tape disagrees, my convention is inverted and every fill-rate number built on
it would be wrong.
"""
import json, urllib.request, time

BASE = "https://api.elections.kalshi.com/trade-api/v2"

def get(path):
    for a in range(4):
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "probe/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except Exception as e:
            if a == 3:
                raise
            time.sleep(1.5 * (a + 1))

CASES = [
    ("KXCLARITYVOTE-26JUL-AUG08", "2026-07-26T03:56:53", 0.51, "yes", "we bought NO@0.49"),
    ("KXMUSKNW-26JUL31-T700",     "2026-07-26T04:02:16", 0.72, "no",  "we bought YES@0.72"),
]

for ticker, when, exp_yes_px, exp_taker, note in CASES:
    d = get(f"/markets/trades?ticker={ticker}&limit=200")
    tr = d.get("trades", [])
    near = [x for x in tr if x["created_time"][:19] >= when[:19][:-2] + "00"
            and x["created_time"][:16] == when[:16]]
    print(f"\n=== {ticker}  ({note}) ===")
    print(f"  expect taker_side={exp_taker!r}, yes_price={exp_yes_px}")
    if not near:
        near = sorted(tr, key=lambda x: abs(
            (x["created_time"][:19]).__hash__()))[:0]
        # fall back: print the trades closest in time textually
        cand = sorted(tr, key=lambda x: abs(
            int(x["created_time"][11:13]) * 3600 + int(x["created_time"][14:16]) * 60
            + int(x["created_time"][17:19])
            - (int(when[11:13]) * 3600 + int(when[14:16]) * 60 + int(when[17:19]))))
        near = [c for c in cand if c["created_time"][:10] == when[:10]][:4]
    for x in near[:6]:
        print("   TAPE", x["created_time"], "taker_side=", x["taker_side"],
              "taker_book_side=", x["taker_book_side"],
              "yes_px=", x["yes_price_dollars"], "ct=", x["count_fp"])
