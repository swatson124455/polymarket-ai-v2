#!/usr/bin/env python3
"""MAKER P6_tilted vs P0_base READOUT (owed to WB per the forecast-tilt proposal).

PAIRED design: P6 = P0 gates VERBATIM + a bounded quote tilt, run on identical
books/prints in the same process. So on WB-COVERED markets the P0/P6 difference
IS the tilt effect; on uncovered markets P6 is P0 by construction (they are the
null check, not the signal).

ERA GUARD (the trap): P0's ledger carries from the 07-17 02:36:41Z clean era,
P6's from 07-17 21:55:09Z. Any market spanning that boundary gives P0 ~19h of
extra accrual -> a fake P0 win. Weather dailies are date-stamped in the
question, so the primary cut keeps only markets dated >= July 19 (created after
the era start); July 20+ is reported as a robustness cut. Unparseable dates are
EXCLUDED and DISCLOSED (running-tab reading rule 3).

NUMBERS DISCIPLINE (MAKER_NUMBERS_LEDGER.md): rewards accrual `acc` is MODEL
tier -> "model, unverified", never profit. Trading (`net`) is NOISE -> band /
labelled drag only, never a headline point. Concentration (Protocol 14) is
computed BEFORE any pooled number is presented.
"""
import json
import re
import sys
from collections import defaultdict

STATE = "/opt/pa2-maker-sim-v5/state.json"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


def market_date(q):
    """(month, day) parsed from a date-stamped daily question, else None."""
    m = re.search(r"on\s+([A-Za-z]+)\s+(\d{1,2})", q or "")
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    return (mon, int(m.group(2))) if mon else None


def net_of(e, last_mid):
    """NET trading = realized + mark-to-market on the open position. Family
    caveat: the real/unreal SPLIT is misstated when a fill crosses through
    zero; NET is the correct column, so only NET is used."""
    pos, cost, real = e.get("pos") or 0.0, e.get("cost") or 0.0, e.get("real") or 0.0
    mark = pos * last_mid if (last_mid is not None and pos) else 0.0
    return real + mark - cost


def main():
    s = json.load(open(STATE))
    mids = {k.split("|")[0]: (v or {}).get("last_mid")
            for k, v in s.items() if k.endswith("|SH")}
    pol = defaultdict(dict)
    for k, v in s.items():
        if "|" not in k:
            continue
        mid, p = k.split("|", 1)
        if p != "SH":
            pol[p][mid] = v

    p0, p6 = pol.get("P0_base", {}), pol.get("P6_tilted", {})
    covered = [m for m in p6 if p6[m].get("wbp") is not None and m in p0]

    buckets = defaultdict(list)
    undated = 0
    for m in covered:
        e6, e0 = p6[m], p0[m]
        d = market_date(e6.get("q"))
        if d is None:
            undated += 1
            continue
        lm = mids.get(m)
        buckets["all"].append((m, d, e0, e6, lm))

    def cut(rows, min_day):
        return [r for r in rows if r[1] >= (7, min_day)]

    print("=" * 78)
    print("P6_tilted vs P0_base — WB FORECAST TILT READOUT")
    print("=" * 78)
    print("WB-covered markets present in both policies: %d" % len(covered))
    print("  excluded, date unparseable (disclosed): %d" % undated)
    print("  NOTE uncovered markets are omitted: P6 == P0 there by construction")
    print()

    for label, min_day in (("PRIMARY  (dated >= Jul 19)", 19),
                           ("ROBUST   (dated >= Jul 20)", 20)):
        rows = cut(buckets["all"], min_day)
        if not rows:
            print("%s: no markets" % label)
            continue
        acc0 = sum((r[2].get("acc") or 0.0) for r in rows)
        acc6 = sum((r[3].get("acc") or 0.0) for r in rows)
        cap = sum((r[3].get("msz") or 0.0) for r in rows)
        net0 = sum(net_of(r[2], r[4]) for r in rows)
        net6 = sum(net_of(r[3], r[4]) for r in rows)
        # Protocol 14: who dominates the REWARDS pool of this cut
        share = sorted(((r[3].get("acc") or 0.0), r[3].get("q", "")[:44])
                       for r in rows)[::-1]
        top1 = 100 * share[0][0] / acc6 if acc6 else 0.0
        top5 = 100 * sum(x[0] for x in share[:5]) / acc6 if acc6 else 0.0
        # tilt actually standing right now (snapshot-only signal)
        tilted_now = sum(1 for r in rows if (r[3].get("tilt_q") or 0.0) != 0.0)

        print("-" * 78)
        print("%s   n=%d markets   capital(msz sum)=$%.0f" % (label, len(rows), cap))
        print("  REWARDS (MODEL, unverified — the only quotable basis)")
        print("    P0_base  acc = $%10.2f" % acc0)
        print("    P6_tilted acc = $%10.2f" % acc6)
        d = acc6 - acc0
        print("    tilt delta    = $%10.2f  (%+.2f%% vs P0)"
              % (d, (100 * d / acc0) if acc0 else 0.0))
        print("  CONCENTRATION (Protocol 14, computed before presenting)")
        print("    top1 = %.1f%% of P6 rewards (%s)" % (top1, share[0][1]))
        print("    top5 = %.1f%%" % top5)
        if top1 >= 50:
            r2 = [r for r in rows if r[3].get("q", "")[:44] != share[0][1]]
            a0 = sum((r[2].get("acc") or 0.0) for r in r2)
            a6 = sum((r[3].get("acc") or 0.0) for r in r2)
            print("    LEAVE-ONE-OUT (top1 >= 50%%): delta = $%.2f (%+.2f%%)"
                  % (a6 - a0, (100 * (a6 - a0) / a0) if a0 else 0.0))
        print("  TRADING (NOISE tier — band/drag only, NEVER a headline)")
        print("    P0 net $%.2f | P6 net $%.2f | tilt drag $%+.2f"
              % (net0, net6, net6 - net0))
        print("  tilt standing at snapshot: %d/%d markets" % (tilted_now, len(rows)))

        # trust-tier segmentation (WB semantics: w=1.0 full, 0.5 damped)
        tiers = defaultdict(list)
        for r in rows:
            tiers[round(r[3].get("wbw") or 0.0, 2)].append(r)
        print("  BY WB TRUST TIER (wbw)")
        for w in sorted(tiers, reverse=True):
            tr = tiers[w]
            a0 = sum((x[2].get("acc") or 0.0) for x in tr)
            a6 = sum((x[3].get("acc") or 0.0) for x in tr)
            print("    w=%.2f  n=%3d  P0 $%8.2f  P6 $%8.2f  delta $%+8.2f"
                  % (w, len(tr), a0, a6, a6 - a0))
        print()

    print("=" * 78)
    print("READING RULES: rewards = MODEL accrual (unverified until pilot")
    print("receipts). Trading is NOISE — never added into a headline. Deltas")
    print("are paired on identical books; era guard applied (see docstring).")


if __name__ == "__main__":
    sys.exit(main())
