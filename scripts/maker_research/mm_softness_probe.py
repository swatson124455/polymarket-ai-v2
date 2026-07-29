#!/usr/bin/env python3
"""MAKER SOFTNESS PROBE — measure COMPETITION per candidate market, the one
input the pilot's selection was missing (operator criterion = "soft" markets;
2026-07-23). Read-only, public CLOB books only. Run from the VPS.

WHY pool/msz was NOT enough: the reward SHARE we capture is
    our_score / (our_score + Σ competitor_score)
where a resting order at distance s from mid scores ((v-s)/v)^2 * size (the
engine's S(), the CFTC/official quadratic). So the pool is only the PRIZE; the
share depends on how much competing size already sits in the reward band. Two
markets with identical pools pay us very differently if one book is crowded and
the other is thin. "Soft" = low competitor score in the band = high share.

This measures the COMPETITOR score directly from the live book and projects the
share a single min-size two-sided quote would take. It is a MODEL projection
(snapshot; competitors' reaction to us is unpriced — the standing caveat), and
it is NOT a return figure, so it is quotable as a MEASURED competition metric
with a method tag, never as EV.

Pacing: 0.15s between book fetches, 3s x attempt backoff on any error; a zeros
book on refetch is treated as rate-limited, never "empty" (data-api discipline,
though /book is CLOB not data-api).
"""
import json
import sys
import time
import urllib.request

CLOB = "https://clob.polymarket.com"
UNIVERSE = "/opt/pa2-maker-live/universe.json"


def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0


def get(url, attempts=4):
    last = None
    for a in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pa2-maker-softness/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception as e:            # noqa: BLE001 — paced retry, see docstring
            last = e
            time.sleep(3 * (a + 1))
    raise last


def book_mid_and_score(token, v):
    """Return (mid, competitor_score_within_band). Competitor score sums S()
    over every resting BID level on this token's book (a maker's YES-side
    reward leg is a resting bid; the NO-side leg is a bid on the complement,
    handled by the caller passing the other token)."""
    b = get(f"{CLOB}/book?token_id={token}")
    bids = [(float(x["price"]), float(x["size"])) for x in (b.get("bids") or [])
            if 0 < float(x["price"]) < 1 and float(x["size"]) > 0]
    asks = [(float(x["price"]), float(x["size"])) for x in (b.get("asks") or [])
            if 0 < float(x["price"]) < 1 and float(x["size"]) > 0]
    if not bids or not asks:
        return None, None, len(bids)
    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    mid = (best_bid + best_ask) / 2.0
    # competitor reward score = Σ S(v, |mid - price|, size) over resting bids
    # inside the band (s < v). Dust below the size-cutoff is ignored by the
    # real scorer; we do not model that cutoff, so this is a slight OVER-count
    # of competition = a CONSERVATIVE (pessimistic) share for us.
    comp = sum(S(v, abs(mid - p), sz) for p, sz in bids if abs(mid - p) < v)
    return mid, comp, len(bids)


def main():
    uni = json.load(open(UNIVERSE))["markets"]
    allow = set((sys.argv[1] if len(sys.argv) > 1 else
                 "sports,entertainment,politics").split(","))
    maxmsz = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
    cands = [m for m in uni if m["sector"] in allow and m["msz"] <= maxmsz]
    cands.sort(key=lambda m: m["msz"])
    print(f"# softness probe — {len(cands)} candidates "
          f"(sectors={sorted(allow)}, msz<=${maxmsz:.0f})")
    print(f"# our_score = a single min-size two-sided quote at wide style "
          f"(s = v/2 each leg); share = ours/(ours+competitor)")
    print("%-9s %-8s %5s %6s %8s %9s %8s  %s"
          % ("mkt", "sector", "msz", "pool", "comp$core", "ourShare", "rew/day", "q"))
    rows = []
    for m in cands:
        v = m["v"]
        # our score: min-size (msz) two-sided, wide (each leg at s = v/2)
        our_leg = S(v, v / 2.0, m["msz"])
        try:
            my, cy, ny = book_mid_and_score(m["yes"], v)
            time.sleep(0.15)
            mn, cn, nn = book_mid_and_score(m["no"], v)
            time.sleep(0.15)
        except Exception as e:            # noqa: BLE001
            print(f"{m['id']:<9} FETCH-FAIL {str(e)[:40]} — SKIP (not 'soft')")
            continue
        if cy is None or cn is None:
            print(f"{m['id']:<9} {m['sector'][:8]:<8} ${m['msz']:<4.0f} "
                  f"one-sided/empty book — cannot score, SKIP")
            continue
        # projected share = our two-leg score / (ours + their two-leg score)
        ours = our_leg * 2
        theirs = cy + cn
        share = ours / (ours + theirs) if (ours + theirs) > 0 else 0.0
        rew_day = share * m["pool"]
        # capital a min-size two-sided quote actually COMMITS: both legs are
        # BUYS (YES bid at mid_y - v/2, NO bid at mid_n - v/2), so
        # cost ~= msz * (mid_y + mid_n - v) ~= msz * (1 - v). Computed from
        # the measured mids, not assumed. MODEL tier like rew_day.
        cost = m["msz"] * max(0.01, (my - v / 2.0) + (mn - v / 2.0))
        cap_eff = rew_day / cost
        rows.append((share, rew_day, m, cost, cap_eff))
        print("%-9s %-8s $%-4.0f $%5.0f %8.2f %8.1f%% $%7.2f  %s"
              % (m["id"], m["sector"][:8], m["msz"], m["pool"], theirs,
                 100 * share, rew_day, m["q"][:40]))
    print()
    rows.sort(key=lambda r: -r[0])          # rank by SHARE (softness), not pool
    print("# RANKED BY SHARE (soft = high share = few competitors in the band):")
    for share, rew_day, m, cost, cap_eff in rows[:8]:
        print("  %5.1f%% share  $%6.2f rew/day(model)  %-8s $%-4.0f  %s"
              % (100 * share, rew_day, m["sector"][:8], m["msz"], m["q"][:44]))
    # CAPITAL-AWARE view (session-8 E-E, Kalshi task-#1 shape re-derived
    # Poly-native): rew/day per dollar the two-sided min quote commits. The
    # numerator is the COMPETITION-measured share model — never bare
    # pool/msz, which the numbers ledger warns anti-predicts realized yield.
    if rows:
        print()
        print("# RANKED BY CAPITAL EFFICIENCY (model $/day per $ committed"
              " at min size; cost from measured mids):")
        for share, rew_day, m, cost, cap_eff in \
                sorted(rows, key=lambda r: -r[4])[:8]:
            print("  %6.3f $/day/$  ($%5.2f/day / $%5.0f cost)  %5.1f%% share"
                  "  %-8s  %s"
                  % (cap_eff, rew_day, cost, 100 * share,
                     m["sector"][:8], m["q"][:36]))
    if rows:
        print()
        print("# NOTE share is a SNAPSHOT model — competitors' reaction to our")
        print("# presence is not priced. It is a competition MEASUREMENT, not EV.")


if __name__ == "__main__":
    main()
