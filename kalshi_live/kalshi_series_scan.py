#!/usr/bin/env python3
"""SERIES SCAN — sandbox, READ-ONLY, NO KEYS, NO MONEY, NEVER TRADES.

THE QUESTION: which SERIES are we NOT in where we could actually eat, or at least
get scraps?

NAMING (operator correction 2026-07-23, see KALSHI_LIP_RULE_CANON.md §T): this file was
first shipped as `kalshi_sector_scan.py`, which was wrong — it scans SERIES, not sectors.
A SECTOR (weather / gas / politics) is OUR thematic grouping and sits ABOVE series; a
SERIES (`KXAAAGASD`) is the recurring question template. Renamed to match what it does.

Pool size alone does NOT answer this and ranking by it is how you walk into a trap.
What matters is what WE would capture if we joined at reference with our real deployed
size, against the competition already resting there. A $4,469/day pool with a wall of
qualifying liquidity pays us less than a $400/day pool nobody is quoting.

So this scores, per market, the same way `kalshi_concentration_study.py` does:
  * join at reference on both sides
  * size = min(JOIN_SIZE contracts, per-market capital / price)   [R1/size-model rules]
  * payout = pool x (yes_share + no_share) / 2                    [rulebook R4]
  * normalise to $/DAY using the program's own Time Period        [rulebook R1 -- the
    single most important correction; a monthly pool is NOT a daily pool]
  * apply the $1.00-per-Time-Period threshold                     [rulebook R2]

See docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md for all four rules, quoted from the
CFTC filing.

TWO MEASUREMENT DEFECTS FIXED 2026-07-23 (D2, D3) — both produced published numbers:

  D2  the program census used `limit=1000` across an 8-page loop — a hard 8,000-program
      ceiling, above which the tail of the census silently does not exist to this scan.
      The live quoter pages at 10000 (maker_kalshi_quoter.py:773); the scan now matches.
      ⚠ MEASURED 2026-07-23 against the live venue: this changes NOTHING today —
      limit=1000 and limit=10000 both return 2,298 programs / 160 series, and KXRT shows
      70 programs either way. So this is a LATENT-ceiling fix with ~3.5x headroom, and
      it is NOT the explanation for a "KXRT = 0 programs" reading. The likelier cause of
      any such reading is point-in-time program CHURN (canon §M7c: programs cycle hourly,
      an instantaneous census between windows shows zero) — KXPM and KXAAAGASD both read
      0 programs at this instant for exactly that reason. `fetch_programs` now SAYS SO
      when the cursor is still open at the page limit instead of returning a short census
      as if it were complete.
  D3  markets WITHIN a series were ranked by per-program reward-per-day. When a series
      shares ONE pool across all its contracts that key is CONSTANT, the sort is stable,
      and `ps[:N]` degenerates to "whatever order the API returned". §M5's
      "KXEARNINGSMENTIONLMT 100% two-sided / $7.42" and the KXPM row are head-of-list
      slices of n=4 at one instant, not samples. Ranking is now deterministically
      tie-broken and the SAMPLER is RANDOM (seeded) or FULL CENSUS, never head-of-list;
      the mode, the seed, the denominator and a `thin` flag ride along in every row.

WHAT THIS CANNOT TELL YOU (and it is most of the decision):
  * TOXICITY / adverse selection. The reward side says nothing about whether the flow
    that fills you is informed. The mention family sits at the TOP of every pool ranking
    and is a known settlement trap (FIGHTMENTION +745 in-window / -1338 settled).
    A high score here is a reason to INVESTIGATE, never a reason to trade.
  * STRUCTURE. The event-aggregate delta throttle assumes additive "above X" threshold
    ladders. Mutually-exclusive / candidate / range series would sum anti-correlated
    strikes as if additive and MIS-FIRE (running tab §H, review finding B2).
  * Instantaneous snapshot; competitors requote; programs churn hourly. `instants: 1`
    is carried in every row so a point estimate is never mistaken for a rate.

MAKER FEES are no longer a blank. Canon §M10: the maker multiplier defaults to ZERO, so
maker fees are free exchange-wide EXCEPT on the ~86 series in the Non-Standard table.
`series_fee_types.json` (`fee_type`: `quadratic` = free vs `quadratic_with_maker_fees` =
charges) is wired in and every candidate is annotated FREE / CHARGES / UNKNOWN; unknown
series are resolved from GET /series/{ticker}. Of the series carrying active LIP programs
exactly one charges: KXAAAGASM.

Run:  python kalshi_series_scan.py [top_series] [markets_per_series] [--census|--head]
                                   [--seed N]
"""
import json
import os
import random
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(HERE, "series_scan.json")
FEE_TYPES_PATH = os.path.join(HERE, "series_fee_types.json")

JOIN_SIZE = float(os.environ.get("SCAN_JOIN_SIZE", 20))
MAX_MARKET = float(os.environ.get("SCAN_MAX_MARKET", 15))
MIN_PAYOUT = 1.00
TICK = 0.01
SPACING_S = 0.30
# D2: the venue's active-program census exceeds 8 x 1000. The live quoter pages at 10000
# (maker_kalshi_quoter.py:773); anything smaller silently truncates the tail of the
# census, and a series that lands in the truncated tail is reported as ZERO programs.
PAGE_LIMIT = int(os.environ.get("SCAN_PAGE_LIMIT", 10000))
PAGES = 8
# D3: head-of-list is not a sample. 'random' (seeded, reproducible) or 'census'.
SAMPLE_MODE = os.environ.get("SCAN_SAMPLE_MODE", "random")
SAMPLE_SEED = int(os.environ.get("SCAN_SEED", 0))
THIN_N = 8              # below this many contracts a per-series rate is not evidence
_last = [0.0]

OURS = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
        "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")
# Fee-verified ($0 maker, via prod read-back against the account). Kept as the strongest
# evidence tier; canon §M10 supersedes it as a BLOCKER (see fee_status below).
FEE_VERIFIED = set(OURS)
# Known settlement trap (running tab §B): the mention family.
TOXIC_HINT = ("MENTION",)


def _load_scoring():
    import importlib.util
    for cand in (os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py"),
                 os.path.join(HERE, "maker_kalshi_recorder.py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("_rec", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("maker_kalshi_recorder.py not found")


REC = _load_scoring()


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-sector-scan/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def levels(raw):
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


_FEE_CACHE = None


def fee_types():
    """`series_fee_types.json` — {series: {fee_type, fee_multiplier, category}}."""
    global _FEE_CACHE
    if _FEE_CACHE is None:
        try:
            with open(FEE_TYPES_PATH) as fh:
                _FEE_CACHE = json.load(fh)
        except (OSError, ValueError):
            _FEE_CACHE = {}
    return _FEE_CACHE


def fee_status(series, fetch=True):
    """('FREE' | 'CHARGES' | 'UNKNOWN', fee_type) for a series' MAKER fills.

    Canon §M10: the maker fee multiplier defaults to ZERO, so maker fills are free
    exchange-wide unless the series is in the Non-Standard Fees table. `fee_type` is the
    venue's two-valued enum for exactly that: `quadratic` (maker-free) vs
    `quadratic_with_maker_fees` (charges). If the table has no entry we ASK
    GET /series/{ticker} rather than guessing — an unknown must never be reported as
    free, because a maker fee can swallow the entire reward."""
    e = fee_types().get(series)
    if e is None and fetch:
        try:
            d = get(f"/series/{series}")
            ft = ((d.get("series") or d) or {}).get("fee_type")
            if ft:
                e = {"fee_type": ft, "source": "series_endpoint"}
                fee_types()[series] = e
        except Exception:
            e = None
    ft = (e or {}).get("fee_type")
    if not ft:
        return "UNKNOWN", None
    return ("CHARGES" if ft == "quadratic_with_maker_fees" else "FREE"), ft


def fetch_programs():
    """Full active-program census. D2: PAGE_LIMIT, not 1000."""
    progs, cur, seen_cursors = [], "", set()
    for _ in range(PAGES):
        d = get(f"/incentive_programs?status=active&limit={PAGE_LIMIT}"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen_cursors:
            break
        seen_cursors.add(cur)
    else:
        # ran out of pages with a cursor still outstanding => STILL truncated. Say so
        # rather than quietly reporting a short census as if it were complete.
        if cur:
            print(f"  !! CENSUS TRUNCATED at {len(progs)} programs "
                  f"({PAGES} pages x {PAGE_LIMIT}) — cursor still open. "
                  f"Series counts below are LOWER BOUNDS.")
    return progs


def rank_programs(ps):
    """Order a series' programs by reward-per-day, DETERMINISTICALLY.

    D3: when a series shares one pool the primary key is constant for every contract,
    Python's sort is stable, and the result is the API's arbitrary order — which then
    silently became the 'top N'. The ticker tie-break makes the ordering a property of
    the DATA instead of a property of the response."""
    return sorted(ps, key=lambda p: (-(p["period_reward"] / 10000.0) / days_of(p),
                                     p.get("market_ticker") or ""))


def select_markets(ps, per_series, mode=None, seed=None):
    """Which contracts of a series to actually score. Returns (subset, mode_used).

    'census' = all of them (the only mode that cannot be biased).
    'random' = seeded sample of the deterministically-ordered list, so it is
               reproducible AND independent of the order the venue returned.
    'head'   = the legacy top-N slice, kept only to reproduce the committed §M5 rows.
               It is NOT a sample when the ranking key is degenerate."""
    mode = (mode or SAMPLE_MODE).lower()
    ranked = rank_programs(ps)
    if mode == "census" or per_series is None or per_series >= len(ranked):
        return ranked, "census"
    if mode == "head":
        return ranked[:per_series], "head"
    rng = random.Random(SAMPLE_SEED if seed is None else seed)
    idx = sorted(rng.sample(range(len(ranked)), per_series))
    return [ranked[i] for i in idx], "random"


def days_of(p):
    a = datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
    b = datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
    d = (b - a).total_seconds() / 86400.0
    return d if d > 0 else None


def score(yl, nl, target, df, pool, days):
    """(payout_per_day, our_combined_share, two_sided) at the deployed shape."""
    if not yl or not nl:
        return 0.0, 0.0, False
    by = max(p for p, _ in yl)
    bn = max(p for p, _ in nl)
    if by <= 0 or bn <= 0:
        return 0.0, 0.0, False
    half = MAX_MARKET / 2.0
    cy = min(JOIN_SIZE, half / by)
    cn = min(JOIN_SIZE, half / bn)
    ys = REC.side_share(yl, [(by, cy)], target, df, TICK)[0]
    ns = REC.side_share(nl, [(bn, cn)], target, df, TICK)[0]
    # R3 FIRST, and it is decisive: "Snapshots will be excluded if there is not two-sided
    # liquidity ... on each side of the market". An excluded snapshot pays NOBODY. Scoring
    # a one-sided book by share alone put KXWNBAMENTION top of this scan at $604/day
    # capture while 0% of its sampled books were two-sided -- a pure artefact. Our own 20ct
    # cannot rescue a book that misses a 1000-contract Target Size (canon §M2: marginal in
    # 0/304), so a persistently one-sided series is UNEARNABLE, not an opportunity.
    two = (REC.qualifying_walk(yl, target)[0] is not None
           and REC.qualifying_walk(nl, target)[0] is not None)
    if not two:
        return 0.0, ys + ns, False
    pay = pool * (ys + ns) / 2.0
    if pay < MIN_PAYOUT:            # R2 threshold, applied per Time Period
        return 0.0, ys + ns, two
    return pay / days, ys + ns, two


def main(top_n, per_series, sample_mode=None, seed=None):
    progs = fetch_programs()
    by_series = defaultdict(list)
    for p in progs:
        if not days_of(p):
            continue
        by_series[(p.get("market_ticker") or "").split("-")[0]].append(p)

    pool_day = {s: sum((p["period_reward"] / 10000.0) / days_of(p) for p in ps)
                for s, ps in by_series.items()}
    ranked = sorted(pool_day.items(), key=lambda kv: -kv[1])
    venue_total = sum(pool_day.values())
    print(f"venue: {len(progs)} programs / {len(by_series)} series / ${venue_total:,.0f}/day pool "
          f"(census: {PAGES} pages x {PAGE_LIMIT})")
    print(f"scanning top {top_n} series, up to {per_series} markets each "
          f"(join {JOIN_SIZE:.0f}ct, ${MAX_MARKET:.0f}/mkt)")
    _mode = (sample_mode or SAMPLE_MODE).lower()
    print(f"sampling: mode={_mode} seed={SAMPLE_SEED if seed is None else seed}, "
          f"ONE instant per contract. A per-series rate over n<{THIN_N} contracts at a "
          f"single instant is a point estimate, NOT evidence — rows are flagged `thin`.\n")

    rows = []
    for si, (s, pd) in enumerate(ranked[:top_n], 1):
        ps, mode_used = select_markets(by_series[s], per_series, sample_mode, seed)
        got, cap_day, shares, two_ct, n = 0, 0.0, [], 0, 0
        for p in ps:
            t = p["market_ticker"]
            try:
                ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
            except Exception:
                continue
            yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
            d = days_of(p)
            pool = p["period_reward"] / 10000.0
            tgt = float(p.get("target_size_fp") or 0)
            df = float(p.get("discount_factor_bps") or 0) / 10000.0
            if tgt <= 0 or df <= 0:
                continue
            cd, sh, two = score(yl, nl, tgt, df, pool, d)
            cap_day += cd
            shares.append(sh)
            two_ct += 1 if two else 0
            n += 1
            got += 1
        if not n:
            continue
        # extrapolate the sampled markets to the series' full program count
        n_progs = len(by_series[s])
        scale = n_progs / n
        fee, fee_type = fee_status(s)
        rows.append({
            "series": s, "programs": n_progs, "pool_day": pd,
            "sampled": n, "cap_day_sampled": cap_day,
            "cap_day_series": cap_day * scale,
            "cap_day_per_market": cap_day / n,
            "share": sum(shares) / len(shares),
            "two_sided_ct": two_ct,
            "two_sided_pct": 100.0 * two_ct / n,
            # D3 — sample provenance travels with the number, always.
            "sample_mode": mode_used, "seed": SAMPLE_SEED if seed is None else seed,
            "coverage_pct": 100.0 * n / n_progs, "instants": 1,
            "thin": n < THIN_N,
            "ours": s in OURS, "fee_ok": s in FEE_VERIFIED,
            # canon §M10 — maker-fee annotation for every candidate
            "maker_fee": fee, "fee_type": fee_type,
            "toxic_hint": any(k in s for k in TOXIC_HINT),
        })
        flags = " ".join(x for x in (
            "OURS" if s in OURS else "",
            "TOXIC?" if any(k in s for k in TOXIC_HINT) else "",
            "MAKER-FEE" if fee == "CHARGES" else ("FEE?" if fee == "UNKNOWN" else ""),
            "THIN" if n < THIN_N else "") if x)
        print(f"  [{si:>3}/{top_n}] {s:26s} pool ${pd:>8,.0f}/d  "
              f"cap/mkt ${cap_day/n:>7.2f}/d  extrap ${cap_day*scale:>8,.2f}/d  "
              f"2sided {two_ct}/{n} ({100.0*two_ct/n:>3.0f}%) of {n_progs}  {flags}")
    json.dump(rows, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT} ({len(rows)} series)")
    return rows


if __name__ == "__main__":
    argv = sys.argv[1:]
    mode = ("census" if "--census" in argv else
            "head" if "--head" in argv else
            "random" if "--random" in argv else None)
    sd = None
    if "--seed" in argv:
        sd = int(argv[argv.index("--seed") + 1])
        argv = argv[:argv.index("--seed")] + argv[argv.index("--seed") + 2:]
    a = [x for x in argv if x.isdigit()]
    main(int(a[0]) if a else 30, int(a[1]) if len(a) > 1 else 4,
         sample_mode=mode, seed=sd)
