#!/usr/bin/env python3
"""M1 — VENUE CENSUS. The macro layer: what the VENUE offers, not what one market does.

READ-ONLY. Public endpoints only (no auth, no credentials, no orders, no cancels).
Safe to run while the bot is STOPped/parked — it never touches the portfolio.

WHY THIS EXISTS
  Reward share = our_score / (competing_score + our_score), and reward $ = share x pool.
  So the allocator primitive is, per program: "what does $X of capital buy, as a share of a
  $Z/day pool, given the CURRENT competing depth?" Nothing in the system computes that across
  the venue — selection has only ever ranked markets we already decided to look at.

R1 POOL FORMULA (canon, do NOT change):
  period_reward/10000 IS ALREADY the DAILY PER-MARKET pool. Do NOT divide by window length.
  (Dividing put 56% of programs below Kalshi's documented $10/day floor. See canon note.)

TWO TRUNCATION FOOTGUNS THIS SCRIPT GUARDS EXPLICITLY
  1. ESTABLISHED (probed 2026-07-27): /incentive_programs paginates on `next_cursor`, NOT
     `cursor`. Reading the wrong key yields None, so the walk stops after ONE page and
     silently returns only the first `limit` rows with no error and no truncation signal.
  2. CANON, NOT re-verified here (see the R1 canon note): the `status` filter is said to be
     CASE-SENSITIVE, recognizing only active/closed/upcoming, with any unrecognized value
     silently returning the WHOLE unfiltered history. A direct check on 2026-07-27 was
     INCONCLUSIVE — every status value tried returned exactly the page cap, which cannot
     discriminate. Treat it as unconfirmed and do not rely on the filter alone.
  So this script does not trust either: it walks `next_cursor` to exhaustion AND independently
  re-checks every row's own start/end window against now, reporting any disagreement rather
  than smoothing it over. It refuses to print totals if pagination did not terminate cleanly.
"""
import argparse
import collections
import datetime
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")

import maker_kalshi_quoter as Q
from maker_kalshi_quoter import public_get, _levels, _qualifying_breakdown

PROGRAMS_PATH = "/trade-api/v2/incentive_programs"


def _iso(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_all_programs(status="active", limit=1000, max_pages=400):
    """Full next_cursor walk. Returns (rows, pages, terminated_cleanly)."""
    rows, seen, cur, pages = [], set(), None, 0
    while pages < max_pages:
        path = f"{PROGRAMS_PATH}?limit={limit}"
        if status:
            path += f"&status={status}"
        if cur:
            path += f"&cursor={cur}"
        j = public_get(path)
        batch = j.get("incentive_programs") or []
        fresh = [p for p in batch if p.get("id") not in seen]
        rows.extend(fresh)
        seen |= {p.get("id") for p in batch}
        pages += 1
        cur = j.get("next_cursor") or None
        if not cur or not batch or not fresh:
            return rows, pages, True
    return rows, pages, False


def pool_per_day(p):
    """CANON: period_reward/10000 is ALREADY the daily per-market pool."""
    return float(p.get("period_reward") or 0) / 10000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth-sample", type=int, default=200,
                    help="how many programs (highest pool first) to price for competing depth")
    ap.add_argument("--spacing", type=float, default=0.06, help="seconds between public reads")
    ap.add_argument("--out", default="m1_venue_census.json")
    a = ap.parse_args()

    Q.READ_BUDGET_PER_CYCLE = 10 ** 9
    Q.REQ_SPACING_S = a.spacing
    now = datetime.datetime.now(datetime.timezone.utc)
    print("M1 VENUE CENSUS")
    print("READ_AT_UTC", now.isoformat())

    rows, pages, clean = fetch_all_programs("active")
    print(f"pagination: pages={pages} rows={len(rows)} terminated_cleanly={clean}")
    if not clean:
        print("!! PAGINATION DID NOT TERMINATE — census INCOMPLETE, do not quote totals")
        return 2

    # independent re-check of the venue's own status filter
    live, stale = [], []
    for p in rows:
        try:
            if _iso(p["start_date"]) <= now <= _iso(p["end_date"]):
                live.append(p)
            else:
                stale.append(p)
        except Exception:
            stale.append(p)
    print(f"filter self-check: rows whose OWN window contains now = {len(live)}; "
          f"rows that do NOT = {len(stale)}")
    if stale:
        print("   -> status=active is NOT exact on this read; using the self-checked LIVE set")
    progs = live

    liq = [p for p in progs if (p.get("incentive_type") or "") == "liquidity"]
    print(f"\n=== UNIVERSE (live now) ===")
    print(f"  programs total {len(progs)}   liquidity-type {len(liq)}")
    series = collections.Counter(
        (p.get("market_ticker") or "").split("-")[0] for p in liq)
    pools = [pool_per_day(p) for p in liq]
    total_pool = sum(pools)
    print(f"  distinct series {len(series)}")
    print(f"  ADDRESSABLE DAILY POOL  ${total_pool:,.2f}/day  across {len(liq)} markets")
    if pools:
        print(f"  pool/market: min ${min(pools):.2f}  median ${st.median(pools):.2f}  "
              f"max ${max(pools):.2f}")

    # window length + target size + df distributions (macro shape of the venue)
    wins, tgts, dfs = [], [], []
    for p in liq:
        try:
            wins.append((_iso(p["end_date"]) - _iso(p["start_date"])).total_seconds() / 3600.0)
        except Exception:
            pass
        try:
            tgts.append(float(p.get("target_size_fp") or 0))
        except Exception:
            pass
        dfs.append(float(p.get("discount_factor_bps") or 0) / 10000.0)
    def bucketize(vals, edges, label):
        print(f"\n  {label}")
        for lo, hi, nm in edges:
            n = sum(1 for v in vals if lo <= v < hi)
            if n:
                print(f"    {nm:<10} n={n:>5}  ({100.0*n/len(vals):>5.1f}%)")
    if wins:
        bucketize(wins, ((0, 6, "0-6h"), (6, 24, "6-24h"), (24, 96, "1-4d"),
                         (96, 336, "4-14d"), (336, 1e9, "14d+")), "program-window length:")
    if tgts:
        print(f"\n  target_size: min {min(tgts):.0f} median {st.median(tgts):.0f} "
              f"max {max(tgts):.0f}")
        print(f"  discount_factor: {dict(collections.Counter(round(d,4) for d in dfs))}")

    print(f"\n  TOP 15 SERIES BY TOTAL DAILY POOL:")
    by_series = collections.defaultdict(float)
    cnt_series = collections.Counter()
    for p in liq:
        s = (p.get("market_ticker") or "").split("-")[0]
        by_series[s] += pool_per_day(p)
        cnt_series[s] += 1
    for s, v in sorted(by_series.items(), key=lambda x: -x[1])[:15]:
        print(f"    {s:<28} ${v:>10,.2f}/day  over {cnt_series[s]:>4} markets  "
              f"({100.0*v/total_pool:>4.1f}% of venue)")

    # ---- competing-depth pricing: what does capital BUY, per program ----
    sample = sorted(liq, key=pool_per_day, reverse=True)[:a.depth_sample]
    print(f"\n=== CAPITAL EFFICIENCY (competing R4 depth priced on {len(sample)} "
          f"highest-pool live programs) ===")
    out, errs, r3_fail = [], 0, 0
    for p in sample:
        t = p.get("market_ticker")
        tgt = float(p.get("target_size_fp") or 0)
        df = float(p.get("discount_factor_bps") or 0) / 10000.0
        try:
            ob = (public_get(f"/trade-api/v2/markets/{t}/orderbook") or {}).get("orderbook_fp") or {}
        except Exception:
            errs += 1
            continue
        yl, _ = _levels(ob.get("yes_dollars") or [])
        nl, _ = _levels(ob.get("no_dollars") or [])
        dy, _cy, refy, _lqy, qy = _qualifying_breakdown(yl, tgt, df)
        dn, _cn, refn, _lqn, qn = _qualifying_breakdown(nl, tgt, df)
        if not (qy and qn):           # R3: a snapshot pays only if BOTH sides qualify
            r3_fail += 1
            continue
        pool = pool_per_day(p)
        # rest S contracts at reference on BOTH sides (df^0 = 1 -> our score == S)
        for S in (20.0, 100.0):
            share = 0.5 * (S / (dy + S) + S / (dn + S))
            capital = S * ((refy or 0) + (refn or 0))      # both legs, at the touch
            if capital <= 0:
                continue
            out.append({"ticker": t, "series": t.split("-")[0], "pool_day": pool,
                        "S": S, "share": share, "reward_day": share * pool,
                        "capital": capital,
                        "reward_per_100cap_day": share * pool / capital * 100.0,
                        "df_total_yes": dy, "df_total_no": dn,
                        "ref_yes": refy, "ref_no": refn, "target": tgt})
    print(f"  priced OK: {len(set(o['ticker'] for o in out))} markets   "
          f"R3-fail (one side cannot reach Target): {r3_fail}   read errors: {errs}")

    for S in (20.0, 100.0):
        sub = [o for o in out if o["S"] == S]
        if not sub:
            continue
        rp = [o["reward_per_100cap_day"] for o in sub]
        print(f"\n  --- resting {S:.0f} ct per side at reference ---")
        print(f"    reward $/day per $100 capital: median {st.median(rp):.4f}  "
              f"mean {st.mean(rp):.4f}  max {max(rp):.4f}")
        print(f"    TOP 12 by capital efficiency:")
        for o in sorted(sub, key=lambda z: -z["reward_per_100cap_day"])[:12]:
            print(f"      {o['ticker']:<34} ${o['reward_day']:>7.2f}/day  "
                  f"share {o['share']*100:>5.2f}%  cap ${o['capital']:>7.2f}  "
                  f"=> ${o['reward_per_100cap_day']:>7.3f}/day per $100  "
                  f"pool ${o['pool_day']:.0f}")

    res = {"read_at_utc": now.isoformat(), "pages": pages,
           "programs_live": len(progs), "liquidity_live": len(liq),
           "series_live": len(series), "addressable_pool_day": total_pool,
           "filter_selfcheck_stale_rows": len(stale),
           "depth_sample": len(sample), "r3_fail": r3_fail, "read_errors": errs,
           "by_series_pool_day": dict(sorted(by_series.items(), key=lambda x: -x[1])),
           "priced": out}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
