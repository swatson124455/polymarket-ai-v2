#!/usr/bin/env python3
"""ADVERSARIAL REFUTATION PROBE — structure-and-risk lens, 2026-07-23.

The survivor qualifier assigned ONE structure verdict per SERIES from a sample of
`events: 1` (see survivor_qualify.json — 27 of 29 rows carry events==1). But the
admission unit in the live quoter is the SERIES:

    maker_kalshi_quoter.select_footprint():
        if SERIES_ALLOW and t.split("-")[0] not in SERIES_ALLOW:  -> drop

i.e. once a series is allowlisted EVERY event of it, forever, is quotable. So a
structure verdict measured on one event does not bind the series.

This probe enumerates ALL open+unopened events of each candidate series from the
PUBLIC API (read-only, no keys) and re-runs the LIVE risk functions
(_strike_of / _is_ladder_event / ladder_pairing) per event, plus the per-event
strike_type / mutually_exclusive fields. It reports, per series, whether the
structure verdict is CONSTANT across events or whether the series is
HETEROGENEOUS — a heterogeneous series cannot be admitted on a one-event verdict.

Read-only. No orders, no keys, no writes outside this file's own JSON output.
"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_quoter import (_strike_of, _is_ladder_event, ladder_pairing,  # noqa: E402
                                 _event_key)

PROD_BASE = "https://external-api.kalshi.com"
SPACING = 0.4
_last = [0.0]


def get(path, params=None):
    if params:
        import urllib.parse
        path = path + "?" + urllib.parse.urlencode(params)
    w = SPACING - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    req = urllib.request.Request(PROD_BASE + path,
                                 headers={"User-Agent": "kalshi-refute-probe/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def all_markets(series):
    """Every market of a series across all statuses. limit=10000 (1000 truncates)."""
    out, cursor = [], None
    for _ in range(20):
        p = {"series_ticker": series, "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        d = get("/trade-api/v2/markets", p)
        ms = d.get("markets") or []
        out.extend(ms)
        cursor = d.get("cursor")
        if not cursor or not ms:
            break
    return out


CANDIDATES = [
    # the 14 NEEDS-PROBE rows, incumbents, and the 4 structural rejects (control)
    "KXNETFLIXTOPVIEWSMOVIE", "KXNETFLIXTOPVIEWSTV", "KXTRUMPENDORSEMENTS",
    "KXEOWEEK", "KXAMSAVO", "KXACTBLUETOP", "KXB200MON",
    "KXBIGBROTHERELIMINATION", "KXNHSALES", "KXMUSKNW", "KXTRUTHSOCIAL",
    "KXRTX5090MON", "KXFEDMENTION", "KXTRUMPACT",
    "KXAAAGASD", "KXAAAGASW",
    "KXAPRPOTUS", "KXTRUMPPHOTO", "KXNBATEAMANNOUNCE", "KXDXYDUD",
]


def main():
    rows = {}
    for s in CANDIDATES:
        try:
            ms = all_markets(s)
        except Exception as e:
            rows[s] = {"error": repr(e)}
            print(f"{s}: ERROR {e!r}")
            continue
        by_ev = defaultdict(list)
        for m in ms:
            by_ev[m.get("event_ticker") or _event_key(m["ticker"])].append(m)
        evs = {}
        for ev, mm in by_ev.items():
            tks = [m["ticker"] for m in mm]
            sts = sorted({m.get("strike_type") for m in mm})
            nets = _is_ladder_event(tks)
            pf = sum(1 for t in tks if _strike_of(t) is None)
            # would ladder_pairing actually pair a +low/-high probe on this event?
            parsed = sorted([(_strike_of(t), t) for t in tks if _strike_of(t) is not None])
            paired = False
            if len(parsed) >= 2:
                lo, hi = parsed[0][1], parsed[-1][1]
                nk = ladder_pairing({lo: 20.0, hi: -20.0})
                paired = (abs(nk.get(lo, 0)) < 1e-9 and abs(nk.get(hi, 0)) < 1e-9)
            evs[ev] = {
                "n": len(mm),
                "strike_types": [x for x in sts if x],
                "statuses": sorted({m.get("status") for m in mm}),
                "code_nets": nets,
                "parse_fail": pf,
                "pairing_would_pair_lo_hi": paired,
                "lo_hi": [parsed[0][1], parsed[-1][1]] if len(parsed) >= 2 else None,
                "lo_title": mm[[m["ticker"] for m in mm].index(parsed[0][1])].get("yes_sub_title")
                            if len(parsed) >= 2 else None,
                "hi_title": mm[[m["ticker"] for m in mm].index(parsed[-1][1])].get("yes_sub_title")
                            if len(parsed) >= 2 else None,
            }
        st_union = sorted({x for e in evs.values() for x in e["strike_types"]})
        nets_vals = {e["code_nets"] for e in evs.values()}
        pair_vals = {e["pairing_would_pair_lo_hi"] for e in evs.values()}
        rows[s] = {
            "events": len(evs),
            "markets": len(ms),
            "strike_type_union": st_union,
            "strike_type_heterogeneous": len(st_union) > 1,
            "code_nets_values": sorted(nets_vals),
            "nets_heterogeneous": len(nets_vals) > 1,
            "pairing_values": sorted(pair_vals),
            "pairing_heterogeneous": len(pair_vals) > 1,
            "per_event": evs,
        }
        print(f"{s:26s} events={len(evs):4d} mkts={len(ms):5d} st={st_union} "
              f"nets={sorted(nets_vals)} pairs={sorted(pair_vals)} "
              f"HET={'YES' if (len(st_union) > 1 or len(nets_vals) > 1 or len(pair_vals) > 1) else 'no'}")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "multievent_structure_refute.json"), "w") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main()
