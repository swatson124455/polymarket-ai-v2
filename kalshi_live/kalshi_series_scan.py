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

WHAT THIS CANNOT TELL YOU (and it is most of the decision):
  * TOXICITY / adverse selection. The reward side says nothing about whether the flow
    that fills you is informed. The mention family sits at the TOP of every pool ranking
    and is a known settlement trap (FIGHTMENTION +745 in-window / -1338 settled).
    A high score here is a reason to INVESTIGATE, never a reason to trade.
  * MAKER FEES. Only KXTEMP*, KXAAAGASD, KXAAAGASW are fee-verified ($0 via read-back).
    An unverified series could charge maker fees that swallow the whole reward. This is
    a hard blocker on any new series until probed against the account.
  * STRUCTURE. The event-aggregate delta throttle assumes additive "above X" threshold
    ladders. Mutually-exclusive / candidate / range series would sum anti-correlated
    strikes as if additive and MIS-FIRE (running tab §H, review finding B2).
  * Instantaneous snapshot; competitors requote; programs churn hourly.

Run:  python kalshi_series_scan.py [top_series] [markets_per_series]
"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(HERE, "series_scan.json")

JOIN_SIZE = float(os.environ.get("SCAN_JOIN_SIZE", 20))
MAX_MARKET = float(os.environ.get("SCAN_MAX_MARKET", 15))
MIN_PAYOUT = 1.00
TICK = 0.01
SPACING_S = 0.30
_last = [0.0]

OURS = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
        "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")
# Fee-verified ($0 maker, via prod read-back). Everything else is fee-UNKNOWN.
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


def main(top_n, per_series):
    progs, cur = [], ""
    for _ in range(8):
        d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    by_series = defaultdict(list)
    for p in progs:
        if not days_of(p):
            continue
        by_series[(p.get("market_ticker") or "").split("-")[0]].append(p)

    pool_day = {s: sum((p["period_reward"] / 10000.0) / days_of(p) for p in ps)
                for s, ps in by_series.items()}
    ranked = sorted(pool_day.items(), key=lambda kv: -kv[1])
    venue_total = sum(pool_day.values())
    print(f"venue: {len(progs)} programs / {len(by_series)} series / ${venue_total:,.0f}/day pool")
    print(f"scanning top {top_n} series, up to {per_series} markets each "
          f"(join {JOIN_SIZE:.0f}ct, ${MAX_MARKET:.0f}/mkt)\n")

    rows = []
    for si, (s, pd) in enumerate(ranked[:top_n], 1):
        ps = sorted(by_series[s], key=lambda p: -(p["period_reward"] / 10000.0) / days_of(p))
        got, cap_day, shares, two_ct, n = 0, 0.0, [], 0, 0
        for p in ps[:per_series]:
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
        scale = len(by_series[s]) / n
        rows.append({
            "series": s, "programs": len(by_series[s]), "pool_day": pd,
            "sampled": n, "cap_day_sampled": cap_day,
            "cap_day_series": cap_day * scale,
            "cap_day_per_market": cap_day / n,
            "share": sum(shares) / len(shares),
            "two_sided_pct": 100.0 * two_ct / n,
            "ours": s in OURS, "fee_ok": s in FEE_VERIFIED,
            "toxic_hint": any(k in s for k in TOXIC_HINT),
        })
        print(f"  [{si:>3}/{top_n}] {s:26s} pool ${pd:>8,.0f}/d  "
              f"cap/mkt ${cap_day/n:>7.2f}/d  extrap ${cap_day*scale:>8,.2f}/d  "
              f"2sided {100.0*two_ct/n:>3.0f}%  {'OURS' if s in OURS else ('TOXIC?' if any(k in s for k in TOXIC_HINT) else '')}")
    json.dump(rows, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT} ({len(rows)} series)")
    return rows


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x.isdigit()]
    main(int(a[0]) if a else 30, int(a[1]) if len(a) > 1 else 4)
