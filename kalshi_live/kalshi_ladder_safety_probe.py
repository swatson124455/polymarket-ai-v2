#!/usr/bin/env python3
"""LADDER-SAFETY PROBE — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES, NO POSITIONS TOUCHED.

THE QUESTION: for each candidate series, does the LIVE quoter's risk logic NET tickers that are
not additively correlated — and if it does, can I produce the settlement state where the netting
is WRONG rather than merely conservative?

Background. `event_deltas` was patched 2026-07-23 so it only nets an event that
`_is_ladder_event` can PROVE is an additive threshold ladder. But `_is_ladder_event` is a pure
TICKER-STRING test:

    every ticker parses to a float strike, and the strikes are DISTINCT  ->  it is a ladder

It never consults `strike_type` or the event's `mutually_exclusive` flag. So a MUTUALLY-EXCLUSIVE
event, or a BUCKET ('between') event, whose tickers happen to carry distinct numeric strikes
passes the test and gets netted anyway. Both are anti-correlated, not additive.

Two consumers act on that:
  * event_deltas -> event_delta_for -> the throttle. A netted-to-zero event reads FLAT and the
    throttle stands down while two live naked exposures are carried.
  * ladder_pairing -> a long-YES-low + long-NO-high match is declared a FLOORED PAIR ("settlement
    returns >= $1 per matched pair"), and paired quantity is then EXCLUDED from unwind targeting,
    throttle direction, the settle-taker and the STOP offsets. If the event is not a monotone
    'above X' ladder that floor does not exist, and the exclusion removes every de-risking path
    from a position that can settle to ZERO on both legs.

This probe runs the real functions over the real ticker sets and prints the settlement table for
any pair it flags, so the claim is demonstrated rather than asserted.

Run:  python kalshi_ladder_safety_probe.py [series ...]     (default: the qualify shortlist)
Out:  ladder_safety_probe.json
"""
import importlib.util
import json
import os
import sys
from collections import defaultdict

import kalshi_horizon_census as C

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ladder_safety_probe.json")
CENSUS = os.path.join(HERE, "horizon_census.json")
RATIO_CUT = float(os.environ.get("PROBE_RATIO_CUT", 2.0))


def _load_quoter():
    spec = importlib.util.spec_from_file_location(
        "_q", os.path.join(HERE, "maker_kalshi_quoter.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


Q = _load_quoter()


def default_series():
    d = json.load(open(CENSUS))
    return [r["series"] for r in d["rows"]
            if r["median_ratio"] is not None and r["median_ratio"] <= RATIO_CUT]


def main(series_filter=None):
    want = set(series_filter or default_series()) | {"KXAAAGASD", "KXAAAGASW"}
    progs = C.fetch_programs()
    by_series = defaultdict(list)
    for p in progs:
        t = p.get("market_ticker") or ""
        if t and t.split("-")[0] in want:
            by_series[t.split("-")[0]].append(p)

    tickers = [p["market_ticker"] for ps in by_series.values() for p in ps]
    meta = C.fetch_markets_batch(tickers)
    print(f"probe: {len(by_series)} series / {len(tickers)} contracts / meta {len(meta)}\n")

    rows = []
    for s in sorted(by_series):
        ev_of = defaultdict(list)
        for p in by_series[s]:
            t = p["market_ticker"]
            m = meta.get(t) or {}
            ev_of[m.get("event_ticker") or Q._event_key(t)].append(t)

        for ev, ts in sorted(ev_of.items()):
            ts = sorted(ts)
            ms = [meta.get(t) or {} for t in ts]
            st = sorted({(m.get("strike_type") or "?") for m in ms})
            try:
                e = C.get(f"/events/{ev}")
                e = e.get("event") or e
                mut = e.get("mutually_exclusive")
                title = e.get("title")
            except Exception:
                mut, title = None, None

            strikes = {t: Q._strike_of(t) for t in ts}
            nets = Q._is_ladder_event(ts)
            # additive-monotone is the ONLY shape the netting is valid for
            monotone = bool(st) and all(
                x in ("greater", "greater_or_equal", "less", "less_or_equal",
                      "greater_than", "less_than") for x in st)
            valid = monotone and mut is not True
            danger = nets and not valid

            demo = None
            if danger and len(ts) >= 2:
                # build the exact pair ladder_pairing would match: lowest long, highest short
                order = sorted((v, k) for k, v in strikes.items() if v is not None)
                lt, ht = order[0][1], order[-1][1]
                held = {lt: 20, ht: -20}
                naked = Q.ladder_pairing(dict(held))
                evd = Q.event_deltas(dict(held))
                demo = {
                    "held": held,
                    "long_low": lt, "long_low_strike": strikes[lt],
                    "long_low_subtitle": (meta.get(lt) or {}).get("yes_sub_title")
                                         or (meta.get(lt) or {}).get("subtitle"),
                    "short_high": ht, "short_high_strike": strikes[ht],
                    "short_high_subtitle": (meta.get(ht) or {}).get("yes_sub_title")
                                           or (meta.get(ht) or {}).get("subtitle"),
                    "ladder_pairing_naked_remainder": naked,
                    "declared_fully_paired": all(v == 0 for v in naked.values()),
                    "event_deltas": {str(k): v for k, v in evd.items()},
                    "event_reads_flat": all(abs(v) < 1e-9 for v in evd.values()),
                }
            rows.append({
                "series": s, "event": ev, "title": title, "contracts": len(ts),
                "strike_types": st, "mutually_exclusive": mut,
                "code_nets_this_event": nets, "netting_is_valid": valid,
                "DANGEROUS": danger, "strikes": strikes, "demo": demo,
            })
            flag = "  DANGEROUS" if danger else ("  ok-nets" if nets else "  ok-abstains")
            print(f"{s:26s} {ev:32s} n={len(ts):3d} st={','.join(st):24s} "
                  f"me={mut!s:5s} nets={nets!s:5s}{flag}")
            if demo:
                print(f"    pair {demo['long_low']} (+20, {demo['long_low_subtitle']}) "
                      f"/ {demo['short_high']} (-20, {demo['short_high_subtitle']})")
                print(f"    ladder_pairing -> fully paired: {demo['declared_fully_paired']}   "
                      f"event_deltas reads FLAT: {demo['event_reads_flat']}")

    json.dump({"rows": rows}, open(OUT, "w"), indent=1)
    d = [r for r in rows if r["DANGEROUS"]]
    print(f"\nwrote {OUT}")
    print(f"\nDANGEROUS events: {len(d)} of {len(rows)}  "
          f"series: {sorted({r['series'] for r in d})}")
    return rows


if __name__ == "__main__":
    main([a for a in sys.argv[1:]] or None)
