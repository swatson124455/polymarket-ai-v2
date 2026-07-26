"""ENTRY-TIMING STUDY — is adverse selection a function of WHEN in a market's life we fill?

NEW FILE. Read-only. Local caches only (receipts CSV + cached candlesticks for close times).

The inventory doctrine can only change the SIGN of a market's EV through SELECTION (which
series, which contracts, which part of the market's life), because every within-market lever
scales reward and fills together:
  * SIZE      cost/control ~= 1.00  (kalshi_skew_cost_study.py)
  * PRICE     cost/control  1.13-5.0 (ditto -- strictly worse than size)
  * QUEUE     reward-per-fill-risk flat in depth (kalshi_queue_allocation_study.py)
So this measures the one axis that is left: the adverse-selection cost `a` as a function of
time-to-close at the moment of the fill, and of the fraction of the market's life elapsed.

`a` here = realised loss per contract on that lot (receipt-grade, fees included).

NOT COVERED
  * 244 lots / 54 contracts / 3 days / 2 families. Late-life buckets are thin -- n is printed.
  * lot P&L is realised at whatever exit actually happened (mostly settlement), so this is the
    cost of a fill UNDER THE DEPLOYED exit rule, not under a counterfactual one.
  * market "life" is approximated by the span of cached minute candles, which begins at first
    quote activity, not at listing. For hourly temp that is close; for gas dailies it can
    understate life and therefore overstate the elapsed fraction.
"""
import csv
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KDATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "kdata")
CSVP = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")


def fam(t):
    return "TEMP" if t.startswith("KXTEMP") else "GAS"


def parse(ts):
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def candle_span():
    span = {}
    for f in glob.glob(os.path.join(KDATA, "candles", "*.json")):
        d = json.load(open(f))
        cs = d.get("candlesticks") or []
        if not cs:
            continue
        ts = [int(c["end_period_ts"]) for c in cs]
        span[d["ticker"]] = (datetime.fromtimestamp(min(ts), timezone.utc),
                             datetime.fromtimestamp(max(ts), timezone.utc))
    return span


def main():
    span = candle_span()
    lots = []
    with open(CSVP, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] != "trade":
                continue
            t = r["market_ticker"]
            if t not in span:
                continue
            s, e = span[t]
            op = parse(r["open_timestamp"])
            life = (e - s).total_seconds() / 60.0
            if life <= 0:
                continue
            lots.append(dict(t=t, fam=fam(t), qty=float(r["quantity_fp"]),
                             pnl=float(r["realized_pnl_with_fees_dollars"]),
                             px=float(r["entry_price_dollars"]),
                             ttc=(e - op).total_seconds() / 60.0,
                             elapsed=(op - s).total_seconds() / 60.0 / life,
                             life=life))
    print(f"{len(lots)} lots with cached candles "
          f"({len({x['t'] for x in lots})} contracts)")
    for f in ("GAS", "TEMP"):
        sel = [x for x in lots if x["fam"] == f]
        lv = sorted(x["life"] for x in sel)
        print(f"\n{'='*88}\n{f}: {len(sel)} lots, market life median {lv[len(lv)//2]:.0f} min "
              f"(min {lv[0]:.0f}, max {lv[-1]:.0f})")

        print("  by TIME-TO-CLOSE at fill:")
        print(f"    {'bucket (min)':>16} {'lots':>5} {'ct':>7} {'P&L $':>9} "
              f"{'a (c/ct)':>9} {'top-ticker%':>11}")
        for lo, hi in ((0, 20), (20, 45), (45, 90), (90, 240), (240, 10 ** 6)):
            b = [x for x in sel if lo <= x["ttc"] < hi]
            if not b:
                continue
            ct = sum(x["qty"] for x in b)
            pn = sum(x["pnl"] for x in b)
            byt = defaultdict(float)
            for x in b:
                byt[x["t"]] += x["qty"]
            print(f"    {lo:>7}-{hi if hi < 10**6 else 'inf':>8} {len(b):>5} {ct:>7.0f} "
                  f"{pn:>9.2f} {-pn/ct*100:>9.2f} {max(byt.values())/ct:>10.0%}")

        print("  by FRACTION OF MARKET LIFE elapsed at fill:")
        print(f"    {'bucket':>16} {'lots':>5} {'ct':>7} {'P&L $':>9} "
              f"{'a (c/ct)':>9} {'top-ticker%':>11}")
        for lo, hi in ((0, .25), (.25, .5), (.5, .75), (.75, 1.01)):
            b = [x for x in sel if lo <= x["elapsed"] < hi]
            if not b:
                continue
            ct = sum(x["qty"] for x in b)
            pn = sum(x["pnl"] for x in b)
            byt = defaultdict(float)
            for x in b:
                byt[x["t"]] += x["qty"]
            print(f"    {lo:>7.2f}-{hi:>8.2f} {len(b):>5} {ct:>7.0f} {pn:>9.2f} "
                  f"{-pn/ct*100:>9.2f} {max(byt.values())/ct:>10.0%}")

        print("  by ENTRY PRICE:")
        print(f"    {'bucket':>16} {'lots':>5} {'ct':>7} {'P&L $':>9} {'a (c/ct)':>9}")
        for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
            b = [x for x in sel if lo <= x["px"] < hi]
            if not b:
                continue
            ct = sum(x["qty"] for x in b)
            pn = sum(x["pnl"] for x in b)
            print(f"    {lo:>7.2f}-{hi:>8.2f} {len(b):>5} {ct:>7.0f} {pn:>9.2f} "
                  f"{-pn/ct*100:>9.2f}")


if __name__ == "__main__":
    main()
