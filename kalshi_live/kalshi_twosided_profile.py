#!/usr/bin/env python3
"""TWO-SIDED DIURNAL PROFILE — sandbox, READ-ONLY, NO KEYS, NO MONEY, NEVER TRADES.

THE QUESTION (canon KALSHI_LIP_RULE_CANON.md §M6): is the two-sided drought DIURNAL?

§M6 measured at 03:14Z that only 8/39 in-allowlist contracts had books reaching Target
Size on BOTH sides; 28/39 had one side COMPLETELY EMPTY. Under rulebook R3 a snapshot
without two-sided liquidity is EXCLUDED and pays NOBODY, so a series whose books only go
two-sided during US waking hours is worth a FRACTION of its headline pool to a bot that
runs 24/7 on a 2-minute cadence.

WHAT THIS MEASURES — the MARKET-LEVEL test only, per R3:
    for each contract with an ACTIVE incentive program, does THE BOOK reach that
    program's own `target_size_fp` on EACH side (REC.qualifying_walk != None)?
Nothing here depends on our orders. Per canon §M2 our 20 ct was marginal to
two-sidedness in 0/304 market-snapshots, so this is environmental, not behavioural.

NO SELECTION-BIAS FILTER. `kalshi_concentration_study.py:133` skips a contract when
either side's book is empty (`if not yl or not nl: continue`), which made §M2's "86.1%
two-sided" a CONDITIONAL rate on a pre-filtered denominator (canon §M6b). This script
counts an empty side as a FAILURE, the way `kalshi_series_scan.py:116` does. Rates here
are UNCONDITIONAL and are NOT comparable to §M1/§M2's.

WHAT ONE RUN CANNOT ESTABLISH:
  * A DIURNAL PROFILE. One reading is one UTC hour. The hour column only becomes a
    profile after the collector has run across the clock (>= 24 distinct hours, and
    ideally several samples per hour to separate hour-of-day from day-of-week and from
    program churn). Run it on a cron/loop and use `--summarize`.
  * FILL RATE / ADVERSE SELECTION. Two-sidedness is a REWARD-side gate only. It says
    nothing about whether the flow that fills you is informed. A series can be 100%
    two-sided and still be a settlement trap (the MENTION family).
  * MAKER FEES. Unverified outside KXTEMP*/KXAAAGASD/KXAAAGASW.
  * OUR CAPTURE. A two-sided book may be two-sided because of a wall of competing
    qualifying size, which lowers our normalised share. Use kalshi_series_scan.py for
    the $/day capture estimate; this answers only "is the snapshot excluded or not".
  * CAUSALITY. Programs churn hourly; a series' contract set is not constant between
    runs, so cross-run deltas mix book behaviour with program turnover. The
    `programs` count is written on every row so churn is visible.

Run:
    python kalshi_twosided_profile.py                 # one reading, default series set
    python kalshi_twosided_profile.py --max 200       # raise the per-series contract cap
    python kalshi_twosided_profile.py --series KXRT,KXPM
    python kalshi_twosided_profile.py --summarize     # bucket every stored reading by UTC hour

Appends one JSON row per (run, series) to kalshi_twosided_profile.jsonl and one
per-contract row to kalshi_twosided_contracts.jsonl. Never touches
concentration_samples.jsonl (FROZEN, md5 e920bf99850279099897a79e8ad78dec).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT_SERIES = os.path.join(HERE, "kalshi_twosided_profile.jsonl")
OUT_CONTRACT = os.path.join(HERE, "kalshi_twosided_contracts.jsonl")

# our live allowlist, verbatim from KALSHI_FREEZE_2026-07-23T0219Z.md:68
ALLOW = ["KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",
         "KXAAAGASD", "KXAAAGASW"]
# canon §M5 candidates (the ones the task names)
CANDIDATES = ["KXINTC", "KXPM", "KXRT", "KXFUNDRAISING", "KXCLAUDE",
              "KXEARNINGSMENTIONLMT", "KXEARNINGSMENTIONAXP"]

SPACING_S = 0.30
MAX_PER_SERIES = int(os.environ.get("TS_MAX_PER_SERIES", 120))
_last = [0.0]


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
    req = urllib.request.Request(PUB + path,
                                 headers={"User-Agent": "kalshi-twosided-profile/1.0"})
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


def side_state(lv, target):
    """('QUAL'|'THIN'|'EMPTY', depth, best_bid) for one side vs Target Size.

    THIN means resting size exists but never cumulates to Target Size walking down
    from the reference, so qualifying_walk clears the set (canon R4) -- the side does
    NOT qualify. EMPTY means no resting size at all. Both fail R3; they are separated
    because they imply different things about whether the series is quotable at all.

    best_bid is recorded because it is the MECHANISM behind most EMPTY sides: once a
    contract is pinned at yes_best_bid = 0.99 the opposing bid would have to sit at
    0.01 (the tick floor) and nobody rests there. Measured 2026-07-23 12:52Z on
    KXAAAGASM-26JUL31: every strike 2.50..4.12 had yes_best_bid 0.99 and an EMPTY no
    side; 4.14 (0.97/0.02) upward was two-sided -- a perfectly contiguous cut. That is
    MONEYNESS, not the clock, and it does not go away at any hour."""
    if not lv:
        return "EMPTY", 0.0, None
    ref = REC.qualifying_walk(lv, target)[0]
    depth = sum(s for _, s in lv)
    best = max(p for p, _ in lv)
    if ref is None:
        return "THIN", depth, best
    return "QUAL", depth, best


def fetch_programs():
    progs, cur = [], ""
    for _ in range(8):
        d = get("/incentive_programs?status=active&limit=1000"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    return progs


def take_reading(series_list, max_per_series):
    ts = datetime.now(timezone.utc)
    run_id = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    progs = fetch_programs()
    by_series = defaultdict(list)
    venue_pool_day = 0.0
    for p in progs:
        d = days_of(p)
        if not d:
            continue
        venue_pool_day += (p["period_reward"] / 10000.0) / d
        by_series[(p.get("market_ticker") or "").split("-")[0]].append(p)

    print(f"# run {run_id}  utc_hour={ts.hour:02d}  "
          f"venue {len(progs)} programs / {len(by_series)} series / "
          f"${venue_pool_day:,.0f}/day pool")
    print(f"# per-series contract cap {max_per_series}; R3 market-level test, "
          f"no empty-book filter\n")

    srows = []
    fc = open(OUT_CONTRACT, "a")
    for s in series_list:
        ps = by_series.get(s) or []
        if not ps:
            print(f"  {s:24s} NO ACTIVE PROGRAMS")
            srows.append({"run": run_id, "utc_hour": ts.hour, "series": s,
                          "programs": 0, "sampled": 0, "two_sided": 0,
                          "two_sided_pct": None, "pool_day": 0.0,
                          "pool_day_two_sided": 0.0, "note": "no_active_programs"})
            continue
        # deterministic order: richest $/day first, so a capped sample is the part we
        # would actually care about rather than an arbitrary slice
        ps = sorted(ps, key=lambda p: -(p["period_reward"] / 10000.0) / days_of(p))
        counts = defaultdict(int)
        n = two = 0
        pool_day = sum((p["period_reward"] / 10000.0) / days_of(p) for p in ps)
        pool_day_two = 0.0
        for p in ps[:max_per_series]:
            t = p["market_ticker"]
            tgt = float(p.get("target_size_fp") or 0)
            if tgt <= 0:
                continue
            try:
                ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
            except (urllib.error.URLError, OSError, ValueError) as e:
                counts["fetch_error"] += 1
                print(f"    ! {t} orderbook fetch failed: {e}")
                continue
            yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
            ys, yd, yb = side_state(yl, tgt)
            ns, nd, nb = side_state(nl, tgt)
            ok = (ys == "QUAL" and ns == "QUAL")
            n += 1
            two += 1 if ok else 0
            d = days_of(p)
            pd_ = (p["period_reward"] / 10000.0) / d
            if ok:
                pool_day_two += pd_
            if ok:
                counts["two_sided"] += 1
            elif ys == "EMPTY" or ns == "EMPTY":
                counts["a_side_empty"] += 1
            else:
                counts["a_side_thin"] += 1
            fc.write(json.dumps({
                "run": run_id, "utc_hour": ts.hour, "series": s, "ticker": t,
                "target": tgt, "yes": ys, "no": ns, "yes_depth": yd, "no_depth": nd,
                "yes_bid": yb, "no_bid": nb,
                "two_sided": ok, "pool_day": pd_, "window_h": d * 24.0}) + "\n")
        pct = (100.0 * two / n) if n else None
        row = {"run": run_id, "utc_hour": ts.hour, "series": s,
               "programs": len(ps), "sampled": n, "two_sided": two,
               "two_sided_pct": pct, "pool_day": pool_day,
               "pool_day_two_sided": pool_day_two,
               "a_side_empty": counts["a_side_empty"],
               "a_side_thin": counts["a_side_thin"],
               "fetch_error": counts["fetch_error"]}
        srows.append(row)
        print(f"  {s:24s} n={n:>3}/{len(ps):<3} two-sided {two:>3} "
              f"({'--' if pct is None else format(pct, '5.1f')}%)  "
              f"empty {counts['a_side_empty']:>3}  thin {counts['a_side_thin']:>3}  "
              f"pool ${pool_day:>8,.1f}/d  earnable ${pool_day_two:>8,.1f}/d")
    fc.close()
    with open(OUT_SERIES, "a") as f:
        for r in srows:
            f.write(json.dumps(r) + "\n")

    tot_n = sum(r["sampled"] for r in srows)
    tot_two = sum(r["two_sided"] for r in srows)
    tot_pool = sum(r["pool_day"] for r in srows)
    tot_earn = sum(r["pool_day_two_sided"] for r in srows)
    print(f"\n  TOTAL  contracts {tot_n}  two-sided {tot_two} "
          f"({100.0 * tot_two / tot_n if tot_n else 0:.1f}%)  "
          f"pool ${tot_pool:,.0f}/d  earnable-right-now ${tot_earn:,.0f}/d "
          f"({100.0 * tot_earn / tot_pool if tot_pool else 0:.1f}%)")
    print("\n  RANK by CURRENT two-sided rate (ties broken by earnable $/day):")
    for i, r in enumerate(sorted([x for x in srows if x["sampled"]],
                                 key=lambda x: (-x["two_sided_pct"],
                                                -x["pool_day_two_sided"])), 1):
        print(f"   {i:>2}. {r['series']:24s} {r['two_sided_pct']:5.1f}%  "
              f"n={r['sampled']:<3} earnable ${r['pool_day_two_sided']:>8,.1f}/d")
    print("\n  ONE READING IS ONE UTC HOUR. This is NOT a diurnal profile until the "
          "collector\n  has run across >=24 distinct hours; see the module docstring "
          "for what it cannot show.")
    return srows


def summarize():
    """Bucket every stored reading by UTC hour. Needs many runs to mean anything."""
    if not os.path.exists(OUT_SERIES):
        raise SystemExit(f"no readings yet: {OUT_SERIES}")
    rows = [json.loads(l) for l in open(OUT_SERIES) if l.strip()]
    runs = sorted({r["run"] for r in rows})
    hours = sorted({r["utc_hour"] for r in rows})
    print(f"{len(rows)} series-readings over {len(runs)} runs, "
          f"{len(hours)} distinct UTC hours: {hours}")
    if len(hours) < 24:
        print(f"!! INCOMPLETE: {24 - len(hours)} UTC hours never sampled. "
              f"No diurnal claim is supportable yet.")
    bys = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        if not r["sampled"]:
            continue
        c = bys[r["series"]][r["utc_hour"]]
        c[0] += r["two_sided"]
        c[1] += r["sampled"]
    hdr = "series".ljust(24) + "".join(f"{h:>5}" for h in hours)
    print("\n" + hdr)
    for s in sorted(bys):
        line = s.ljust(24)
        for h in hours:
            tw, n = bys[s][h]
            line += f"{(100.0 * tw / n):>5.0f}" if n else "    ."
        print(line)
    print("\ncells are two-sided % of contracts sampled in that UTC hour; "
          "'.' = never sampled")


def coverage(series_list, days_back=7):
    """PROGRAM DUTY CYCLE by UTC hour — the prior question to two-sidedness.

    You cannot have a two-sided *drought* in a contract that has no incentive program
    at all: with no program there is no pool, no Target Size, and nothing to earn at
    any book state. This walks every program window (any status) that overlaps the
    last `days_back` days and asks, for each UTC hour, what fraction of those days had
    at least one live program on the series.

    Unlike the two-sided reading this needs NO waiting: program windows are published
    with start_date/end_date, so a full diurnal answer is available from one call.

    READ THE SHAPE, NOT THE LEVEL. A FLAT row means the series simply had a
    multi-day continuous program covering part of the lookback -- its duty % is
    program lifetime, NOT a daily cycle. A HOLED row (zeros at the same hours every
    day) is a real recurring blackout. Measured 2026-07-23: KXAAAGASD is the only
    series in our set with a holed row -- 6 windows in 7 days, each ~12.5-13.2h,
    starting 14:44-15:29Z and ALL ending 03:59Z, so it is dark ~04:00-15:00Z daily.

    ⚠ MUST NOT use `status=active` here. Active returns only programs live at this
    instant (2,283 rows on 2026-07-23T12:42Z), which by construction CANNOT show a
    blackout -- the first version of this function did that and silently dropped
    KXAAAGASD entirely, i.e. it reported "no such series" for exactly the series with
    the blackout. `status=scheduled` returns the historical superset (>=25,000 rows,
    including windows that already ended, e.g. KXAAAGASD-26JUL15). Paginate it.
    """
    want = set(series_list)
    seen, cur = {}, ""
    for _ in range(30):
        d = get("/incentive_programs?status=scheduled&limit=1000"
                + (f"&cursor={cur}" if cur else ""))
        for p in d.get("incentive_programs") or []:
            t = p.get("market_ticker") or ""
            if t.split("-")[0] in want:
                seen[(t, p["start_date"], p["end_date"])] = p
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    lo = now - timedelta(days=days_back)
    bys = defaultdict(list)
    for p in seen.values():
        a = datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
        if b < lo or a > now:
            continue
        bys[p["market_ticker"].split("-")[0]].append((a, b))
    print(f"program duty cycle, last {days_back}d to {now:%Y-%m-%dT%H:%MZ}  "
          f"(digit = 0-9 scaled coverage of that UTC hour; 9 = always live)")
    print("series".ljust(22) + "n_win duty   " + "".join(str(h % 10) for h in range(24)))
    for k in sorted(bys):
        iv = bys[k]
        hit, tot = [0] * 24, [0] * 24
        c = lo
        while c < now:
            n = c + timedelta(hours=1)
            hit[c.hour] += 1 if any(a < n and b > c for a, b in iv) else 0
            tot[c.hour] += 1
            c = n
        duty = sum(hit) / max(sum(tot), 1)
        bar = "".join(str(round(9 * hit[h] / tot[h])) if tot[h] else "." for h in range(24))
        print(f"{k:22s}{len(set(iv)):>5} {100 * duty:5.1f}%  {bar}")
    print("\nflat row = one long program, level is lifetime not a cycle; "
          "holed row = recurring daily blackout")


if __name__ == "__main__":
    if "--summarize" in sys.argv:
        summarize()
    elif "--coverage" in sys.argv:
        sl = ALLOW + CANDIDATES + ["KXAAAGASM"]
        if "--series" in sys.argv:
            sl = [x for x in sys.argv[sys.argv.index("--series") + 1].split(",") if x]
        coverage(sl)
    else:
        sl = ALLOW + CANDIDATES
        if "--series" in sys.argv:
            sl = [x for x in sys.argv[sys.argv.index("--series") + 1].split(",") if x]
        mx = MAX_PER_SERIES
        if "--max" in sys.argv:
            mx = int(sys.argv[sys.argv.index("--max") + 1])
        take_reading(sl, mx)
