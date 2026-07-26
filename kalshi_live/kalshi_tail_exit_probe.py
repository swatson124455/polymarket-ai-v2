#!/usr/bin/env python3
"""TAIL-EXIT PROBE — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

ADVERSARIAL LENS: horizon-and-exit. The expansion proposal ranks survivors by the horizon
RATIO and by an "uncompensated tail" in hours. Both are forward-looking arithmetic on the
CURRENTLY-ACTIVE program. Neither measures the thing that actually decides whether the tail
is survivable, which is:

    WHEN A MARKET IS OPEN BUT HAS NO ACTIVE LIQUIDITY PROGRAM, IS THERE A BOOK TO EXIT INTO?

Because that is the exact state the code lands in:
  * select_footprint (maker_kalshi_quoter.py:282-330) iterates PROGRAMS. No program => the
    ticker is not even a candidate. It cannot be quoted, ramped or wound down.
  * STRAND UNWIND (:1083-1110) is the only remaining passive exit, and it is gated on
        sby is None or sbn is None or sby + sbn >= 1.0  ->  `continue`
    i.e. it needs a TWO-SIDED, UNCROSSED book. If the program was what made the book, the
    book dies with the program and the strand unwind silently does nothing.
  * The settlement taker (:974-1005) is armed ONLY by
        close_time < now + SETTLE_UNWIND_MIN (30 min, clamped <= WIND_DOWN_MIN)
    so with a tail of T hours it is unarmed for T-0.5 hours.
  * ladder_pairing paired quantity is excluded from the strand unwind AND from the taker
    TRIGGER (both iterate naked_by), so a MATCHED PAIR has no exit path in the tail at all.

So this probe measures the DARK-BOOK state directly, on the real venue, right now:
  phase A  every OPEN market of each series, and every ACTIVE program on that series
  phase B  classify each open market COVERED (has program) / DARK (open, no program)
  phase C  for a sample of DARK markets, pull the orderbook and evaluate the EXACT strand
           unwind precondition + the taker's execution cost (worst-of-book cross)
  phase D  recurrence: does a DARK market later get a NEW program? Point-in-time data cannot
           answer that directly, so we report the observable proxy — the distribution of
           program windows per series and whether any OPEN market carries a program whose
           end_date is strictly before its own close_time.

WHAT THIS DOES NOT COVER
  * Fill rate, queue position, adverse selection. Reward-side/book-shape only.
  * Settlement toxicity. Needs settled positions we do not have.
  * ONE INSTANT per run. Book shape churns; treat percentages as a snapshot with the stated n.
  * It cannot prove a program will NOT recur. It can only show the state a position would be
    stranded in for as long as the darkness lasts.

Run:  python kalshi_tail_exit_probe.py
Out:  tail_exit_probe.json  (+ stdout report)
"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(HERE, "tail_exit_probe.json")

SPACING_S = 0.32
PAGE_LIMIT = 10000          # 1000 silently truncates — known defect (programs endpoint)
MKT_PAGE_LIMIT = 1000       # /markets REJECTS limit>1000 (400) — measured, cursor-paginated below
PAGES = 8
TICK = 0.01
JOIN_SIZE = 20.0            # deployed join shape, ct/side
_last = [0.0]

# proposed survivors (NEEDS-PROBE tier of the expansion proposal) + our two incumbents
SURVIVORS = [
    "KXNETFLIXTOPVIEWSMOVIE", "KXNETFLIXTOPVIEWSTV", "KXTRUMPENDORSEMENTS", "KXEOWEEK",
    "KXAMSAVO", "KXACTBLUETOP", "KXB200MON", "KXBIGBROTHERELIMINATION", "KXNHSALES",
    "KXMUSKNW", "KXTRUTHSOCIAL", "KXRTX5090MON", "KXFEDMENTION", "KXTRUMPACT",
]
BENCH = ["KXAAAGASD", "KXAAAGASW"]


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path,
                                 headers={"User-Agent": "kalshi-tail-exit-probe/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


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


def fetch_programs(status="active"):
    progs, cur, seen = [], "", set()
    for _ in range(PAGES):
        d = get(f"/incentive_programs?status={status}&limit={PAGE_LIMIT}"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    else:
        if cur:
            print(f"  !! TRUNCATED at {len(progs)} programs — counts are LOWER BOUNDS")
    return progs


def fetch_open_markets(series):
    out, cur, seen = [], "", set()
    for _ in range(PAGES):
        d = get(f"/markets?series_ticker={series}&status=open&limit={MKT_PAGE_LIMIT}"
                + (f"&cursor={cur}" if cur else ""))
        out += d.get("markets") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    return out


def cross_cost(lv, ct):
    """WORST-OF-BOOK cost to BUY `ct` contracts by walking the resting levels of `lv`
    (price, size) best-first. Returns (filled_ct, avg_price) — the settlement taker's real
    execution, not the touch price."""
    got, spend = 0.0, 0.0
    for p, s in sorted(lv, key=lambda r: -r[0]):
        take = min(s, ct - got)
        if take <= 0:
            break
        got += take
        spend += take * p
    return got, (spend / got if got else None)


def main():
    now = datetime.now(timezone.utc)
    series_list = SURVIVORS + BENCH

    print(f"TAIL-EXIT PROBE  {now.isoformat()}  read-only public API\n{'='*90}")
    progs = fetch_programs("active")
    by_series_prog = defaultdict(list)
    prog_by_ticker = {}
    for p in progs:
        t = p.get("market_ticker") or ""
        s = t.split("-")[0]
        by_series_prog[s].append(p)
        prog_by_ticker[t] = p
    print(f"active programs venue-wide: {len(progs)}\n")

    rows = []
    dark_samples = []
    for s in series_list:
        try:
            mkts = fetch_open_markets(s)
        except Exception as e:
            print(f"  {s:26s} market fetch FAILED {e!r}")
            continue
        ps = by_series_prog.get(s, [])
        covered, dark, tails = [], [], []
        for m in mkts:
            t = m.get("ticker")
            p = prog_by_ticker.get(t)
            if p:
                covered.append(m)
                try:
                    tails.append((parse_iso(m["close_time"])
                                  - parse_iso(p["end_date"])).total_seconds() / 3600.0)
                except Exception:
                    pass
            else:
                dark.append(m)
        rows.append({
            "series": s, "open_markets": len(mkts), "active_programs": len(ps),
            "covered": len(covered), "dark": len(dark),
            "dark_pct": (100.0 * len(dark) / len(mkts)) if mkts else None,
            "tail_h_min": min(tails) if tails else None,
            "tail_h_max": max(tails) if tails else None,
        })
        print(f"  {s:26s} open={len(mkts):4d} covered={len(covered):4d} DARK={len(dark):4d} "
              f"({(100.0*len(dark)/len(mkts) if mkts else 0):5.1f}%)  "
              f"tail_h {rows[-1]['tail_h_min']} .. {rows[-1]['tail_h_max']}")

        # --- phase C: what does a DARK book look like? (that is our only exit venue)
        # sample the dark markets nearest to close first: those are the ones a stranded
        # position would actually have to exit through.
        dk = sorted(dark, key=lambda m: m.get("close_time") or "")[:6]
        for m in dk:
            t = m["ticker"]
            try:
                ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
            except Exception:
                continue
            yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
            sby = max((p for p, _ in yl), default=None)
            sbn = max((p for p, _ in nl), default=None)
            # EXACT strand-unwind precondition, quoter :1099-1101
            strand_ok = not (sby is None or sbn is None or sby + sbn >= 1.0)
            # taker execution: to flatten a long-YES of JOIN_SIZE we BUY NO — walk the NO book
            got_n, avg_n = cross_cost(nl, JOIN_SIZE)
            got_y, avg_y = cross_cost(yl, JOIN_SIZE)
            try:
                hrs = (parse_iso(m["close_time"]) - now).total_seconds() / 3600.0
            except Exception:
                hrs = None
            dark_samples.append({
                "series": s, "ticker": t, "h_to_close": hrs,
                "best_yes": sby, "best_no": sbn,
                "spread_ticks": (round((1.0 - (sby + sbn)) / TICK)
                                 if (sby is not None and sbn is not None) else None),
                "strand_unwind_possible": strand_ok,
                "no_depth_ct": sum(sz for _, sz in nl), "yes_depth_ct": sum(sz for _, sz in yl),
                "taker_fill_no_ct": got_n, "taker_avg_no": avg_n,
                "taker_fill_yes_ct": got_y, "taker_avg_yes": avg_y,
            })

    # ---- phase C summary
    print(f"\n{'='*90}\nDARK-BOOK EXIT CONDITIONS  (n={len(dark_samples)} open markets with NO "
          f"active program)\n{'='*90}")
    print(f"{'series':26s} {'h_close':>8} {'byes':>6} {'bno':>6} {'sprd':>5} {'strand?':>8} "
          f"{'noDepth':>8} {'fill20no':>9} {'avgNo':>7}")
    def fmt(v, spec):
        return format(v, spec) if v is not None else "n/a".rjust(len(format(0, spec)))
    for d in dark_samples:
        print("  {:24s} {} {} {} {} {:>8} {:8.0f} {:9.0f} {}".format(
            d["series"], fmt(d["h_to_close"], "8.1f"), fmt(d["best_yes"], "6.2f"),
            fmt(d["best_no"], "6.2f"), fmt(d["spread_ticks"], "5d"),
            str(d["strand_unwind_possible"]), d["no_depth_ct"], d["taker_fill_no_ct"],
            fmt(d["taker_avg_no"], "7.3f")))

    n = len(dark_samples)
    if n:
        ok = sum(1 for d in dark_samples if d["strand_unwind_possible"])
        full = sum(1 for d in dark_samples if d["taker_fill_no_ct"] >= JOIN_SIZE)
        print(f"\n  strand unwind POSSIBLE on {ok}/{n} dark books ({100.0*ok/n:.1f}%)")
        print(f"  taker could fill a {JOIN_SIZE:.0f}ct flatten on {full}/{n} "
              f"({100.0*full/n:.1f}%) — worst-of-book")

    json.dump({"generated": now.isoformat(), "rows": rows, "dark_samples": dark_samples,
               "join_size": JOIN_SIZE,
               "note": "read-only public API; one instant; reward-side/book-shape only"},
              open(OUT, "w"), indent=2)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
