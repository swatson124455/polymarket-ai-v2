#!/usr/bin/env python3
"""Walk-forward copy-trader grader — hire on the résumé, fire on proven decay,
score ONLY what copying earned after each hire.

THE RULE BEING TESTED (locked with the operator, 2026-07-10 — this is the
deployable strategy itself, not a proxy):
  At each monthly review date, using ONLY information that existed then:
    HIRE : a trader joins the copy list when their LIFETIME record shows
           >= --min-markets-hire distinct resolved markets (first BUY per
           market, 2-98c), positive average edge, bootstrap
           P(edge>0) >= --p-hire, and the record spans >= --min-span-days
           (a hot single tournament cannot qualify anyone).
    FIRE : a listed trader is dropped only when their RECENT record (trailing
           --fire-window-days, min --fire-min-markets markets) shows
           bootstrap P(edge<0) >= --p-fire — the same statistical bar pointed
           the other way. A GOAT's cold month is not statistically convincing
           and survives; sustained real decay is, and doesn't. The firing test
           never sees the old glory, so a monster past cannot shield present
           bleed (operator's $1M-then-bleed objection). A fired trader
           re-enters only when the recent decay signal clears.
    GRADE: the portfolio's bets are the FIRST BUYS listed traders make while
           on the list. Nothing before a trader's hire date ever counts.
           Score = pooled market-clustered edge of those bets (o - price,
           prob-points per contract), plus per-interval consistency.

WHY THIS SHAPE (operator objections it answers):
  * Improvers are caught: a bad-start/good-finish trader is hired the moment
    the cumulative record turns convincing, and graded only from then on.
  * GOATs aren't churned: hire evidence is the whole lifetime book — one bad
    month barely moves 300 markets of history.
  * Has-beens are cut: the fire test looks only at recent form, so lifetime
    winnings can't shield years of bleed.
  * No peeking, twice over: rosters at time t use only bets ENTERED before t
    whose outcomes were KNOWN by t (market resolved_at <= t; long-dated
    politics bets don't count as evidence before they resolved). Entries
    lacking a resolved_at timestamp fall back to entry time and are COUNTED
    and reported (a mild optimism whose size is printed, not hidden).

PRE-REGISTERED PRIMARY (locked in code before the first data run):
  pooled walk-forward portfolio edge over the VOL-SOURCED, NON-TRUNCATED
  universe (volume-board membership selects on activity, not outcomes — the
  PNL board conditions on outcomes and stays descriptive), requiring
  P(edge>0) >= --p-min on >= --min-markets distinct markets with >=
  --min-traders distinct traders ever rostered, AND positive pooled edge on
  BOTH robustness grids (review dates shifted +/- --grid-shift-days).
  FAIL-TERMINAL only if the bootstrap upper bound sits below --econ-floor;
  otherwise INCONCLUSIVE. Everything else (all-trader portfolio, categories,
  per-trader contributions) is DESCRIPTIVE.

DATA: runs entirely off find_copyable_traders.py's per-address history cache
(no new API pulls except one cheap leaderboard fetch to tag VOL/PNL sources)
plus read-only markets lookups. Run it AFTER the deepen re-run completes so
the whales' histories are full.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc \
      venv/bin/python /tmp/mbpc/scripts/walkforward_copy_traders.py \
      --cache /tmp/copyable_cache | tee /tmp/walkforward.log
    ... --self-test          # offline mechanics check, no DB/network

Provenance (CLAUDE.md Forbidden Patterns 8/9): cite these numbers with their
roster sizes, market counts, and the PRIMARY qualifier. A portfolio built on
3 rostered traders has not proven a strategy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_copyable_traders as fc  # noqa: E402  (tested shared helpers)

GRID_ANCHOR = datetime(2025, 1, 1)   # locked, data-independent review grid


# ── Pure, offline-testable core ──────────────────────────────────────────────
def known_time(entry_ts: datetime, resolved_at: Optional[datetime]) -> datetime:
    """When the OUTCOME of a bet became knowable. resolved_at when recorded;
    fallback = entry time (mild optimism, counted and reported by the caller)."""
    if resolved_at is not None and resolved_at > entry_ts:
        return resolved_at
    return entry_ts


def review_dates(t_min: datetime, t_max: datetime, step_days: int,
                 anchor: datetime = GRID_ANCHOR, shift_days: int = 0) -> list[datetime]:
    """Fixed grid from a locked anchor (never data-chosen), optionally shifted
    for the robustness grids."""
    a = anchor + timedelta(days=shift_days)
    k = max(0, math.floor((t_min - a).days / step_days))
    out = []
    t = a + timedelta(days=k * step_days)
    while t <= t_max + timedelta(days=step_days):
        if t >= t_min:
            out.append(t)
        t += timedelta(days=step_days)
    return out


def hire_ok(evidence: list[dict], t: datetime, cfg, seed: int) -> bool:
    """Lifetime bar at review t. evidence rows: {'_ts','known','marketId','edge'}
    (already one row per market — first-BUY estimand)."""
    past = [e for e in evidence if e["known"] <= t and e["_ts"] <= t]
    if len(past) < cfg.min_markets_hire:
        return False
    ts = [e["_ts"] for e in past]
    if (max(ts) - min(ts)).days < cfg.min_span_days:
        return False
    p, mean, _ = fc.boot_stats([e["edge"] for e in past], cfg.n_boot_roster, seed)
    return mean > 0 and p >= cfg.p_hire


def _fire(evidence: list[dict], t: datetime, cfg, seed: int) -> bool:
    """Recent-decay kill switch at review t — looks ONLY at the trailing
    window, so past glory cannot shield present bleed. Fire iff recent mean
    edge is negative AND the bootstrap says P(recent edge < 0) >= p_fire on
    >= fire_min_markets recent markets."""
    lo = t - timedelta(days=cfg.fire_window_days)
    recent = [e for e in evidence if e["known"] <= t and lo <= e["_ts"] <= t]
    if len(recent) < cfg.fire_min_markets:
        return False
    edges = [e["edge"] for e in recent]
    if sum(edges) >= 0:
        return False
    p_neg, _, _ = fc.boot_stats([-x for x in edges], cfg.n_boot_roster, seed)
    return p_neg >= cfg.p_fire


def walk_forward(per_trader: dict[str, list[dict]], dates: list[datetime],
                 cfg, seed: int) -> dict:
    """Run the hire/fire roster across the review grid; collect portfolio bets.
    Returns {'bets': [(market, edge, cat, trader, interval_idx)],
             'roster_sizes': [...], 'hires': n, 'fires': n,
             'ever_rostered': set}."""
    listed: dict[str, bool] = {a: False for a in per_trader}
    # pre-sort evidence per trader once
    ev = {a: sorted(rows, key=lambda e: e["_ts"]) for a, rows in per_trader.items()}
    ts_sorted = {a: [e["_ts"] for e in rows] for a, rows in ev.items()}
    bets, roster_sizes = [], []
    hires = fires = 0
    ever: set = set()
    for i, t in enumerate(dates[:-1]):
        t_next = dates[i + 1]
        for a, rows in ev.items():
            # cheap pre-check: can't hire with fewer than min entries before t
            n_before = bisect_left(ts_sorted[a], t)
            if not listed[a]:
                if n_before < cfg.min_markets_hire:
                    continue
                if hire_ok(rows, t, cfg, seed + i) and not _fire(rows, t, cfg, seed + i):
                    listed[a] = True
                    hires += 1
                    ever.add(a)
            else:
                if _fire(rows, t, cfg, seed + i):
                    listed[a] = False
                    fires += 1
        roster = [a for a, on in listed.items() if on]
        roster_sizes.append(len(roster))
        for a in roster:
            lo = bisect_left(ts_sorted[a], t)
            hi = bisect_left(ts_sorted[a], t_next)
            for e in ev[a][lo:hi]:
                bets.append((e["marketId"], e["edge"], e["cat"], a, i))
    return {"bets": bets, "roster_sizes": roster_sizes,
            "hires": hires, "fires": fires, "ever_rostered": ever}


def portfolio_stats(bets: list[tuple], n_boot: int, seed: int) -> Optional[dict]:
    if not bets:
        return None
    me = fc.per_market_edges([(b[0], b[1]) for b in bets])
    p, mean, upper = fc.boot_stats(me, n_boot, seed)
    return {"p": p, "edge": mean, "upper95": upper, "n_markets": len(me),
            "n_bets": len(bets), "n_traders": len({b[3] for b in bets})}


def interval_consistency(bets: list[tuple], n_intervals: int) -> tuple[int, int]:
    """(#intervals with positive mean edge, #intervals with any bets)."""
    by_i: dict[int, list[float]] = {}
    for b in bets:
        by_i.setdefault(b[4], []).append(b[1])
    active = [i for i in by_i if by_i[i]]
    pos = sum(1 for i in active if sum(by_i[i]) / len(by_i[i]) > 0)
    return pos, len(active)


# ── DB/cache run ─────────────────────────────────────────────────────────────
async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient

    # 1. addresses = whatever the copyable run cached; src tags from a fresh
    #    leaderboard fetch (16 cheap requests; board drift => src '?' excluded
    #    from primary and counted).
    addrs = [f[:-5] for f in os.listdir(args.cache) if f.endswith(".json")
             and f.startswith("0x")]
    if not addrs:
        print(f"no cached histories in {args.cache} — run find_copyable_traders first")
        return 1
    client = PolymarketClient()
    async with client:
        universe = await fc.fetch_universe(client, args.universe, ["PNL", "VOL"])
    src_map = {u["address"]: u["src"] for u in universe}

    db = Database()
    await db.init()
    try:
        histories: dict[str, dict] = {}
        n_hft = 0
        for a in addrs:
            with open(os.path.join(args.cache, f"{a}.json")) as f:
                blob = json.load(f)
            trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
            status = blob.get("status", "ok") if isinstance(blob, dict) else "ok"
            for t in trades:
                t["_ts"] = fc.parse_ts(t.get("timestamp"))
            if not trades:
                continue
            # bots/market-makers: mechanically uncopyable, excluded (2026-07-10)
            if status == "hft" or fc.is_hft_history(trades, args.hft_max_rate):
                n_hft += 1
                continue
            histories[a] = {"trades": trades, "status": status}

        keys = sorted({t.get("marketId") or "" for h in histories.values()
                       for t in h["trades"]} - {""})
        markets = await fc.load_markets(db, keys, args.timeout)
        n_gamma = fc.merge_gamma_cache(markets, keys, args.gamma_cache)
    finally:
        await db.close()

    # 2. labeled first-buy evidence per trader, with knowledge times
    per_trader: dict[str, list[dict]] = {}
    n_no_resolved_at = n_labeled = 0
    for a, h in histories.items():
        rows = []
        for t in fc.first_buys(h["trades"], args.pmin, args.pmax):
            mkt = markets.get(t.get("marketId") or "")
            o = fc.label_outcome(t.get("tokenId") or "", t.get("side") or "", mkt)
            if o is None or t["_ts"] is None:
                continue
            n_labeled += 1
            ra = fc.parse_ts((mkt or {}).get("resolved_at"))
            if ra is None:
                n_no_resolved_at += 1
            rows.append({"_ts": t["_ts"], "known": known_time(t["_ts"], ra),
                         "marketId": t["marketId"], "edge": o - float(t["price"]),
                         "cat": fc.bucket_category((mkt or {}).get("category"))})
        if rows:
            per_trader[a] = rows

    all_ts = sorted(e["_ts"] for rows in per_trader.values() for e in rows)
    if not all_ts:
        print("no labeled evidence at all")
        return 1
    cfg = args
    dates = review_dates(all_ts[0], all_ts[-1], args.review_days)

    # 3. primary universe restriction (VOL-sourced, non-truncated)
    prim_traders = {a for a in per_trader
                    if "VOL" in src_map.get(a, "")
                    and histories[a]["status"] != "truncated"}
    unknown_src = sum(1 for a in per_trader if a not in src_map)

    # 4. walk-forward: descriptive (all) + primary (restricted) + robustness grids
    wf_all = walk_forward(per_trader, dates, cfg, args.seed)
    wf_prim = walk_forward({a: r for a, r in per_trader.items() if a in prim_traders},
                           dates, cfg, args.seed)
    robust_means = []
    for j, sh in enumerate((args.grid_shift, -args.grid_shift)):
        d2 = review_dates(all_ts[0], all_ts[-1], args.review_days, shift_days=sh)
        w2 = walk_forward({a: r for a, r in per_trader.items() if a in prim_traders},
                          d2, cfg, args.seed + 100 + j)
        s2 = portfolio_stats(w2["bets"], args.n_boot, args.seed + 200 + j)
        robust_means.append(s2["edge"] if s2 else float("nan"))

    stats_all = portfolio_stats(wf_all["bets"], args.n_boot, args.seed + 1)
    stats_prim = portfolio_stats(wf_prim["bets"], args.n_boot, args.seed + 2)
    verdict, detail = fc.primary_verdict(stats_prim, args.p_min, args.min_markets,
                                         args.min_traders, args.econ_floor,
                                         robust_means)
    _report(args, per_trader, histories, prim_traders, unknown_src, dates,
            wf_all, wf_prim, stats_all, stats_prim, robust_means, verdict,
            detail, n_labeled, n_no_resolved_at, n_hft, n_gamma)
    with open(args.out, "w") as f:
        json.dump(fc.json_safe({
            "verdict": verdict, "detail": detail,
            "primary_stats": stats_prim, "all_stats": stats_all,
            "robust_means": robust_means,
            "review_dates": [d.isoformat() for d in dates],
            "roster_sizes_primary": wf_prim["roster_sizes"],
            "ever_rostered_primary": sorted(wf_prim["ever_rostered"]),
            "ever_rostered_all": sorted(wf_all["ever_rostered"]),
        }), f, indent=1)
    print(f"full results -> {args.out}")
    return 0


def _report(args, per_trader, histories, prim_traders, unknown_src, dates,
            wf_all, wf_prim, stats_all, stats_prim, robust_means, verdict,
            detail, n_labeled, n_no_resolved_at, n_hft, n_gamma) -> None:
    print("\n" + "=" * 100)
    print("  WALK-FORWARD COPY-TRADER GRADE — hire on lifetime record, fire on proven decay,")
    print("  score only post-hire bets. The deployable rule, tested as it would run.")
    print(f"  traders with evidence={len(per_trader)}  labeled first-buys={n_labeled:,}"
          f"  (resolved_at missing for {n_no_resolved_at:,} — knowledge-time fallback=entry time)")
    print(f"  BOT/HFT excluded={n_hft} (> {args.hft_max_rate:.0f} bets/day)"
          f"  gamma-backfilled labels merged={n_gamma:,}")
    print(f"  review grid: every {args.review_days}d from {dates[0].date()} to {dates[-1].date()}"
          f" ({len(dates)} reviews; anchor locked {GRID_ANCHOR.date()})")
    print(f"  hire: >={args.min_markets_hire} mkts, span>={args.min_span_days}d, P(edge>0)>={args.p_hire}"
          f"   fire: trailing {args.fire_window_days}d, >={args.fire_min_markets} mkts, P(edge<0)>={args.p_fire}")
    print("=" * 100)

    def _block(name, wf, st):
        pos, act = interval_consistency(wf["bets"], len(dates) - 1)
        print(f"  {name}: ", end="")
        if st is None:
            print("no portfolio bets (roster never populated)")
            return
        print(f"edge={st['edge']:+.4f}  P(>0)={st['p']:.3f}  upper95={st['upper95']:+.4f}")
        print(f"    {st['n_bets']:,} bets / {st['n_markets']:,} mkts / {st['n_traders']} traders"
              f"  | intervals positive: {pos}/{act}"
              f"  | hires={wf['hires']} fires={wf['fires']}"
              f"  | avg roster={sum(wf['roster_sizes'])/max(1,len(wf['roster_sizes'])):.1f}")

    _block("DESCRIPTIVE (all traders)          ", wf_all, stats_all)
    _block("PRIMARY (VOL-sourced, full history)", wf_prim, stats_prim)
    print(f"  primary universe: {len(prim_traders)} traders"
          f"  (unknown-src excluded: {unknown_src}; truncated excluded: "
          f"{sum(1 for h in histories.values() if h['status'] == 'truncated')})")
    print("-" * 100)
    print(f"  robustness grids (+/-{args.grid_shift}d): pooled edges {robust_means}")
    print(f"  VERDICT: {verdict} — {detail}")
    if verdict == "PASS":
        print("  NEXT (not a deploy): per-fill OrderFilled chain audit on the rostered addresses,")
        print("  then the fill-quality gate (backtest_copyable_fills.py) on the same roster.")
    print("-" * 100)
    # per-category + top contributors (descriptive)
    cats = Counter()
    cat_edge: dict[str, list] = {}
    contrib: dict[str, list] = {}
    for m, e, c, a, i in wf_prim["bets"]:
        cats[c] += 1
        cat_edge.setdefault(c, []).append((m, e))
        contrib.setdefault(a, []).append((m, e))
    if cat_edge:
        print(f"  primary portfolio by category:")
        for c, rows in sorted(cat_edge.items(), key=lambda kv: -len(kv[1])):
            me = fc.per_market_edges(rows)
            print(f"    {c:<10} {len(rows):>5} bets  {len(me):>5} mkts  "
                  f"mean edge {sum(me)/len(me):+.4f}")
        print(f"  top rostered contributors (primary):")
        top = sorted(contrib.items(), key=lambda kv: -len(kv[1]))[:10]
        for a, rows in top:
            me = fc.per_market_edges(rows)
            print(f"    {a[:14]}  {len(rows):>5} bets  {len(me):>5} mkts  "
                  f"mean edge {sum(me)/len(me):+.4f}")
    print("=" * 100 + "\n")


# ── Self-test: the four personas, mechanically (no DB, no network) ───────────
class _Cfg:
    min_markets_hire = 25
    min_span_days = 60
    p_hire = 0.90
    fire_window_days = 90
    fire_min_markets = 10
    p_fire = 0.90
    n_boot_roster = 400


def _mk(base: datetime, day: int) -> datetime:
    return base + timedelta(days=day)


def _rows(t0, day0, n, mu, tag, seed, spread_days=60):
    import random
    rng = random.Random(seed)
    out = []
    for i in range(n):
        ts = _mk(t0, day0 + (i * spread_days) // max(1, n))
        out.append({"_ts": ts, "known": ts, "marketId": f"m{tag}{i}",
                    "edge": rng.gauss(mu, 0.05), "cat": "sports"})
    return out


def _self_test() -> int:
    print("SELF-TEST — walk-forward mechanics (no DB, no network)\n")
    ok = True
    cfg = _Cfg()
    t0 = datetime(2025, 1, 1)

    # persona 1: GOAT — strong 10 months, mildly bad recent month → NOT fired
    goat = _rows(t0, 0, 300, 0.08, "g", 1, spread_days=300) \
         + _rows(t0, 300, 12, -0.01, "gb", 2, spread_days=25)
    t_rev = _mk(t0, 330)
    hired = hire_ok(goat, t_rev, cfg, 7)
    fired = _fire(goat, t_rev, cfg, 7)
    print(f"  [goat] hired={hired} fired-after-mild-bad-month={fired} : {hired and not fired}")
    ok &= hired and not fired

    # persona 2: $1M-then-bleed — great year, then 5 months of real decay → fired
    bleeder = _rows(t0, 0, 200, 0.10, "b", 3, spread_days=300) \
            + _rows(t0, 310, 45, -0.08, "bb", 4, spread_days=85)
    t_rev = _mk(t0, 400)
    still_hire_bar = hire_ok(bleeder, t_rev, cfg, 8)   # lifetime still shiny...
    fired = _fire(bleeder, t_rev, cfg, 8)              # ...but recent decay convicts
    print(f"  [bleeder] lifetime-bar-still-passes={still_hire_bar} fired-on-recent={fired} : {fired}")
    ok &= fired

    # persona 3: improver — bad first 30, good next 60 → hired mid-run, not before
    improver = _rows(t0, 0, 30, -0.05, "BAD", 5, spread_days=90) \
             + _rows(t0, 100, 60, 0.09, "GOOD", 6, spread_days=150)
    early = hire_ok(improver, _mk(t0, 95), cfg, 9)
    late = hire_ok(improver, _mk(t0, 260), cfg, 9)
    print(f"  [improver] hired-early={early} hired-after-turnaround={late} : {(not early) and late}")
    ok &= (not early) and late

    # persona 4: hot-streaker — 30 great markets in 10 days → span rule blocks
    streak = _rows(t0, 0, 30, 0.20, "s", 10, spread_days=10)
    print(f"  [streak] hired-off-one-hot-week={hire_ok(streak, _mk(t0, 20), cfg, 11)} : "
          f"{not hire_ok(streak, _mk(t0, 20), cfg, 11)}")
    ok &= not hire_ok(streak, _mk(t0, 20), cfg, 11)

    # walk-forward end-to-end: goat+improver on roster produce post-hire bets only
    per = {"G": goat, "I": improver, "S": streak}
    dates = review_dates(t0, _mk(t0, 400), 30)
    wf = walk_forward(per, dates, cfg, 42)
    bets_pre_hire = [b for b in wf["bets"] if b[3] == "I"
                     and b[0].startswith("mBAD")]  # improver's pre-hire markets
    print(f"  [walk] bets={len(wf['bets'])} hires={wf['hires']} "
          f"improver-pre-hire-bets-counted={len(bets_pre_hire)} : "
          f"{wf['hires'] >= 2 and len(bets_pre_hire) == 0}")
    ok &= wf["hires"] >= 2 and len(bets_pre_hire) == 0

    # grid is locked and shiftable
    d0 = review_dates(t0, _mk(t0, 100), 30)
    d1 = review_dates(t0, _mk(t0, 100), 30, shift_days=15)
    ok_g = d0 and d1 and all((d - GRID_ANCHOR).days % 30 == 0 for d in d0) \
        and all((d - GRID_ANCHOR - timedelta(days=15)).days % 30 == 0 for d in d1)
    print(f"  [grid] anchored + shiftable : {ok_g}")
    ok &= ok_g

    # knowledge time: long-dated market not evidence before resolution
    e = {"_ts": t0, "known": known_time(t0, _mk(t0, 200)), "marketId": "mx",
         "edge": 0.5, "cat": "politics"}
    ok_k = e["known"] == _mk(t0, 200) and known_time(t0, None) == t0
    print(f"  [knowledge] resolved_at gates evidence; fallback=entry : {ok_k}")
    ok &= ok_k

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Walk-forward copy-trader grade (hire/fire/score)")
    ap.add_argument("--cache", default="/tmp/copyable_cache")
    ap.add_argument("--universe", type=int, default=300, help="leaderboard depth for src tags")
    ap.add_argument("--review-days", type=int, default=30, dest="review_days")
    ap.add_argument("--grid-shift", type=int, default=15, dest="grid_shift",
                    help="robustness grids = review dates shifted +/- this many days")
    ap.add_argument("--min-markets-hire", type=int, default=25, dest="min_markets_hire")
    ap.add_argument("--min-span-days", type=int, default=60, dest="min_span_days")
    ap.add_argument("--p-hire", type=float, default=0.90, dest="p_hire")
    ap.add_argument("--fire-window-days", type=int, default=90, dest="fire_window_days")
    ap.add_argument("--fire-min-markets", type=int, default=10, dest="fire_min_markets")
    ap.add_argument("--p-fire", type=float, default=0.90, dest="p_fire")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--min-traders", type=int, default=5, dest="min_traders")
    ap.add_argument("--econ-floor", type=float, default=0.02, dest="econ_floor")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot")
    ap.add_argument("--n-boot-roster", type=int, default=400, dest="n_boot_roster",
                    help="bootstrap size for hire/fire decisions (cheaper, deterministic)")
    ap.add_argument("--hft-max-rate", type=float, default=200.0, dest="hft_max_rate",
                    help="exclude accounts trading more than this many times/day (bots; 0 disables)")
    ap.add_argument("--gamma-cache", default="/tmp/copyable_cache/gamma_resolutions.json",
                    dest="gamma_cache",
                    help="resolutions backfill JSON (backfill_resolutions_gamma.py)")
    ap.add_argument("--pmin", type=float, default=0.02)
    ap.add_argument("--pmax", type=float, default=0.98)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--out", default="/tmp/walkforward.json")
    ap.add_argument("--seed", type=int, default=20260710)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
