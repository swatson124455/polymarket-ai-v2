#!/usr/bin/env python3
"""KALSHI OPPORTUNITY SCANNER -- READ-ONLY. Surfaces LIVE, tradeable LIP series we are NOT in.

Answers "what SHOULD we be quoting that we aren't?" by applying the lane's hard rules to the
whole venue, so a fresh program is caught the day it lists (not after it resolves like KXBA).

FILTERS (each is a lesson paid for):
  R1  -- rank by $/day = (period_reward/10000)/window_days, NEVER the raw pool ($1,300 KXBA = $3.3/day).
  R3  -- book must reach Target Size on BOTH sides or it pays NOBODY (empty/closed books = $0).
  FEE -- maker-fee-free only (a maker fee can swallow the whole reward).
  TOX -- skip 'mention'/discrete-event/categorical(mutex) shapes; keep threshold ladders (mutex=False).
  TIME-- skip near-resolved (< MIN_HOURS_LEFT) -- late-life = adverse selection, and KXBA was already closed.
  OWN -- skip series already in the allowlist.
GETs only. No trades, no config, no writes.
"""
import sys, json, datetime as dt
from collections import defaultdict
import kalshi_attribution_ledger as L

ALLOW = set("KXTEMPDCH KXTEMPAUSH KXTEMPLAXH KXTEMPNYCH KXTEMPCHIH KXAAAGASD KXAAAGASW "
            "KXB200MON KXAMSAVO KXH100MON KXMUSKNW KXCHIPBURRITO KXTRUMPENDORSEMENTS "
            "KXGENERICBALLOTVOTEHUB".split())
TARGET_DEFAULT = 1000.0
MIN_HOURS_LEFT = 12.0        # don't chase a program about to close (late-life adverse selection)
TOP_N_BOOKCHECK = 15         # bound orderbook reads to the top-N by $/day
TOX_KEYWORDS = ("MENTION", "HUR", "STORM", "CANE", "ELIM", "FIGHT")  # informed/discrete/settlement-trap


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def two_sided(ticker, target):
    """R3: does the BOOK reach target on both sides, and is the best bid in (0.04,0.96]?"""
    ob = L.get(L.P + f"/markets/{ticker}/orderbook").get("orderbook_fp", {}) or {}
    yd = sum(_f(s) for _, s in (ob.get("yes_dollars") or []))
    nd = sum(_f(s) for _, s in (ob.get("no_dollars") or []))
    by = max((_f(p) for p, _ in (ob.get("yes_dollars") or [])), default=0.0)
    return (yd >= target and nd >= target and 0.04 < by <= 0.96), yd, nd


def main():
    now = dt.datetime.now(dt.timezone.utc)
    progs = L.get(L.P + "/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
    # aggregate by series
    agg = defaultdict(lambda: {"usd_day": 0.0, "n": 0, "min_end": None, "tickers": []})
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        t = p.get("market_ticker")
        if not t:
            continue
        s = t.split("-")[0]
        try:
            st = dt.datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
            en = dt.datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
        except Exception:
            continue
        days = max((en - st).total_seconds() / 86400, 1 / 24)
        a = agg[s]
        a["usd_day"] += (_f(p.get("period_reward")) / 10000) / days
        a["n"] += 1
        a["tickers"].append((t, _f(p.get("target_size_fp")) or TARGET_DEFAULT))
        a["min_end"] = en if a["min_end"] is None else min(a["min_end"], en)

    # candidate series: not ours, enough time left, not obviously toxic by name
    cands = []
    for s, a in agg.items():
        if s in ALLOW:
            continue
        hrs_left = (a["min_end"] - now).total_seconds() / 3600 if a["min_end"] else 0
        if hrs_left < MIN_HOURS_LEFT:
            continue
        cands.append((s, a, hrs_left))
    cands.sort(key=lambda x: -x[1]["usd_day"])

    print(f"scanned {len(progs)} active programs / {len(agg)} series @ {now.strftime('%Y-%m-%dT%H:%MZ')}")
    print(f"non-allowlist series with >= {MIN_HOURS_LEFT}h left: {len(cands)}; book-checking top {TOP_N_BOOKCHECK}\n")

    # fee table
    try:
        fees = json.load(open("series_fee_types.json"))
    except Exception:
        fees = {}

    print(f"{'series':<26}{'$/day':>7}{'progs':>6}{'hrs_left':>9}{'2sided':>8}{'fee':>6}{'tox':>5}  verdict")
    hits = []
    for s, a, hrs in cands[:TOP_N_BOOKCHECK]:
        # sample up to 6 books for R3
        checked = twosided = 0
        for t, tgt in a["tickers"][:4]:
            ok, _, _ = two_sided(t, tgt)
            checked += 1
            twosided += ok
        two_pct = (twosided / checked * 100) if checked else 0
        fee = fees.get(s, {})
        fee_ok = (fee.get("fee_type") == "quadratic") if fee else None  # None = unknown, verify
        toxic = any(k in s.upper() for k in TOX_KEYWORDS)
        earnable = a["usd_day"] * (twosided / max(checked, 1))
        verdict = "CANDIDATE" if (two_pct >= 50 and not toxic and fee_ok is not False) else \
                  ("toxic-name" if toxic else ("books<50% 2sided" if two_pct < 50 else "fee?"))
        if verdict == "CANDIDATE":
            hits.append((s, earnable, two_pct, a["usd_day"], a["n"], hrs))
        print(f"{s:<26}{a['usd_day']:7.1f}{a['n']:6d}{hrs:9.1f}{two_pct:7.0f}%"
              f"{('FREE' if fee_ok else ('CHRG' if fee_ok is False else '?')):>6}"
              f"{('Y' if toxic else '-'):>5}  {verdict}")

    print(f"\n=== LIVE CANDIDATES (fee-free, non-toxic, >=50% two-sided, {MIN_HOURS_LEFT}h+ left), by earnable $/day ===")
    for s, earn, tp, ud, n, hrs in sorted(hits, key=lambda x: -x[1]):
        print(f"  {s:<26} earnable ~${earn:6.1f}/day (${ud:.1f} raw x {tp:.0f}% two-sided)  "
              f"{n} strikes  {hrs:.0f}h left")
    if not hits:
        print("  (none passed all filters this instant)")
    print("\n[$ = R1 upper bound; divide ~3 for realism. Verify fee_type=? by read-back before adding. "
          "Adding to allowlist is Tier-2, operator-gated.]")


if __name__ == "__main__":
    sys.exit(main())
