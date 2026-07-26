"""REDUCE-QUOTE FEASIBILITY PROBE — can the proposed doctrine's reducing quote actually rest?

NEW FILE. READ-ONLY. Public Kalshi API only (no keys, no orders), >=0.35s spacing.

The INVENTORY DOCTRINE v1 proposal severs the F1 deadlock with:
    reduce_price = reference_reducing_side + TICK * through_ticks(tau)
and proves termination from "one tick through the touch gives price priority, so the first
opposite-direction taker fills us".

That argument has two silent preconditions. This probe measures both on the live allowlist:

  P1  a REFERENCE EXISTS on the reducing side.  If we are long YES the reducing quote is a NO
      bid; with zero resting NO bids there is no reference, the formula is undefined, and the
      deployed guard (`_priceable`, maker_kalshi_quoter.py:449) returns [] -- i.e. the exact
      at_ref_pct=0.0 state the doctrine claims to have eliminated.

  P2  one tick through the touch is PASSIVE.  A NO bid at no_bid + k ticks crosses when
      k >= spread_ticks = (1 - yes_bid - no_bid)/0.01.  Where the spread is 1 tick, the
      "1 tick through" reducing quote is MARKETABLE -- it is a taker flatten, the thing the
      doctrine's own tape study measured as loss-making at every horizon.

NOT COVERED
  * bid PRESENCE only, from the markets endpoint (yes_bid/no_bid); this does not test whether
    depth reaches Target Size (R3). Empty-side counts are therefore a LOWER bound on
    "cannot rest a qualifying reducing quote".
  * one instant, one allowlist (7 series). Book state is strongly time-of-day dependent
    (canon SS M6 measured a much worse overnight regime).
  * says nothing about fill probability given a resting order.
"""
import json
import statistics as st
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone

PUB = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",
          "KXAAAGASD", "KXAAAGASW"]
TICK = 0.01


def get(url):
    err = None
    for _ in range(3):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=25).read())
        except Exception as e:                       # noqa: BLE001 - probe, retry on anything
            err = e
            time.sleep(1.0)
    raise err


def markets(series):
    out, cur = [], None
    while True:
        u = f"{PUB}/markets?series_ticker={series}&status=open&limit=1000"
        if cur:
            u += f"&cursor={cur}"
        d = get(u)
        out += d.get("markets", [])
        cur = d.get("cursor")
        time.sleep(0.35)
        if not cur:
            break
    return out


def main():
    print(datetime.now(timezone.utc).isoformat(), "UTC  (public read-only)")
    spreads, tot, ey_t, en_t, ea_t = [], 0, 0, 0, 0
    print(f"{'series':12s} {'n':>4} {'no YES bid':>11} {'no NO bid':>10} {'either':>7} {'either%':>8}")
    for s in SERIES:
        ms = markets(s)
        ey = en = ea = 0
        for m in ms:
            yb = float(m.get("yes_bid_dollars") or 0)
            nb = float(m.get("no_bid_dollars") or 0)
            if yb <= 0:
                ey += 1
            if nb <= 0:
                en += 1
            if yb <= 0 or nb <= 0:
                ea += 1
            else:
                spreads.append(round((1.0 - yb - nb) / TICK))
        tot += len(ms); ey_t += ey; en_t += en; ea_t += ea
        pct = (ea / len(ms) * 100) if ms else 0.0
        print(f"{s:12s} {len(ms):>4} {ey:>11} {en:>10} {ea:>7} {pct:>7.1f}%")
    print(f"{'TOTAL':12s} {tot:>4} {ey_t:>11} {en_t:>10} {ea_t:>7} {ea_t/tot*100:>7.1f}%")

    print(f"\nP1  reducing quote has NO reference in {ea_t}/{tot} = {ea_t/tot:.1%} of contracts")
    print(f"    long-YES case (no NO bid): {en_t}/{tot} = {en_t/tot:.1%}")
    print(f"    long-NO  case (no YES bid): {ey_t}/{tot} = {ey_t/tot:.1%}")

    spreads.sort()
    print(f"\nP2  spread ticks over the {len(spreads)} two-sided-bid contracts")
    print("    histogram:", dict(sorted(Counter(spreads).items())))
    print(f"    median {st.median(spreads):.0f}  p25 {spreads[len(spreads)//4]}  "
          f"p75 {spreads[3*len(spreads)//4]}")
    x1 = sum(1 for x in spreads if x <= 1) / len(spreads)
    x2 = sum(1 for x in spreads if x <= 2) / len(spreads)
    print(f"    spread <= 1 tick -> a 1-tick-through reduce order CROSSES (taker): {x1:.1%}")
    print(f"    spread <= 2 ticks: {x2:.1%}")
    print(f"    otherwise the reduce quote improves the bid inside a median "
          f"{st.median(spreads):.0f}-tick spread and still requires a taker to cross it.")


if __name__ == "__main__":
    main()
