#!/usr/bin/env python3
"""VENUE-WIDE HORIZON-RATIO CENSUS — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

THE QUESTION: which SERIES on the whole venue look like GAS?

The discriminator (measured 2026-07-23) is the HORIZON RATIO:

    ratio = (market close_time - now) / (program end_date - now)

  ratio ~ 1.0  the reward program spans the WHOLE hold. You are paid for every hour you
               carry the inventory. This is what KXAAAGASD / KXAAAGASW look like.
  ratio >> 1   the reward STOPS days-to-months before the contract closes, so the tail of
               the hold is uncompensated inventory risk. Worse, there is NO EXIT for it:
                 * select_footprint keys lifecycle on the PROGRAM end_date
                   (maker_kalshi_quoter.py:294-312) — when the program expires the ticker
                   leaves the footprint entirely;
                 * STRAND UNWIND (maker_kalshi_quoter.py:1022-1053) iterates NAKED
                   positions only — a MATCHED PAIR has no exit path at all.
               So a high-ratio series can strand a hedged pair with zero reward accrual
               and no code path that closes it.

This scan applies the ratio filter to every series carrying an active LIP program, and
ranks by:  ratio <= RATIO_MAX  ->  maker-fee FREE  ->  two-sided rate  ->  pool $/day.

All four rulebook rules from docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md are applied:
  R1  period_reward is the TOTAL for a Time Period, NOT a rate. Everything is normalised
      to $/day before any comparison. A monthly pool is NOT a daily pool.
  R2  the $1.00 minimum is a threshold on the WHOLE-period payout.
  R3  two-sided exclusion is MARKET-level and is applied BEFORE ranking — a snapshot on a
      one-sided book pays NOBODY, so a persistently one-sided series is UNEARNABLE, not
      an opportunity. (Skipping this put a 0%-two-sided series top of a prior scan at
      $604/day.)
  R4  payout fraction = (yes_share + no_share) / 2.

STRUCTURE is classified from the DATA, not from the ticker string. A numeric-suffix
heuristic mis-classifies prefixed numerics (KXFUNDRAISING-...-A145000000 IS a threshold
ladder). This reads `strike_type` off /markets/{ticker} and `mutually_exclusive` off
/events/{event_ticker}.

WHAT THIS CANNOT TELL YOU — and it is most of the decision:
  * FILL RATE and ADVERSE SELECTION. There is no queue position in the public data, so
    the reward side is all that is simulatable. A series can score perfectly here and
    still bleed on the trading side (canon §M8: KXTEMP* earned 91% of our reward income
    and was 100% of our loss).
  * The capture figures are UPPER BOUNDS (canon §M7d, model over-predicts ~2-6x): they
    assume we rest at reference on both sides for the whole period.
  * ONE INSTANT. Programs churn hourly (canon §M7c) — a point-in-time census understates
    any series whose windows cycle. `instants: 1` rides along on every row.
  * Sampling is SEEDED-RANDOM over a deterministically-ordered program list, never
    head-of-list: a series sharing ONE pool across contracts has a constant sort key, so
    "top N by pool" degenerates to arbitrary API order (that defect produced a false
    "100% two-sided / $7.42" on n=4).

Run:  python kalshi_horizon_census.py [markets_per_series] [--seed N] [--full]
"""
import json
import os
import random
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(HERE, "horizon_census.json")
FEE_TYPES_PATH = os.path.join(HERE, "series_fee_types.json")

JOIN_SIZE = float(os.environ.get("SCAN_JOIN_SIZE", 20))     # deployed shape
MAX_MARKET = float(os.environ.get("SCAN_MAX_MARKET", 15))   # deployed per-contract cap
MIN_PAYOUT = 1.00                                           # R2
TICK = 0.01
SPACING_S = 0.30                                            # public-API courtesy spacing
PAGE_LIMIT = 10000          # 1000 silently truncates — known defect
PAGES = 8
RATIO_MAX = float(os.environ.get("CENSUS_RATIO_MAX", 1.5))
THIN_N = 4
_last = [0.0]

OURS = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
        "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")
TOXIC_HINT = ("MENTION",)
LADDER_STRIKES = ("greater", "less", "greater_or_equal", "less_or_equal",
                  "greater_than", "less_than", "between")


def _load_scoring():
    """Reuse the SAME qualifying-walk / share code the recorder and the live quoter use.

    Re-implementing the walk here would be a second copy of the rulebook, free to drift
    from the one that actually prices our orders."""
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
    req = urllib.request.Request(PUB + path,
                                 headers={"User-Agent": "kalshi-horizon-census/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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
    global _FEE_CACHE
    if _FEE_CACHE is None:
        try:
            with open(FEE_TYPES_PATH) as fh:
                _FEE_CACHE = json.load(fh)
        except (OSError, ValueError):
            _FEE_CACHE = {}
    return _FEE_CACHE


def fee_status(series, fetch=True):
    """('FREE'|'CHARGES'|'UNKNOWN', fee_type) for MAKER fills on this series.

    Canon §M10: the maker multiplier defaults to ZERO, so maker fills are free venue-wide
    except on the explicitly-listed series. An UNKNOWN is never reported as free — a maker
    fee can swallow the whole reward."""
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
    progs, cur, seen = [], "", set()
    for _ in range(PAGES):
        d = get(f"/incentive_programs?status=active&limit={PAGE_LIMIT}"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    else:
        if cur:
            print(f"  !! CENSUS TRUNCATED at {len(progs)} programs — cursor still open. "
                  f"Every series count below is a LOWER BOUND.")
    return progs


def days_of(p):
    a, b = parse_iso(p["start_date"]), parse_iso(p["end_date"])
    d = (b - a).total_seconds() / 86400.0
    return d if d > 0 else None


def fetch_markets_batch(tickers):
    """/markets?tickers=a,b,c — batched metadata. Falls back to per-ticker on failure."""
    out = {}
    CH = 40
    for i in range(0, len(tickers), CH):
        chunk = tickers[i:i + CH]
        try:
            d = get("/markets?limit=200&tickers=" + ",".join(chunk))
            for m in d.get("markets") or []:
                out[m["ticker"]] = m
        except Exception:
            for t in chunk:
                try:
                    d = get(f"/markets/{t}")
                    m = d.get("market") or d
                    out[m["ticker"]] = m
                except Exception:
                    continue
    return out


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
    # R3 FIRST and it is decisive — an excluded snapshot pays NOBODY, ours or anyone's.
    two = (REC.qualifying_walk(yl, target)[0] is not None
           and REC.qualifying_walk(nl, target)[0] is not None)
    if not two:
        return 0.0, ys + ns, False
    pay = pool * (ys + ns) / 2.0
    if pay < MIN_PAYOUT:                       # R2, applied per Time Period
        return 0.0, ys + ns, two
    return pay / days, ys + ns, two


def calendar_of(ps, now):
    """Program CALENDAR for a series: does it run during OUR dark hours?

    Our two working series are dark for a large part of the day (KXAAAGASD's measured duty
    cycle is ~48.8%, windows all ending 03:59Z and restarting ~14:44-15:31Z). A candidate
    that only runs while gas is already live adds nothing. Returns UTC hour coverage as a
    24-slot bitmap over the union of the series' active windows, clipped to the next 24h."""
    hours = set()
    horizon = 24
    for p in ps:
        try:
            a, b = parse_iso(p["start_date"]), parse_iso(p["end_date"])
        except Exception:
            continue
        # hours from now forward that this window covers
        for h in range(horizon):
            t0 = now.replace(minute=0, second=0, microsecond=0)
            slot = t0.timestamp() + h * 3600
            if a.timestamp() <= slot < b.timestamp():
                hours.add(int((datetime.fromtimestamp(slot, timezone.utc)).hour))
    return sorted(hours)


def main(per_series=5, seed=0, full=False):
    now = datetime.now(timezone.utc)
    progs = fetch_programs()
    by_series = defaultdict(list)
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if not days_of(p):
            continue
        t = p.get("market_ticker") or ""
        if not t:
            continue
        by_series[t.split("-")[0]].append(p)

    pool_day = {s: sum(((p.get("period_reward") or 0) / 10000.0) / days_of(p) for p in ps)
                for s, ps in by_series.items()}
    venue_total = sum(pool_day.values())
    ranked = sorted(pool_day.items(), key=lambda kv: -kv[1])
    print(f"census @ {now.isoformat()}")
    print(f"venue: {len(progs)} active programs / {len(by_series)} series "
          f"/ ${venue_total:,.0f}/day headline pool")
    print(f"sampling {per_series}/series, seeded-random (seed={seed}), ONE instant.\n")

    # ---- pass 1: pick the sample, batch-fetch market metadata (close_time, strike_type)
    sample = {}
    for s, ps in by_series.items():
        ordered = sorted(ps, key=lambda p: (-((p.get("period_reward") or 0) / 10000.0)
                                            / days_of(p),
                                            p.get("market_ticker") or ""))
        if full or per_series >= len(ordered):
            sample[s] = ordered
        else:
            rng = random.Random(f"{seed}:{s}")
            idx = sorted(rng.sample(range(len(ordered)), per_series))
            sample[s] = [ordered[i] for i in idx]

    all_tickers = [p["market_ticker"] for ps in sample.values() for p in ps]
    print(f"fetching metadata for {len(all_tickers)} contracts...")
    meta = fetch_markets_batch(all_tickers)
    print(f"  got {len(meta)}/{len(all_tickers)}\n")

    rows = []
    for si, (s, pd) in enumerate(ranked, 1):
        ps = sample[s]
        ratios, closed, no_meta = [], 0, 0
        strikes, ev_tickers = [], []
        cap_day, shares, two_ct, n = 0.0, [], 0, 0
        for p in ps:
            t = p["market_ticker"]
            m = meta.get(t)
            if not m:
                no_meta += 1
            else:
                strikes.append(m.get("strike_type") or "")
                if m.get("event_ticker"):
                    ev_tickers.append(m["event_ticker"])
                try:
                    ct = parse_iso(m["close_time"])
                    pe = parse_iso(p["end_date"])
                    num = (ct - now).total_seconds()
                    den = (pe - now).total_seconds()
                    if den <= 0:
                        pass                     # program already over: no horizon to rate
                    elif num <= 0:
                        closed += 1              # contract closed but program still active
                    else:
                        ratios.append(num / den)
                except Exception:
                    pass
            # ---- R3 book test + capture, on the same contracts
            try:
                ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
            except Exception:
                continue
            yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
            d = days_of(p)
            pool = (p.get("period_reward") or 0) / 10000.0
            tgt = float(p.get("target_size_fp") or 0)
            df = float(p.get("discount_factor_bps") or 0) / 10000.0
            if tgt <= 0 or df <= 0:
                continue
            cd, sh, two = score(yl, nl, tgt, df, pool, d)
            cap_day += cd
            shares.append(sh)
            two_ct += 1 if two else 0
            n += 1
        if not n and not ratios:
            continue

        # ---- structure, from the DATA not the ticker string
        mut_ex = None
        category = None
        if ev_tickers:
            try:
                e = get(f"/events/{ev_tickers[0]}")
                e = e.get("event") or e
                mut_ex = e.get("mutually_exclusive")
                category = e.get("category")
            except Exception:
                pass
        st = [x for x in strikes if x]
        ladder_st = bool(st) and all(x in LADDER_STRIKES for x in st)
        if mut_ex is True:
            structure = "categorical"
        elif ladder_st and mut_ex is False:
            structure = "ladder"
        elif ladder_st:
            structure = "ladder?"
        elif st:
            structure = f"other({'/'.join(sorted(set(st)))})"
        else:
            structure = "unknown"

        fee, fee_type = fee_status(s)
        med = statistics.median(ratios) if ratios else None
        rows.append({
            "series": s,
            "pool_day": pd,
            "programs": len(by_series[s]),
            "sampled": len(ps),
            "scored": n,
            "median_ratio": med,
            "ratio_n": len(ratios),
            "ratio_min": min(ratios) if ratios else None,
            "ratio_max": max(ratios) if ratios else None,
            "closed_contracts": closed,
            "no_meta": no_meta,
            "two_sided_ct": two_ct,
            "two_sided_pct": (100.0 * two_ct / n) if n else None,
            "cap_day_sampled": cap_day,
            "cap_day_per_market": (cap_day / n) if n else None,
            "share": (sum(shares) / len(shares)) if shares else None,
            "maker_fee": fee, "fee_type": fee_type,
            "structure": structure, "mutually_exclusive": mut_ex,
            "strike_types": sorted(set(st)),
            "category": category,
            "ours": s in OURS,
            "toxic_hint": any(k in s for k in TOXIC_HINT),
            "thin": len(ratios) < THIN_N,
            "instants": 1,
            "passes_ratio": (med is not None and med <= RATIO_MAX),
            "hours_utc": calendar_of(by_series[s], now),
        })
        r = f"{med:6.2f}" if med is not None else "   n/a"
        print(f"  [{si:>3}/{len(ranked)}] {s:28s} ratio {r}  "
              f"pool ${pd:>8,.0f}/d  2s {two_ct}/{n}  {fee:7s} {structure}")

    json.dump({"generated_utc": now.isoformat(),
               "venue_programs": len(progs), "venue_series": len(by_series),
               "venue_pool_day": venue_total,
               "ratio_max": RATIO_MAX, "per_series": per_series, "seed": seed,
               "rows": rows}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT} ({len(rows)} series)")

    passing = [r for r in rows if r["passes_ratio"]]
    print(f"\n{'='*78}\nRATIO FILTER: {len(passing)} of {len(rows)} series with a measurable "
          f"median ratio <= {RATIO_MAX}\n{'='*78}")
    order = sorted(passing, key=lambda r: (r["maker_fee"] != "FREE",
                                           -(r["two_sided_pct"] or 0),
                                           -r["pool_day"]))
    for r in order:
        print(f"  {r['series']:28s} ratio {r['median_ratio']:5.2f} (n={r['ratio_n']})  "
              f"${r['pool_day']:>8,.0f}/d  2s {r['two_sided_pct'] or 0:5.1f}%  "
              f"{r['maker_fee']:7s} {r['structure']:14s} "
              f"{'OURS' if r['ours'] else ''} hrsUTC={r['hours_utc']}")
    return rows


if __name__ == "__main__":
    argv = sys.argv[1:]
    sd = 0
    if "--seed" in argv:
        i = argv.index("--seed")
        sd = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    fl = "--full" in argv
    a = [x for x in argv if x.isdigit()]
    main(int(a[0]) if a else 5, seed=sd, full=fl)
