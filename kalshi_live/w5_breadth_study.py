#!/usr/bin/env python3
"""W5 — BREADTH-CAPACITY STUDY (scale plan B3, read-only).

QUESTION: how many concurrent qualifying quiet markets exist inside the 8-day horizon,
and how much capital do they absorb at the bot's own sizing before breadth runs out?
This bounds how much of the operator's "several thousand" can actually work (pools are
fixed per market, so scale = breadth, not size-in-market — scale plan §1).

METHOD (all public endpoints, frozen output):
  1. Full active incentive_programs scan (the quoter's own pagination pattern).
  2. Horizon filter: program end_date within --horizon-days (default 8). Program end is
     the payout clock the canon established; market close can differ — label INFERRED.
  3. For up to --sample tickers spread across series: pull the public orderbook and the
     market row; apply the bot's OWN entry gates (spread <= 8 ticks, both sides priced,
     ref inside the 4c-96c band, 24h volume <= 1000 ct) and the quiet preference.
  4. For every QUALIFYING market, capital absorbed = est_commit_usd (the exact
     _capped_join-aligned model from kalshi_capital_rank) at live caps ($45/market,
     50 ct inventory ceiling).
  5. Report: qualifying count (sample and extrapolated-with-denominator), pool $/day
     covered, and the capital-absorption curve at $350 / $1k / $2.5k.

Read-only. Writes only under --outdir (default /tmp/w5).
"""
import argparse
import collections
import datetime as dt
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TICK = 0.01


def harvest_active(public_get):
    progs, cursor, pages = [], "", 0
    while pages < 60:
        pages += 1
        d = public_get("/trade-api/v2/incentive_programs?status=active&limit=1000"
                       + (f"&cursor={cursor}" if cursor else ""))
        rows = d.get("incentive_programs") or []
        progs += rows
        cursor = d.get("next_cursor") or d.get("cursor") or ""
        if not rows or not cursor:
            break
        time.sleep(0.2)
    return progs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/w5")
    ap.add_argument("--horizon-days", type=float, default=8.0)
    ap.add_argument("--sample", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20260805)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    from kalshi_capital_rank import est_commit_usd
    import urllib.request

    def public_get(path):
        # PLAIN fetch — the quoter's public_get spends the live read budget and the first
        # run exhausted it 153/250 (bug, 2026-08-05). A study must never touch that budget.
        req = urllib.request.Request("https://api.elections.kalshi.com" + path,
                                     headers={"User-Agent": "w5-breadth-study"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    now = dt.datetime.now(dt.timezone.utc)
    progs = harvest_active(public_get)
    horizon = now + dt.timedelta(days=a.horizon_days)

    def _iso(s):
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))

    in_h = []
    for r in progs:
        try:
            if now <= _iso(r["end_date"]) <= horizon:
                in_h.append(r)
        except Exception:
            continue
    pool_by_ticker = {}
    for r in in_h:
        t = r.get("market_ticker")
        if t:
            pool_by_ticker[t] = pool_by_ticker.get(t, 0.0) + \
                float(r.get("period_reward") or 0) / 10000.0
    tickers = sorted(pool_by_ticker)
    per_series = collections.defaultdict(list)
    for t in tickers:
        per_series[t.split("-")[0]].append(t)
    # sample breadth-first across series so one giant ladder can't eat the budget
    random.seed(a.seed)
    order, idx = [], 0
    keys = sorted(per_series)
    while len(order) < min(a.sample, len(tickers)):
        added = False
        for s in keys:
            lst = per_series[s]
            if idx < len(lst):
                order.append(lst[idx])
                added = True
                if len(order) >= min(a.sample, len(tickers)):
                    break
        if not added:
            break
        idx += 1

    qual, rows = [], []
    for i, t in enumerate(order):
        row = {"ticker": t, "pool_usd_day": round(pool_by_ticker[t], 2)}
        try:
            mk = public_get(f"/trade-api/v2/markets/{t}").get("market") or {}
            # venue shape (canon): orderbook_fp with *_dollars string pairs
            ob = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
            yes = [(float(p), float(sz)) for p, sz in (ob.get("yes_dollars") or [])]
            no = [(float(p), float(sz)) for p, sz in (ob.get("no_dollars") or [])]
            by = max((p for p, _ in yes), default=None)
            bn = max((p for p, _ in no), default=None)
            row["vol24h"] = float(mk.get("volume_24h") or 0)
            if by is None or bn is None:
                row["skip"] = "one_sided"
            else:
                spread_ticks = round((1.0 - by - bn) / TICK)
                row["spread_ticks"] = spread_ticks
                row["ref_yes"] = by
                if not (0.04 <= by <= 0.96):
                    row["skip"] = "price_band"
                elif spread_ticks > 8:
                    row["skip"] = "wide"
                elif row["vol24h"] > 1000:
                    row["skip"] = "hot"
                else:
                    row["commit_usd"] = round(est_commit_usd(by, 45.0, 50), 2)
                    qual.append(row)
        except Exception as e:
            row["skip"] = f"err:{e!r}"[:60]
        rows.append(row)
        time.sleep(0.12)
        if (i + 1) % 50 == 0:
            print(f"  sampled {i+1}/{len(order)} qual={len(qual)}", file=sys.stderr)

    n_s, n_q = len(rows), len(qual)
    frac = n_q / n_s if n_s else 0.0
    est_universe = frac * len(tickers)
    commit = sorted((r["commit_usd"] for r in qual), reverse=True)
    pool_q = sum(r["pool_usd_day"] for r in qual)
    avg_commit = (sum(commit) / len(commit)) if commit else 0.0
    out = {"schema": 1, "read_ts": now.isoformat(),
           "programs_active": len(progs), "in_horizon": len(in_h),
           "tickers_in_horizon": len(tickers), "series_in_horizon": len(per_series),
           "sampled": n_s, "qualifying_in_sample": n_q,
           "qualifying_frac": round(frac, 4),
           "est_qualifying_universe": round(est_universe, 1),
           "sample_pool_usd_day_qualifying": round(pool_q, 2),
           "avg_commit_usd": round(avg_commit, 2),
           "capital_rungs": {str(c): (round(c / avg_commit, 1) if avg_commit else None)
                              for c in (350, 1000, 2500)},
           "rows": rows}
    path = os.path.join(a.outdir, "w5_breadth.json")
    json.dump(out, open(path, "w"), indent=1, sort_keys=True)
    print(f"active={len(progs)} in_horizon_tickers={len(tickers)} "
          f"series={len(per_series)}")
    print(f"sample={n_s} qualifying={n_q} ({frac:.1%}) -> est universe "
          f"~{est_universe:.0f} markets (extrapolation from the sample fraction; "
          f"denominator = {len(tickers)} in-horizon tickers)")
    print(f"qualifying sample pool ${pool_q:.2f}/day; avg commit ${avg_commit:.2f} "
          f"-> markets needed: $350={350/avg_commit:.0f} $1k={1000/avg_commit:.0f} "
          f"$2.5k={2500/avg_commit:.0f}" if avg_commit else "no qualifying markets")
    print("wrote", path)


if __name__ == "__main__":
    main()
